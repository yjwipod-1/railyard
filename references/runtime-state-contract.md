---
name: runtime-state-contract
description: Executable runtime state and event sidecar contract for append, replay, integrity, lineage, runtime-artifact visibility, and required-gate invariants
type: contract
version: 0.9.0
supersedes: 0.8.2
governing_contract:
  artifact_id: runtime-architecture
  artifact_kind: contract
  artifact_version: 0.8.0
  locator: references/runtime-architecture.md
frozen_references:
  - artifact_id: knowledge-contract
    artifact_kind: contract
    artifact_version: 0.8.0
    locator: references/knowledge-contract.md
  - artifact_id: runtime-artifact-visibility-contract
    artifact_kind: contract
    artifact_version: 1.0.0
    locator: references/runtime-artifact-visibility-contract.md
risk_level: high
validator_required: true
visibility: public
conformance_fixture: examples/runtime_state_contract_fixtures/conformance.json
conformance_command: python scripts/test_runtime_state_contract.py
---

# Runtime State and Event Sidecar Contract

This contract defines the storage-neutral append, integrity, replay, projection,
and lineage behavior for runtime state. The append-only event stream is the sole
authority. Projections, snapshots, checkpoints, indexes, and exports are derived,
digest-bound, discardable artifacts. This contract does not implement storage,
an adapter, a scheduler, retry execution, a gate engine, a Validator mesh, or a
publish gate.

The frozen Runtime Architecture Contract v0.8.0 is the governing source for the
runtime event taxonomy and transitions. The frozen Knowledge Contract v0.8.0 is
the governing source for ArtifactRef, provenance, visibility, evidence, and
relationship preservation.

## 1. Authority and separation

Runtime state and development workflow state are separate authority domains.
Runtime components MUST NOT read or write the workflow database as runtime state.
Lifecycle helpers MUST NOT write runtime state. Cross-domain references use typed
ArtifactRef objects only.

Every runtime state change MUST be represented by an appended RuntimeEvent.
Projections and snapshots MUST NOT be patched or upserted as mutable truth. A
derived artifact that does not match replay MUST be discarded and rebuilt.

## 2. Append protocol

### 2.1 AppendRequest

An AppendRequest contains no store-assigned fields.

| Field | Type | Required | Rule |
|---|---|---|---|
| `run_id` | string | Yes | Non-empty owning run identity. |
| `event_type` | string | Yes | Exactly one event type from Section 5. |
| `payload` | object | Yes | Must satisfy the named schema for `event_type`. |
| `causation_id` | string | Conditional | Exactly one of this field or `causation_chain`. |
| `causation_chain` | array of string | Conditional | Root event uses an empty array. |
| `actor_role` | string | Yes | `runner`, `architect`, `validator`, `planner`, or `human`. |
| `actor_identity` | string | Yes | Non-empty event producer identity. |
| `trigger_artifact` | ArtifactRef | Yes | Typed triggering artifact. |
| `reason` | string | Yes | Non-empty factual reason. |
| `recommended_action` | string | Yes | `none`, `retry`, `resume`, `intervention_required`, `more_evidence`, or `escalate`. |
| `expected_stream_head` | object | Yes | Exact expected `{event_order, content_digest}`; `event_order` is a non-negative integer and is not a boolean. |
| `client_event_id` | string | Yes | Globally unique client idempotency key, resolved by a global idempotency index before per-run head evaluation. |
| `prev_event_digest` | string | Yes | Digest of the immediate predecessor. |

The empty stream head is exactly `{event_order: 0, content_digest:
sha256:0000000000000000000000000000000000000000000000000000000000000000}`.
Its `prev_event_digest` is the same zero digest:

```text
sha256:0000000000000000000000000000000000000000000000000000000000000000
```

### 2.2 Mandatory append decision order

The store MUST process an AppendRequest in this exact order. The global
`client_event_id` lookup and the selected run's append transaction MUST be
serialized so two concurrent requests cannot both pass the idempotency check.

1. Look up `client_event_id` in the global idempotency index before validating
   request fields or evaluating a stream head or chain link. The stored index
   entry contains the canonical complete request, stored event identity, and
   original receipt bytes.
2. If it exists, compare the complete AppendRequest using the canonical
   serialization in Section 3.
   - An exact duplicate returns the original stored receipt byte-for-byte. No
     event is appended. Its original expected head may now be stale; that does
     not change the result.
   - A divergent duplicate is rejected as `divergent_duplicate`. The existing
     event and receipt are unchanged.
3. For a new `client_event_id`, validate enough envelope shape to select the run,
   then compare `expected_stream_head` with the current run head. A mismatch is
   rejected as `stale_head` and returns an exact rejection object
   `{code, current_stream_head, last_stored_receipt}`. `code` is `stale_head`;
   `current_stream_head` is the exact current head; and
   `last_stored_receipt` is the latest receipt or `null` for an empty stream.
4. Compare `prev_event_digest` with the current head digest. A mismatch is
   rejected as `hash_chain_link`.
5. Validate the exact request shape and typed field domains, payload schema,
   causation, transition invariants, and reducer preconditions. Execute the
   reducer before any event or receipt is stored; a reducer rejection leaves the
   stream, projection, and idempotency index unchanged.
6. Assign store fields, store the complete prior projection as `prior_state` and
   the reducer result as `next_state`, compute the event digest,
   commit the event and receipt atomically, and return that receipt.

The ordering is normative. Implementations MUST NOT perform request validation,
the stale-head check, or the chain-link check before exact retry detection.
A globally duplicated `client_event_id` whose complete request differs,
including one whose `run_id` differs, is a `divergent_duplicate`.

### 2.3 Stored RuntimeEvent

A stored RuntimeEvent is an exact object containing every field of the complete
AppendRequest plus these required store-assigned fields and no others:

| Field | Type | Rule |
|---|---|---|
| `event_id` | string | Globally unique. |
| `event_order` | integer | Positive, strictly increasing, and gap-free within the run. |
| `occurred_at` | string | ISO 8601 timestamp with timezone. |
| `schema_version` | string | `0.9.0`. |
| `prior_state` | object | Projection immediately before this event. |
| `next_state` | object | Reducer output after this event. |
| `content_digest` | string | SHA-256 of the event excluding this field. |

The store computes `prior_state` and `next_state`; clients MUST NOT supply them.
The event digest preimage is the complete stored RuntimeEvent after those values
and all other store-assigned fields are fixed, with only `content_digest`
excluded.

### 2.4 Mandatory AppendReceipt

Every successful append and exact retry MUST return an AppendReceipt with all of
these required fields. None is optional.

| Field | Type | Rule |
|---|---|---|
| `event_id` | string | Stored event identity. |
| `event_order` | integer | Stored event order. |
| `stored_content_digest` | string | Stored event digest. |
| `new_stream_head` | object | Exact `{event_order, content_digest}` after append. |
| `signed_receipt` | object | Cryptographically verifiable binding of run id and head. |

`signed_receipt` MUST contain:

| Field | Type | Rule |
|---|---|---|
| `algorithm` | string | Registered algorithm identifier. |
| `key_id` | string | Identifies verification material. |
| `signed_payload` | object | Exactly `{run_id, event_order, content_digest}`. |
| `signature` | string | Signature or MAC over canonical `signed_payload` bytes; encoding is fixed by the registered algorithm profile. |

Conforming OSS implementations MUST provide at least one open-source-verifiable
profile. The executable conformance profile uses HMAC-SHA256 with independently
held verification material. Its registered values are exactly
`algorithm=HMAC-SHA256` and `key_id=conformance-key-1`; its signature encoding is
exactly 64 lowercase hexadecimal characters containing the 32-byte HMAC result.
The verifier selects the
trusted key by `key_id`; it MUST reject unknown algorithms and unknown key ids
instead of interpreting receipt-provided verification material. Ed25519 is also conforming. Provider-defined receipt
formats are allowed only when they preserve every field and define a deterministic
verification procedure.

Clients MUST persist the signed receipt outside the mutable event store as a
trusted head. Verification MUST validate the cryptographic attestation and the
binding between signed payload, expected run context, receipt head, top-level
event order, and stored digest. The verifier MUST receive the expected `run_id`
from deterministic caller context; it MUST NOT infer the expected run solely from
the untrusted receipt.

Receipt verification MUST reject unless all of these checks pass:

1. AppendReceipt, `signed_receipt`, `signed_payload`, and `new_stream_head`
   contain exactly their required fields, with no missing or extra fields.
   String fields are non-empty strings, event orders are positive integers (a
   boolean is not an integer), and digests match `sha256:` plus 64 lowercase hex
   characters.
2. `algorithm` is registered and `key_id` resolves to independently trusted
   verification material before the signature or MAC is accepted.
3. The signature or MAC verifies over canonical `signed_payload` bytes.
4. `signed_payload.run_id` equals the caller-supplied expected `run_id`.
5. `signed_payload.event_order` equals `new_stream_head.event_order`.
6. `signed_payload.content_digest` equals `new_stream_head.content_digest`.
7. Top-level `event_order` equals `new_stream_head.event_order`.
8. Top-level `stored_content_digest` equals
   `new_stream_head.content_digest`.

An inconsistency in any one field invalidates the complete receipt even when the
signature over an unchanged signed payload remains valid.
Tail completeness is verified by comparing the current stream head to the latest
trusted signed receipt only after all receipt checks pass in the expected run
context.

## 3. Canonical serialization and digests

All event, projection, snapshot, checkpoint, export, request-comparison, and
receipt-signature preimages use RFC 8785, JSON Canonicalization Scheme (JCS).
This contract profile accepts I-JSON values containing objects, arrays, strings,
null, booleans, and integers in the interoperable range `-(2^53)+1` through
`(2^53)-1`. Floating-point values are outside this contract profile. Object keys
are strings. All strings MUST contain valid Unicode scalar values; lone UTF-16
surrogates are rejected. UTF-8 encoding is mandatory.

Object property names MUST be sorted by their unsigned UTF-16 code units as
specified by RFC 8785, not by Unicode scalar value, UTF-8 byte order, locale, or
the host language's default string ordering. This distinction is observable for
non-BMP keys. For example, U+10000 sorts before U+E000 because its leading UTF-16
code unit is D800, which sorts before E000. String escaping follows RFC 8785:
control characters and JSON metacharacters use their required JSON escapes and
all other valid scalar values are emitted as UTF-8.

JCS provides injective typed serialization for this profile. Implementations MUST
NOT use raw `key:value` concatenation, delimiter joining, locale ordering, or
whitespace-sensitive serialization.

The digest is lowercase `sha256:<hex>` over canonical UTF-8 bytes. Every digest
preimage excludes only the fields explicitly listed below; no implementation may
silently omit nulls, empty arrays or objects, false, zero, or presentation fields
that are not listed.

Fixed vectors:

| Value | Canonical UTF-8 | SHA-256 |
|---|---|---|
| `{}` | `{}` | `sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a` |
| Typed nested vector from the fixture | `{"array":[null,true,false,0,-7,42],"delimiter_string":"a:b,c{}[]","nested":{"a":[1,{"k":"v"}],"z":null}}` | `sha256:0b8cd4fc84058be18af361dd70b65e9d44e971cd26dede99188690d11323a816` |
| Escaping and key-order vector from the fixture | JCS bytes recorded in the fixture | `sha256:dbf612d31a596d6c4651103b341aa2c84214ea296d3f5b79197bc14dde18bde7` |
| U+10000 and U+E000 object-key vector | U+10000 key first; exact UTF-8 bytes recorded in the fixture | `sha256:188d67278618861de0e2a3dc1f8a3fd25b5a84c186cd332197b2ce466134d180` |

The complete machine-readable values and expected bytes are in
`examples/runtime_state_contract_fixtures/conformance.json`.

### 3.1 Digest preimages

| Subject | Complete preimage rule | Excluded fields |
|---|---|---|
| AppendRequest equality | Complete exact-shape AppendRequest. This is a canonical-byte equality comparison, not a digest comparison. | none |
| RuntimeEvent | Complete stored RuntimeEvent, including the complete AppendRequest, all store-assigned fields, `prior_state`, and `next_state`. | `content_digest` |
| Signed receipt payload | Exact `{run_id, event_order, content_digest}` object. | none |
| RunProjection | Complete RunProjection containing stable replay state and presentation metadata. | `projection_digest`, `projection_id`, `derived_at` |
| Snapshot | `snapshot_digest` |
| Checkpoint | `content_digest` |
| Export | `content_digest` |

`projection_id` and `derived_at` are presentation metadata, not replay state.
These two fields and `projection_digest` itself MUST NOT affect a projection
digest. Every
other projection-envelope field, including run identity, lineage, status, graph,
stage states, gate decisions, artifact references, latest event identity/order,
event count, event range, projection type, and replayed state, is included when
present in the exact projection schema. Replaying the same ordered events at
different wall-clock times or with different generated projection identifiers
MUST produce the same canonical preimage and projection digest.

## 4. Hash-chain and tamper threat model

Each event digest includes `prev_event_digest`. The first event links to the zero
digest. Later events link to the prior event's stored digest. On read, a verifier
recomputes each event digest, verifies gap-free order, and verifies every link.

Unkeyed SHA-256 digests detect accidental corruption and unsophisticated mutation.
They do not establish an external trust anchor. An attacker able to rewrite the
store can rewrite a suffix or the full chain and recompute every unkeyed digest.
Hash-chain verification alone MUST NOT be claimed to detect that attack.

A trusted signed head is required to detect malicious tail truncation, divergent
suffix replacement, or full-chain rewriting. Verification against a trusted head
MUST fail when current head order or digest differs. Without a trusted signed head,
stream completeness and resistance to malicious recomputation are unprovable.

## 5. Frozen event taxonomy and payload schemas

The schema names below are exactly equal to the frozen Runtime Architecture event
taxonomy. Aliases such as `stage.started`, `gate.passed`, `retry.requested`, or
`intervention.recorded` are not conforming event types.

| Named payload schema | Required payload fields |
|---|---|
| `run.created` | `run_provenance`, `trigger`, `executor_identity`, `run_ordinal`, `created_at`, `stage_graph`, `visibility_context`; conditional `lineage` for every child run |
| `run.started` | `started_at`, `executor_identity` |
| `run.stage.started` | `stage_id`, `started_at`, `entry_evidence` |
| `run.stage.completed` | `stage_id`, `completed_at`, `gate_decisions`, `artifacts_produced` |
| `run.stage.failed` | `stage_id`, `failed_at`, `error`, `failure_category`, `failure_is_transient`, `failure_is_deterministic`, `artifacts_produced_before_failure`, `retry_eligible` |
| `run.stage.skipped` | `stage_id`, `skipped_at`, `authorized_by`, `reason`, `authorizing_intervention_id` |
| `run.gate.evaluated` | `stage_id`, `gate_id`, `decision_id`, `outcome`, `execution_mode`, `evaluated_at`, `evaluated_by`, `evidence`; conditional `degradation_note`; conditional `reevaluates_decision_id` for re-evaluation |
| `run.gate.blocked` | `stage_id`, `gate_id`, `decision_id`, `outcome`, `execution_mode`, `evaluated_at`, `evaluated_by`, `evidence`, `blocked_reason`, `required_evidence`; conditional `degradation_note`; conditional `reevaluates_decision_id` for re-evaluation |
| `run.gate.overridden` | `stage_id`, `gate_id`, `new_decision_id`, `original_decision_id`, `outcome`, `execution_mode`, `evaluated_at`, `evaluated_by`, `evidence`, `override_reason`, `authorized_by`, `authorized_at`, `authorizing_intervention_id`; conditional `degradation_note` |
| `run.retry.initiated` | `new_run_id`, `lineage`, `retry_strategy`, `current_retry_count`, `max_retries`, `failure_category`, `authorized_by`, `authorized_at`; conditional `failure_is_transient` and `failure_is_deterministic` when `authorized_by=system` |
| `run.resumed` | `new_run_id`, `lineage`, `checkpoint_event_order`, `recovery_action`, `authorized_by`, `authorized_at` |
| `run.redesign` | `new_run_id`, `lineage`, `revised_stage_graph`, `authorized_by`, `authorized_at` |
| `run.intervention` | `intervention_id`, `intervention_type`, `authorized_by`, `reason`, `evidence` |
| `run.terminated` | `terminated_at`, `terminated_by`, `termination_reason`, `from_status`, `terminal_status` |
| `run.completed` | `completed_at`, `terminal_stages_completed`, `final_projection_digest`, `total_event_count` |
| `run.failed` | `failed_at`, `failed_stage_id`, `error`, `failure_category`, `failure_is_transient`, `failure_is_deterministic`, `retry_eligible` |
| `run.blocked` | `blocked_at`, `blocked_reason`, `resolution_paths`, `required_evidence` |
| `run.interrupted` | `interrupted_at`, `last_event_order`, `interruption_cause`, `checkpoint_available` |

Every field used by a reducer is present in its named payload schema. ArtifactRef,
StageGraph, gate decision, error, provenance, and lineage values remain typed
objects; they MUST NOT be opaque strings.

All payloads use exact object shapes. Strings are non-empty; timestamps are
timezone-bearing ISO 8601 values; counts and orders are integers in their stated
bounds and reject booleans; digests use the lowercase SHA-256 form; arrays retain
their declared element type. Trigger, execution mode, outcome, retry strategy,
recovery action, intervention type, terminal status, interruption cause, and
resolution path values are closed enums. `degradation_note` is required for a
degraded execution mode and forbidden for `full` mode.
Failure category is exactly one lifecycle category: `permission_denied`,
`command_failed`, `sandbox_boundary`, `authorization_required`,
`environment_issue`, or `unresolved_dependency`.

### 5.1 StageGraph and Gate declarations

A StageGraph has exactly `graph_id`, `stages`, `edges`, `entry_stages`, and
`terminal_stages`. Stage identities are unique; every edge endpoint, entry, and
terminal identity resolves to a declared stage; and the directed graph is
acyclic. Every genesis stage has status `pending`. A Gate preserves `gate_id`,
`gate_type`, `required`, `failure_behavior`, and conditional
`allow_gate_override`. A gate with `gate_type=validator` additionally requires
`contract_ref` as a complete typed ArtifactRef. A missing, scalar, or unresolved
validator `contract_ref` rejects `run.created` and `run.redesign`; the reducer
MUST NOT infer a contract from ambient workflow state.

### 5.2 run.created

`run.created` is the only genesis event. Its `stage_graph` is a complete frozen
StageGraph declaration, and its `run_provenance` is complete typed provenance.
The payload MUST contain a `visibility_context` per
`references/runtime-artifact-visibility-contract.md` Sec.2, recording the
trigger, policy, and governing-contract visibility inputs and a resolved run
visibility even when no runtime artifacts have been produced. The reducer
initializes status `pending`, all declared stages as `pending`, and the run's
immutable provenance, trigger, executor identity, ordinal, creation time, graph,
visibility context, and optional lineage. It does not start execution.

A root run omits `lineage`. Every child run includes complete lineage in its own
`run.created` payload. Reducers MUST NOT read an undeclared graph, provenance, or
lineage record from another store.

### 5.2.1 RuntimeArtifact visibility

Every runtime artifact produced or consumed during execution MUST carry the
visibility shapes defined by `references/runtime-artifact-visibility-contract.md`:

- `RuntimeArtifact` (Sec.1.1): `artifact_ref`, `origin_run`, optional
  `origin_stage`, `produced_by`, `source_artifacts`, `visibility`, and
  `visibility_resolution`.
- `VisibilityContributor` (Sec.1.2): `contributor_id`, `contributor_kind`,
  `contributor_ref`, `asserted_visibility`, `authority`, and
  `classification_evidence`.
- `VisibilityResolution` (Sec.1.3): `resolution_id`, `resolved_at`,
  `contributors`, `resolution_rule` (exactly `most_restrictive`),
  `resolved_visibility`, and `resolution_audit`.

The most-restrictive resolution `restricted > project > public` is the only
permitted resolution rule. No runtime component may downgrade, broaden, or
reinterpret a recorded visibility value. Absence of a restricted marker is
never evidence of public visibility.

The following stable failure codes apply:

| Code | Description |
|---|---|
| `visibility_contributor_missing` | A required contributor is absent from the resolution set. |
| `visibility_contributor_conflict` | Two contributors assert different values for the same identity. |
| `visibility_invalid_value` | An asserted visibility value is not one of `public`, `project`, `restricted`. |
| `visibility_no_evidence` | A contributor has an empty or missing `classification_evidence` array. |
| `visibility_downgrade` | Artifact or run visibility is less restrictive than its resolved visibility. |
| `visibility_omitted_restricted` | A restricted contributor existed but was not included in the resolution. |
| `visibility_context_missing` | A `run.created` event does not contain the required `visibility_context`. |

### 5.3 run.started

`run.started` is distinct from genesis. It requires an existing `pending` run and
changes the run to `active`, recording `started_at`. Stage entry then uses
`run.stage.started`; stage entry MUST NOT serve as the pending-to-active run
transition.

## 6. State and lineage

Run statuses are `pending`, `active`, `completed`, `failed`, `blocked`, and
`interrupted`. `completed`, `failed`, and `blocked` are terminal for the original
run. `interrupted` is non-terminal state evidence that can authorize a new resume
run; it never becomes `active` on the same run.

| Prior | Next | Event |
|---|---|---|
| none | pending | `run.created` |
| pending | active | `run.started` |
| active | completed | `run.completed` |
| active | failed | `run.failed` or `run.stage.failed` |
| active | blocked | `run.blocked` |
| active | interrupted | `run.interrupted` |
| pending, active, or interrupted | failed or blocked | `run.terminated`, exactly as declared by `terminal_status` |
| failed | failed | `run.redesign` child action; original remains failed |
| blocked | failed | `run.redesign` child action terminates original as failed |

Child creation does not change the parent projection. A child has a new `run_id`,
new event order beginning at 1, a new hash chain, and lineage in child genesis.

### 6.1 RunLineage

| Field | Type | Required | Rule |
|---|---|---|---|
| `parent_run_id` | string | Yes | Immediate parent identity. |
| `lineage_kind` | string | Yes | `retry`, `resume`, `more_evidence`, or `redesign`. |
| `lineage_reason` | string | Yes | Non-empty factual reason. |
| `parent_status` | string | Yes | Parent projection status at the boundary. |
| `parent_boundary_event_id` | string | Yes | Event establishing the lineage boundary. |
| `parent_boundary_event_type` | string | Yes | Exact boundary event type. |
| `parent_boundary_event_order` | integer | Yes | Positive parent-local event order. |

The boundary reference is intentionally valid for both terminal events and the
non-terminal `run.interrupted` event used by resume.

| lineage_kind | parent_status | parent_boundary_event_type | Additional boundary constraint |
|---|---|---|---|
| `retry` | `failed` | `run.failed` | none |
| `retry` | `failed` | `run.stage.failed` | none |
| `retry` | `failed` | `run.terminated` | boundary payload has `terminal_status=failed` |
| `resume` | `interrupted` | `run.interrupted` | interruption remains non-terminal evidence |
| `more_evidence` | `blocked` | `run.blocked` | none |
| `more_evidence` | `blocked` | `run.terminated` | boundary payload has `terminal_status=blocked` |
| `redesign` | `failed` | `run.failed` | none |
| `redesign` | `failed` | `run.stage.failed` | none |
| `redesign` | `failed` | `run.terminated` | boundary payload has `terminal_status=failed` |
| `redesign` | `blocked` | `run.blocked` | none |
| `redesign` | `blocked` | `run.terminated` | boundary payload has `terminal_status=blocked` |

`run.retry.initiated`, `run.resumed`, and `run.redesign` are audit events on the
parent. The child's own `run.created` payload repeats the complete RunLineage.
Each parent action MUST bind the lineage parent id and status to the current
projection and its boundary id, type, and order to the latest stored event. The
boundary type and, for `run.terminated`, its declared terminal status MUST match
one row above. The new child id is non-empty and differs from the parent. Retry
count is between 1 and `max_retries`, `max_retries` is at most 3, and failure
category matches the parent. A system-authorized retry is accepted only when
`retry_strategy=full`, `failure_category=command_failed`,
`failure_is_transient=true`, `failure_is_deterministic=true`, and
`current_retry_count < max_retries`. Both failure flags are required for system
authorization, forbidden for Architect or Human authorization, and MUST equal
the immutable classification recorded by the parent's `run.failed` or
`run.stage.failed` boundary; a parent terminated without that classification is
not eligible for system auto-retry. Resume
requires an available checkpoint at or before the boundary. Redesign requires a
complete valid revised StageGraph. Retry and resume preserve the parent status.
Redesign records the child action and deterministically leaves the original run
`failed`, using `authorized_at` as its termination time and
`lineage.lineage_reason` as its termination reason; a previously failed parent
remains failed and a blocked parent transitions to failed in the same reducer.

## 7. Deterministic reducer

The reducer is a pure function of `(prior_projection, RuntimeEvent)` and produces
one next projection. It MUST NOT read wall-clock time, random identifiers,
mutable workflow state, or undeclared graph/provenance/lineage data. Stored
`occurred_at` and payload timestamps are event inputs and may be copied.

The event schema set and reducer row set MUST be exactly equal. The machine-
readable `event_schemas` and `reducers` maps in the conformance fixture are the
executable representation of this table.

| Event | Allowed prior | Reducer effect |
|---|---|---|
| `run.created` | no projection | Initialize `pending` run from payload. Copy `visibility_context` immutably into the projection. |
| `run.started` | `pending` | Set run `active`; record start fields. |
| `run.stage.started` | active run, pending stage | Set stage active from payload. |
| `run.stage.completed` | active run and stage, gates satisfied | Set stage completed. Build full RuntimeArtifact objects from the payload `artifacts_produced` array. Each RuntimeArtifact MUST have `origin_run` equal to the event's `run_id` and `origin_stage` equal to the payload `stage_id`. Append to projection `runtime_artifacts` and recompute `artifact_refs` and `resolved_run_visibility`. |
| `run.stage.failed` | active run and stage | Set stage and run failed. Build full RuntimeArtifact objects from the payload `artifacts_produced_before_failure` array using the same origin rules as `run.stage.completed`. Append to projection `runtime_artifacts` and recompute `artifact_refs` and `resolved_run_visibility`. |
| `run.stage.skipped` | active run, optional pending stage | Set optional stage skipped. |
| `run.gate.evaluated` | active stage | Append immutable decision with outcome and evidence; a re-evaluation references and preserves the prior decision. |
| `run.gate.blocked` | active stage | Append blocked decision; a re-evaluation references and preserves the prior decision; no implicit run transition. |
| `run.gate.overridden` | optional gate with contract permission | Append new decision; preserve original. |
| `run.retry.initiated` | failed parent | Audit child creation; parent unchanged. |
| `run.resumed` | interrupted parent | Audit child creation; parent unchanged. |
| `run.redesign` | failed or blocked parent | Validate the revised graph, audit child creation, and leave the original parent failed. |
| `run.intervention` | active, blocked, or interrupted | Append intervention audit; state change needs its own event. |
| `run.terminated` | pending, active, or interrupted | Set declared `failed` or `blocked` terminal status. |
| `run.completed` | active; terminal stages complete | Set completed. |
| `run.failed` | active | Set failed. |
| `run.blocked` | active | Set blocked. |
| `run.interrupted` | active | Set interrupted. |

The following payload-dependent reducer requirements are normative:

- `run.completed` verifies that the payload names exactly every terminal stage,
  every terminal stage is `completed`, every required stage is `completed`, and
  every required gate has an immutable `pass` decision with evidence. Its
  `total_event_count` equals the resulting event count. It independently
  recomputes `final_projection_digest` from the stable replay preimage containing
  run id, completed status, provenance, trigger, executor, ordinal, StageGraph,
  lineage, complete stage states, artifact references, completion time, terminal
  stage list, and resulting event count. The asserted digest itself and volatile
  projection metadata are excluded. Only an exact match sets the run completed.
- `run.stage.failed` requires the referenced stage to exist and be `active`. It
  records failure time, structured error, category, retry eligibility, and
  transient/deterministic classification plus partial artifacts on the stage,
  and sets both stage and run to `failed`. `run.failed` records the same
  classification on its named active stage and the run projection. These
  immutable fields are the source of truth for system auto-retry eligibility.
- `run.terminated` requires `from_status` to equal the prior projection status,
  `terminated_by` to be `architect` or `human`, and `terminal_status` to be
  exactly `failed` or `blocked`. It records termination evidence and applies the
  declared terminal status. It cannot manufacture `completed`.
- `run.stage.completed` requires every declared gate to have a decision, exact
  decision references in its payload, required-gate pass evidence, and an active
  target stage. It records completion artifacts in both stage and run views.
- Gate evaluation, block, and override events require an active referenced stage
  and declared gate. Every stored decision preserves execution mode, evaluator,
  evaluation time, evidence, and conditional degradation note. A block has
  outcome `blocked`. An override has outcome `pass` and requires an optional gate
  with explicit contract permission, a preserved original decision, and a
  matching `override_gate` intervention whose authority and reason also match.
- A first gate evaluation omits `reevaluates_decision_id`. A re-evaluation uses
  `run.gate.evaluated` or `run.gate.blocked`, requires
  `reevaluates_decision_id` to equal the current decision for that gate, requires
  a globally new `decision_id`, and requires at least one typed evidence
  reference not present on the referenced decision. The reducer appends the new
  immutable decision to history and only then advances the current-decision
  pointer. It never deletes, replaces, or mutates the referenced decision. A
  required gate becomes satisfiable only when its current appended decision is
  `pass` with non-empty evidence; re-evaluation cannot manufacture or substitute
  pass and does not relax stage-completion checks.
- Optional stage skip requires a pending optional stage and a matching Human
  `skip_stage` intervention. Required stages remain unskippable.
- `run.failed` requires the named stage to be active and marks that stage failed
  as well as the run. `run.blocked` and `run.interrupted` copy their complete
  payload evidence into the projection; interruption order must match the
  preceding gap-free event count.

### 7.1 Reducer visibility invariants

The reducer MUST enforce these visibility invariants on every artifact-producing
event:

1. `origin_run` on every RuntimeArtifact MUST equal the event's `run_id`.
2. `origin_stage` on every RuntimeArtifact MUST equal the payload `stage_id`
   for `run.stage.completed` and `run.stage.failed`.
3. Every `source_artifact` referenced in `source_artifacts` MUST be represented
   by a matching `contributor_ref` in at least one `visibility_resolution.contributors`
   entry with `contributor_kind: source_artifact`.
4. Every RuntimeArtifact's `visibility` MUST equal its
   `visibility_resolution.resolved_visibility`.
5. Adding an artifact with stricter visibility (project vs a public run, or
   restricted vs a project run) MUST tighten `resolved_run_visibility` to the
   stricter value. The initial `visibility_context` and prior artifacts MUST NOT
   be rewritten.
6. The `resolution_audit.contributor_count` MUST equal the number of
   `visibility_resolution.contributors`.
7. The `resolution_audit` counts (`restricted_count`, `project_count`,
   `public_count`) MUST match the actual asserted visibility distribution in
   `contributors`.

Violation of any invariant MUST cause the reducer to reject the event. The
stream, projection, and idempotency index remain unchanged on rejection.

## 8. Required-stage and required-gate invariants

A stage with `required: true` MUST NOT be skipped, overridden to completion, or
recast as pass. `run.stage.completed` is rejected unless every required gate has
an immutable decision with `outcome=pass` and non-empty evidence.

A gate with `required: true` MUST be evaluated and MUST NOT be skipped or
overridden. A `pass` decision requires non-empty evidence. Transport or storage
degradation MUST NOT change the outcome. `fail`, `blocked`, and `inconclusive`
MUST NOT be rewritten as pass. Re-evaluation with new evidence creates a new
decision with a new identity, references the current prior decision through
`reevaluates_decision_id`, and preserves every prior decision and referenced
Validation Report. Direct replacement of the current decision or mutation of
decision history is non-conforming.

When required correctness cannot be established, the run blocks or fails.
Permitted resolution is more evidence in a new child run, contract redesign in a
new child run, or termination. No role, including Human, can override these
invariants.

## 9. Projection, snapshot, and checkpoint

### 9.1 RunProjection visibility surface

A RunProjection preserves run identity, lineage, status, graph, stage states,
gate decisions, artifact references, visibility state, latest event
identity/order, event count, and projection digest. The visibility surface
consists of four immutable or derived fields:

| Field | Type | Source | Mutability |
|---|---|---|---|
| `visibility_context` | object | Copied from `run.created` payload | Immutable after initialization |
| `runtime_artifacts` | array of RuntimeArtifact | Appended by `run.stage.completed` and `run.stage.failed` in event order | Append-only |
| `artifact_refs` | array of ArtifactRef | Derived from `runtime_artifacts[].artifact_ref` as identity-only ArtifactRef values | Derived; recomputed on every replay |
| `resolved_run_visibility` | string | Most-restrictive resolution across `visibility_context` contributors and all `runtime_artifacts[].visibility` | Derived; updated on every artifact-producing event |

`visibility_context` is the immutable record of the run's initial visibility
inputs: trigger, policy, and governing-contract contributors plus the initial
resolved run visibility. It is copied once from `run.created` and MUST NOT be
modified by any later event or consumer.

`runtime_artifacts` is an ordered array of full RuntimeArtifact objects
conforming to `references/runtime-artifact-visibility-contract.md` Sec.1.1.
Every artifact has `artifact_ref`, `origin_run`, `origin_stage` (when produced
by a stage), `produced_by`, `source_artifacts`, `visibility`, and
`visibility_resolution`. Artifacts are appended in event order and MUST NOT be
reordered or removed after append.

`artifact_refs` is a derived compatibility projection: an ordered array of
identity-only ArtifactRef values (`artifact_id`, `artifact_kind`, and optional
`artifact_version`) extracted from `runtime_artifacts[].artifact_ref` in the
same order. It exists so consumers that only need artifact identity references
do not need to parse the full RuntimeArtifact objects.

`resolved_run_visibility` is the most-restrictive visibility across the
initial `visibility_context` contributors and every `runtime_artifacts[].visibility`
value. When a stage produces an artifact with a stricter visibility, only
`resolved_run_visibility` is tightened; the `visibility_context`, prior events,
and artifact `visibility_resolution` fields remain unchanged.

#### 9.2 Deterministic replay and digest inclusion

Projection digest is computed over stable replay state only, excluding
`projection_digest`, `projection_id`, and `derived_at`. The visibility
surface fields -- `visibility_context`, `runtime_artifacts`, `artifact_refs`,
and `resolved_run_visibility` -- are included in the projection digest preimage
when present. Replaying the same ordered events at different wall-clock times
MUST produce the same canonical preimage and projection digest.

Snapshots and checkpoints are optional replay accelerators. Their projection
digest MUST match independent replay. A checkpoint is captured after every stage
boundary when resume is to be supported and identifies its boundary event by id
and order. A snapshot or checkpoint mismatch invalidates only the derived
artifact, never the event stream.

## 10. Typed preservation

ArtifactRef fields (`artifact_id`, `artifact_kind`, optional
`artifact_version`, `locator`, and `digest`) remain individually addressable.
RuntimeArtifact visibility is explicit as `public`, `project`, or `restricted`
and uses the most restrictive contributing source or policy per
`references/runtime-artifact-visibility-contract.md`. Visibility is resolved
at artifact creation time through the typed `VisibilityResolution` shape;
every contributor carries explicit `asserted_visibility`, `authority`, and
`classification_evidence`. The resolved visibility is recorded immutably and
MUST NOT be downgraded, broadened, or reinterpreted by downstream consumers.
Provenance, evidence, relationships, invalidation hooks, and Knowledge lifecycle
fields remain typed and replayable. This runtime contract does not materialize,
classify, extract, or curate Knowledge.

## 11. Executable conformance

Run:

```powershell
python scripts/test_runtime_state_contract.py
```

The suite executes behavior and negative mutations for:

- distinct `run.created` pending genesis and `run.started` active transition;
- root and child genesis without undeclared graph, provenance, or lineage reads;
- exact retry before stale-head evaluation, divergent duplicate rejection, and
  new-event stale-head rejection, plus reducer-before-store transition snapshots;
- executable type, domain, exact-shape, and conditional-field rejection for all
  18 event payloads and receipt fields;
- JCS injectivity and fixed SHA-256 vectors across nested objects, arrays,
  delimiter strings, null, booleans, and integers;
- mandatory signed receipt verification and trusted-head tail truncation;
- deterministic projection digests across different wall-clock derivations;
- terminal and interrupted lineage boundaries;
- retry, resume, and redesign action binding and projection effects;
- full GateDecision fields, degradation conditions, and intervention binding;
- independent completion digest recomputation and forged digest rejection;
- exact event-schema/reducer set equality and reducer payload sufficiency;
- independent frozen crosswalk equality for every required and conditional
  payload field and every reducer `allowed_status`, `next_status`, and `reads`
  value across all 18 event types;
- exhaustive removal, replacement, and addition mutations, including coordinated
  schema-plus-reducer mutations that remain internally self-consistent;
- required-stage and required-gate negative invariants;
- byte-level ASCII hygiene for this public contract.

Removing any declared invariant, schema, reducer, digest exclusion, signed
receipt field, or required payload input causes a conformance failure.
The frozen expected crosswalk is defined independently in the executable test;
it is not derived from the candidate fixture under test.

## 12. Non-goals

This contract does not implement persistence, a storage adapter, a sidecar
daemon, runtime scheduling, retry execution, checkpoint serialization, a gate
engine, Validator coordination, a Validator mesh, a publish gate, dynamic plugin
loading, or proprietary integration.

## 13. Governance

| Field | Value |
|---|---|
| Contract version | 0.9.0 |
| Governing contract | `references/runtime-architecture.md` v0.8.0 |
| Frozen reference | `references/knowledge-contract.md` v0.8.0 |
| Frozen reference | `references/runtime-artifact-visibility-contract.md` v1.0.0 |
| Event schema version | 0.9.0 |
| Risk level | high |
| Validator required | true |
| Visibility | public |
