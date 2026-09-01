---
name: runtime-evidence-export-contract
description: Versioned, storage-neutral contract for exporting one complete runtime run as independently verifiable evidence
type: contract
version: 1.1.0
supersedes: 1.0.0
governing_contracts:
  - artifact_id: runtime-state-contract
    artifact_kind: contract
    artifact_version: 0.9.0
    locator: references/runtime-state-contract.md
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
---

# Runtime Evidence Export Contract

This contract defines the versioned, storage-neutral contract for exporting one
complete runtime run as independently verifiable evidence. The export envelope
is a self-contained, digest-bound artifact that an offline consumer can verify
without access to the source runtime store, workflow database, or Control state.

## 1. Authority and separation

The append-only event stream is the sole authoritative record of runtime state.
An export is derived evidence. A reconstructed RunProjection, a snapshot, a
checkpoint, or any other derived artifact inside the export envelope MUST NOT
mutate, contradict, or replace authoritative event-stream state.

Exports are point-in-time evidence. An export captures the event stream up to
the source stream head at export time. Later events appended after the export
are not part of that export and do not invalidate it.

## 2. Complete-run export envelope

A complete-run export envelope is a single JSON object with these required
top-level fields. No field beyond those listed here is part of this contract.

### 2.1 Envelope identity

| Field | Type | Required | Contract |
|---|---|---|---|
| `export_version` | string | Yes | Exactly `"1.1.0"`. |
| `export_id` | string | Yes | Globally unique export identity. |
| `exported_at` | string | Yes | ISO 8601 timestamp with timezone of export creation. |

### 2.2 Run identity

| Field | Type | Required | Contract |
|---|---|---|---|
| `run_id` | string | Yes | Non-empty run identity. Must match `run_id` in every event, receipt, and projection. |

### 2.3 Source stream head

| Field | Type | Required | Contract |
|---|---|---|---|
| `source_stream_head` | object | Yes | Stream head at the moment of export. |
| `source_stream_head.event_order` | integer | Yes | Non-negative integer. Must equal the highest `event_order` in `events`. |
| `source_stream_head.content_digest` | string | Yes | Digest matching pattern `^sha256:[a-f0-9]{64}$`. Must equal the stored `content_digest` of the last event. |

### 2.4 Events

| Field | Type | Required | Contract |
|---|---|---|---|
| `events` | array of RuntimeEvent | Yes | Complete, gap-free, ordered event stream from event_order 1 through source_stream_head.event_order. |

Each RuntimeEvent is a stored event as defined by `references/runtime-state-contract.md`
Sec.2.3, containing every required AppendRequest field (`run_id`, `event_type`,
`payload`, `causation_id` or `causation_chain`, `actor_role`, `actor_identity`,
`trigger_artifact`, `reason`, `recommended_action`, `expected_stream_head`,
`client_event_id`, `prev_event_digest`) and every store-assigned field (`event_id`,
`event_order`, `occurred_at`, `schema_version`, `prior_state`, `next_state`,
`content_digest`). Event `event_order` starts at 1 and is strictly increasing and
gap-free within the export. `prev_event_digest` links to the prior event's
`content_digest`, with the first event linking to the zero digest
`sha256:0000000000000000000000000000000000000000000000000000000000000000`.

Schema_version of every stored event MUST be `"0.9.0"`.

Event types must be from the frozen taxonomy in `references/runtime-state-contract.md`
Sec.5. No aliases or custom event types are conforming.

### 2.5 Receipts

| Field | Type | Required | Contract |
|---|---|---|---|
| `receipts` | array of receipt objects | Yes | At least one receipt covering the source stream head. |

Each receipt object contains:

| Field | Type | Required | Contract |
|---|---|---|---|
| `event_id` | string | Yes | Stored event identity. |
| `event_order` | integer | Yes | Stored event order, positive. |
| `stored_content_digest` | string | Yes | Stored event digest matching `^sha256:[a-f0-9]{64}$`. |
| `new_stream_head` | object | Yes | `{event_order, content_digest}` after append. |
| `signed_receipt` | object | Yes | Per `references/runtime-state-contract.md` Sec.2.4. |

`signed_receipt` contains `algorithm`, `key_id`, `signed_payload` (exact
`{run_id, event_order, content_digest}`), and `signature`. At least one receipt
must bind the final event in the export at `source_stream_head.event_order`.

Receipts appear in event order. Not every event requires a receipt; the required
minimum is at least one receipt covering the current stream head. Intermediate
receipts increase verification confidence.

### 2.6 Projection

| Field | Type | Required | Contract |
|---|---|---|---|
| `projection` | object | Yes | RunProjection reconstructed by replaying all exported events. |

The RunProjection preserves all stable replay-state fields defined in
`references/runtime-state-contract.md` Sec.9, specifically: `run_id`, `status`,
`visibility_context`, ordered `runtime_artifacts`, identity-only `artifact_refs`,
`resolved_run_visibility`, `stage_graph` (including `stages` with their `stage_id`,
`status`, `required`, gate decisions, and artifact references), `lineage` (present
for child runs, absent for root runs), `stage_states`, `gate_decisions`,
`gate_decision_history`, event envelope fields, and `projection_digest`.

`projection_digest` is computed over the stable replay preimage excluding
`projection_digest`, `projection_id`, and `derived_at` per
`references/runtime-state-contract.md` Sec.3.1.

| Field | Type | Required | Contract |
|---|---|---|---|
| `event_range` | object | Yes | First and last event_order covered by this projection. |
| `event_range.first` | integer | Yes | Must be 1. |
| `event_range.last` | integer | Yes | Must equal `source_stream_head.event_order`. |

### 2.7 Visibility

| Field | Type | Required | Contract |
|---|---|---|---|
| `visibility` | string | Yes | One of `"public"`, `"project"`, `"restricted"`. |

The envelope visibility is a derived value, not a self-declared scalar. It MUST
equal the most-restrictive resolved visibility computed from `visibility_basis`
(Sec.2.7.1). A mismatch between the envelope `visibility` and the resolved
visibility_basis result is non-conforming.

The export visibility is the most restrictive visibility among all artifacts
contained in the export, governed by `references/knowledge-contract.md` Sec.2.3
and Sec.6. An export containing any `restricted` artifact must itself be
`restricted`. An export containing any `project` artifact but no `restricted`
artifacts is `project`. An export containing only `public` artifacts is `public`.

#### 2.7.1 Visibility Basis

| Field | Type | Required | Contract |
|---|---|---|---|
| `visibility_basis` | array of VisibilityBasisEntry | Yes | Non-empty array of explicit visibility contributors. |

Every entry in the array is a `VisibilityBasisEntry` with these fields:

| Field | Type | Required | Contract |
|---|---|---|---|
| `contributor_kind` | string | Yes | One of `contained_artifact`, `governing_contract`, `project_policy`, or `trigger_provenance`. |
| `contributor` | ArtifactRef | Yes | Typed artifact, contract, or policy reference. For `project_policy`, `artifact_kind` MUST be `policy`. |
| `asserted_visibility` | string | Yes | One of `public`, `project`, or `restricted`. The explicit visibility this contributor requires. |
| `rationale` | string | Yes | Non-empty explanation of why this visibility is asserted for this contributor. |

#### 2.7.2 Most-Restrictive Resolution

The resolved visibility is the most restrictive value among all
`visibility_basis` entries, following the partial order `restricted > project >
public`. When any entry asserts `restricted`, the resolved visibility is
`restricted`. When no entry asserts `restricted` and at least one entry asserts
`project`, the resolved visibility is `project`. When all entries assert
`public`, the resolved visibility is `public`.

The envelope `visibility` field MUST equal the resolved visibility_basis result.
An offline verifier derives the envelope visibility exclusively from
`visibility_basis`, never from the self-declared `visibility` field alone.

#### 2.7.3 Basis Completeness Rules

1. Every runtime artifact, event payload artifact, or projection artifact
   contained in the export that carries an explicit visibility classification
   MUST be represented by a `contained_artifact` entry.
2. A run with no contained runtime artifacts still requires at least one
   accepted `governing_contract` or `project_policy` entry.
3. Every `governing_contract` listed in `provenance.governing_contracts` that
   carries a declared visibility MUST be represented by a `governing_contract`
   entry.
4. A `project_policy` entry MUST be present when the export relies on
   project-level policy to justify its visibility.

#### 2.7.4 Basis Integrity Rules

1. **Duplicate rejection**: No two entries may have the same
   `(contributor_kind, contributor.artifact_id, contributor.artifact_kind)`
   identity. A duplicate causes verification failure.
2. **Conflict detection**: A conflict exists when two or more entries with
   different `contributor_kind` values assert different visibility values for
   what is effectively the same scope. Conflicts are rejected.
3. **Missing contributor**: Every contained artifact, governing contract, and
   project-policy source that contributes to the export's visibility
   classification MUST appear in `visibility_basis`. A missing contributor
   that affects the resolved visibility causes verification failure.
4. **Omitted restricted contributor**: An artifact with `restricted` visibility
   that is not represented in `visibility_basis` is a hard failure. The
   resolved visibility would incorrectly exclude a restrictive requirement.
5. **Invalid visibility value**: Any entry whose `asserted_visibility` is not
   one of `public`, `project`, or `restricted` is non-conforming.
6. **Envelope downgrade**: The envelope `visibility` MUST NOT be less
   restrictive than the resolved `visibility_basis` result. A downgrade is a
   non-conforming export.

### 2.8 Provenance

| Field | Type | Required | Contract |
|---|---|---|---|
| `provenance` | object | Yes | Typed provenance for this export. |

| Field | Type | Required | Contract |
|---|---|---|---|
| `provenance.origin_artifact` | ArtifactRef | Yes | The triggering source for the exported run. `artifact_kind` is `ticket`, `pipeline_config`, `script`, or `request_artifact`. |
| `provenance.governing_contracts` | array of ArtifactRef | Yes | Governing contracts for the exported run. Must include at least `references/runtime-state-contract.md` and `references/knowledge-contract.md`. |

Every ArtifactRef follows `references/knowledge-contract.md` Sec.1.1 with
required `artifact_id` and `artifact_kind`, and optional `artifact_version`,
`locator`, and `digest`.

### 2.9 Export content digest

| Field | Type | Required | Contract |
|---|---|---|---|
| `export_content_digest` | string | Yes | SHA-256 digest of the entire export envelope excluding this field. Pattern `^sha256:[a-f0-9]{64}$`. |

## 3. Canonical digest preimage

The export content digest is computed as follows:

1. Take the complete export envelope object with all fields except
   `export_content_digest`.
2. Serialize the object using RFC 8785, JSON Canonicalization Scheme (JCS), into
   UTF-8 bytes.
3. Compute SHA-256 over those canonical UTF-8 bytes.
4. Format the result as `sha256:<64 lowercase hex characters>`.

JCS rules applicable to this contract mirror `references/runtime-state-contract.md`
Sec.3: object keys are sorted by unsigned UTF-16 code units; I-JSON values
(objects, arrays, strings, null, booleans, integers in the range `-(2^53)+1`
through `(2^53)-1`) are accepted; floating-point values are outside this profile;
all strings contain valid Unicode scalar values; lone UTF-16 surrogates are
rejected; UTF-8 encoding is mandatory.

### 3.1 Excluded presentation fields

Only `export_content_digest` is excluded from the export digest preimage. All
other envelope fields, including `export_id`, `exported_at`, `source_stream_head`,
every event field including `content_digest`, every receipt field, every
projection field including `projection_digest`, `visibility`, `visibility_basis`,
and all provenance fields, are included.

## 4. Deterministic offline verification

An offline consumer verifies the export in this exact order. A failure at any
step produces the listed failure code and stops verification. All steps must
pass for the export to be accepted as valid.

### Step 1: Verify schema

Parse the export envelope as JSON. Verify it is a valid object with all required
top-level fields in Sec.2. Verify every required sub-field, typed domain, and
pattern constraint.

**Failure code: `schema_invalid`**

### Step 2: Verify export content digest

Remove `export_content_digest`, canonicalize per Sec.3, compute SHA-256, and
compare with the asserted `export_content_digest`. Reject any mismatch.

**Failure code: `digest_mismatch`**

### Step 3: Verify event hash chain

Iterate through `events` in `event_order`. Verify `event_order` starts at 1 and
is strictly increasing and gap-free. For each event, compute its own
`content_digest` by removing the `content_digest` field from the stored event,
canonicalizing, and hashing. Compare the computed digest with the asserted
`content_digest` field. Then verify `prev_event_digest`:

- Event 1: `prev_event_digest` MUST equal the zero digest
  `sha256:0000000000000000000000000000000000000000000000000000000000000000`.
- Event N > 1: `prev_event_digest` MUST equal event (N-1)'s computed or asserted
  `content_digest`.

Reject any mismatch.

**Failure code: `hash_chain_broken`**

### Step 4: Verify receipts

For each receipt object, verify all checks listed in
`references/runtime-state-contract.md` Sec.2.4:

1. Required field presence and type correctness.
2. `algorithm` is a registered algorithm identifier.
3. `key_id` resolves to independently trusted verification material.
4. The signature or MAC verifies over canonical `signed_payload` bytes.
5. `signed_payload.run_id` equals the export `run_id`.
6. `signed_payload.event_order` equals `new_stream_head.event_order`.
7. `signed_payload.content_digest` equals `new_stream_head.content_digest`.
8. Top-level `event_order` equals `new_stream_head.event_order`.
9. Top-level `stored_content_digest` equals `new_stream_head.content_digest`.

At least one receipt must cover the source stream head. The last receipt's
`event_order` and `content_digest` must match `source_stream_head`.

**Failure code: `receipt_invalid`**

### Step 5: Verify projection digest

Replay every event through a deterministic reducer matching the contract in
`references/runtime-state-contract.md` Sec.7. Compute the resulting projection,
then compute its digest per Sec.3.1 of that contract (excluding
`projection_digest`, `projection_id`, `derived_at`). Compare with the asserted
`projection.projection_digest`. Reject any mismatch.

**Failure code: `projection_digest_mismatch`**

### Step 6: Verify stream head

Verify that `source_stream_head.event_order` equals the highest `event_order`
in `events`. Verify that `source_stream_head.content_digest` equals the
computed `content_digest` of the last event. Also verify that the last
event's `event_order` equals the projection's total event count.

**Failure code: `stream_head_mismatch`**

### Step 7: Verify visibility from basis

This step replaces the legacy `visibility_downgrade` check. The verifier
derives the envelope visibility exclusively from `visibility_basis`.

1. Parse `visibility_basis` as a non-empty array of objects with
   `contributor_kind`, `contributor` (ArtifactRef), `asserted_visibility`,
   and `rationale`.
2. Verify every `asserted_visibility` is one of `public`, `project`, or
   `restricted`.
3. Verify no duplicate `(contributor_kind, contributor.artifact_id,
   contributor.artifact_kind)` identities exist in the array.
4. Verify the array contains at least one `governing_contract` or
   `project_policy` entry.
5. Compute the resolved visibility by applying the most-restrictive
   resolution (Sec.2.7.2) over every entry's `asserted_visibility`.
6. Verify the envelope `visibility` field equals the resolved visibility.
7. Verify that every governing contract listed in
   `provenance.governing_contracts` that carries a declared visibility
   is represented by a `governing_contract` entry. The governing contracts
   `runtime-state-contract` (v0.9.0, public), `knowledge-contract`
   (v0.8.0, public), and `runtime-artifact-visibility-contract`
   (v1.0.0, public) are the minimum expected set.
8. Verify that every contained runtime artifact, event payload artifact,
   and projection artifact carrying an explicit visibility classification
   is represented by a `contained_artifact` entry.

**Failure code: `visibility_basis_missing`** -- `visibility_basis` is absent,
empty, or malformed (missing required fields, wrong types, or pattern violations).

**Failure code: `visibility_basis_invalid_entry`** -- An entry has an
`asserted_visibility` value outside the allowed set, or `contributor_kind`
is not one of `contained_artifact`, `governing_contract`, `project_policy`,
or `trigger_provenance`.

**Failure code: `visibility_basis_duplicate`** -- Two or more entries share
the same `(contributor_kind, contributor.artifact_id, contributor.artifact_kind)`
identity.

**Failure code: `visibility_basis_conflict`** -- Two or more entries assert
different visibility values for the same scope and no resolution rule selects
the more restrictive value deterministically.

**Failure code: `visibility_basis_incomplete`** -- A contained artifact,
governing contract, or project-policy source that contributes to the export's
visibility classification is missing from `visibility_basis`.

**Failure code: `visibility_downgrade`** -- The envelope `visibility` is less
restrictive than the resolved `visibility_basis` result. A `public` envelope
when the basis resolves to `project` or `restricted`, or a `project` envelope
when the basis resolves to `restricted`.

### Step 8: Verify provenance

Verify that `provenance.origin_artifact` is a valid ArtifactRef with non-empty
`artifact_id` and a conforming `artifact_kind` from the set `ticket`,
`pipeline_config`, `script`, or `request_artifact`. Verify that
`provenance.governing_contracts` is a non-empty array of ArtifactRef values,
each with non-empty `artifact_id` and `artifact_kind`. Each ArtifactRef must
not use a machine-local absolute path as a `locator`.

**Failure code: `provenance_missing`**

### Step 9: Verify event order gap

Confirm that `events` contains every positive integer from 1 through
`source_stream_head.event_order` exactly once, with no gaps or duplicates.
This is a stricter check than the hash chain order check and further
detects gap-free completeness.

**Failure code: `event_order_gap`**

### 4.10 Verification trust material

Production receipt verification material (HMAC secrets and private keys) MUST NOT
appear in the export envelope at any visibility. The export stores only the static
receipt bytes---the `algorithm`, `key_id`, `signed_payload`, and `signature`---and
never the raw verification key.

A conforming verifier resolves verification material by `key_id` through its own
out-of-band trust configuration. The verifier MUST NOT:

- Read verification material from the embedded receipt `key_id` string.
- Read verification material from the source runtime store.
- Infer verification material from any field inside the export envelope, including
  `signed_receipt.key_id`, `provenance`, or event payloads.
- Treat a `key_id` present in the export as proof that the corresponding key is
  trusted.

The verifier MUST reject unknown `key_id` values and unknown `algorithm`
identifiers rather than interpreting receipt-provided data as verification
material. This mirrors the production verification procedure defined in
`references/runtime-state-contract.md` Sec.2.4.

### 4.11 Test-only conformance key

To make the public `complete-run-export.json` example independently verifiable
without access to a source runtime store, this contract publishes a single
test-only, non-secret conformance HMAC key. This key is explicitly not a
production secret and MUST NOT be used for production receipt signing.

- **Algorithm:** `HMAC-SHA256`
- **Key ID:** `conformance-key-1`
- **Key (ASCII bytes):** `railyard-conformance-hmac-key-v1-test-only`

A fresh verifier computes each example receipt signature by canonicalizing the
`signed_payload` object per Sec.3, computing HMAC-SHA256 over those canonical
bytes with the ASCII key above, and formatting the 32-byte result as 64 lowercase
hexadecimal characters. A verifier that uses any other key, or that reads
verification material from the example file, will produce incorrect signatures.

## 5. Structured failure codes

| Code | Description |
|---|---|
| `schema_invalid` | The export envelope does not satisfy the schema. Missing required fields, wrong types, or pattern violations. |
| `digest_mismatch` | The asserted `export_content_digest` does not match recomputed canonical digest. |
| `hash_chain_broken` | An event's `prev_event_digest` does not match the prior event's `content_digest`, or a `content_digest` does not match its own recomputed value. |
| `receipt_invalid` | A receipt fails cryptographic verification or internal field consistency checks. |
| `projection_digest_mismatch` | The asserted `projection.projection_digest` does not match the digest of reconstructed replay state. |
| `stream_head_mismatch` | The asserted `source_stream_head` does not match the last event's identity or digest. |
| `visibility_basis_missing` | The `visibility_basis` array is absent, empty, or structurally malformed. |
| `visibility_basis_invalid_entry` | A `visibility_basis` entry has an invalid `asserted_visibility` or `contributor_kind` value. |
| `visibility_basis_duplicate` | Two or more `visibility_basis` entries share the same contributor identity. |
| `visibility_basis_conflict` | Two or more entries assert different visibility for the same scope without deterministic resolution. |
| `visibility_basis_incomplete` | A contained artifact, governing contract, or project-policy visibility contributor is missing from the basis. |
| `visibility_downgrade` | The envelope visibility is less restrictive than the resolved `visibility_basis` result. |
| `provenance_missing` | The export provenance is missing, incomplete, or contains an invalid ArtifactRef. |
| `event_order_gap` | Event orders are not contiguous from 1 through the stream head. |

A verifier returns exactly one failure code per run. If multiple failures exist,
the first failure discovered in the prescribed verification order is reported.

## 6. Authority boundaries

| Artifact type | Authority | Relationship to export |
|---|---|---|
| Event stream | Authoritative | The source of truth. Export captures a point-in-time copy. |
| Export | Derived evidence | A self-contained digest-bound snapshot of one run. |
| Projection | Derived evidence | Reconstructed from events; must match independent replay. |
| Snapshot | Derived evidence | Optional replay accelerator; mismatch invalidates only the snapshot, never the event stream. |
| Checkpoint | Derived evidence | Optional stage-boundary capture; mismatch invalidates only the checkpoint, never the event stream. |

An export MUST NOT claim to replace, invalidate, or supersede the event stream.
An event appended after export time does not invalidate the export; the export
is valid for the stream state at its declared export time.

## 7. Visibility preservation

### 7.1 No-downgrade behavior

The export visibility is a single envelope-level value reflecting the most
restrictive visibility of any artifact contained in the export. The following
downgrades are non-conforming:

- A `public` export containing `project` or `restricted` artifacts.
- A `project` export containing `restricted` artifacts.
- Any export that omits a required visibility contributor from the set.

### 7.2 Path neutrality

Visibility MUST NOT be inferred from file paths, locator strings, or artifact
naming conventions. Visibility is carried as an explicit typed field on the
export envelope and on every `RuntimeArtifact` contained in events and
projection.

### 7.3 Redaction limits

When an export must be limited to a less restrictive visibility than the full
run stream, the following material MUST be redacted or omitted rather than
exposed at the wrong visibility:

- Secrets, credentials, tokens, and private keys. These are never exportable
  at any visibility.
- Internal or proprietary identifiers, locators, or configuration.
- Material whose accepted visibility forbids the export target visibility.

Redaction of runtime events or receipt fields causes verification to fail.
A partial export that omits events, receipts, or projection fields is not a
conforming complete-run export. A conforming complete-run export at a given
visibility must contain only artifacts compatible with that visibility.

## 8. Optional snapshots and checkpoints

Snapshots and checkpoints are optional replay accelerators. This contract does
not require their presence in a complete-run export.

When present in the export envelope, they appear as optional additional
top-level fields:

| Field | Type | Required | Contract |
|---|---|---|---|
| `snapshots` | array | No | Optional array of RunProjection snapshots at specific event orders. |
| `checkpoints` | array | No | Optional array of checkpoint objects, each with `stage_id`, `event_order`, `event_id`, `artifacts`, and `content_digest`. |

Snapshot and checkpoint digests are independent of the export content digest.
They do not affect export verification unless the optional snapshot or
checkpoint verification is also requested. A snapshot or checkpoint that
fails its own digest check does not invalidate the export, events, receipts,
projection, or any other verified envelope field.

When omitted, the export is still a valid complete-run export.

## 9. Implementation crosswalk

This section is a conceptual mapping from contract fields to concerns a future
exporter implementation must address. It contains no implementation code.

| Contract field or rule | Implementation concern |
|---|---|
| `export_version`, `export_id`, `exported_at` | Exporter assigns version, generates a unique UUID-like identifier, and records the ISO 8601 export timestamp. |
| `run_id`, `source_stream_head` | Store reads the current run head from the event store. |
| `events` | Store reads the full ordered event stream and serializes each stored RuntimeEvent with all fields. |
| `receipts` | Store reads persisted AppendReceipts. At minimum the receipt for the stream head. |
| `projection` | Store outputs the current RunProjection from the event store projection or replays events to rebuild it. |
| `projection.projection_digest` | Compute by canonicalizing the projection minus presentation fields (`projection_digest`, `projection_id`, `derived_at`) and hashing. |
| `visibility` | Compute as the most restrictive visibility across all contained runtime artifacts, event payload fields, and provenance entries. |
| `visibility_basis` | Build a non-empty array of VisibilityBasisEntry objects. Include every governing contract from `provenance.governing_contracts`, every contained runtime artifact with explicit visibility classification, and at least one project_policy entry. Compute the resolved visibility via most-restrictive resolution; verify it equals the envelope `visibility` field. |
| `provenance` | Read from the run's stored provenance. Construct ArtifactRef values from the store. |
| `export_content_digest` | Compute by assembling the complete envelope without this field, canonicalizing, and hashing. Write this field last. |
| Event hash chain | Events are exported in store order. The store-verified chain transfers to the export as-is. The exporter must not reorder, insert, or omit events. |
| Receipt verification material | Export stores static receipt bytes. Production verification material (HMAC secrets and private keys) MUST NOT enter the export. The verifier resolves trusted keys by `key_id` through its own out-of-band trust configuration; it MUST NOT read verification material from the embedded receipt `key_id` string, from the source runtime store, or from any field inside the export envelope. |
| Offline verification | Any conforming verifier replays the ordered events with a deterministic reducer matching `references/runtime-state-contract.md` Sec.7, recomputes all digests, and verifies every link, receipt, and head. |
| Snapshot/checkpoint (optional) | Exporter reads optional snapshots and checkpoints from the store and includes them. They are opaque to core export verification. |

## 10. Non-goals

This contract does NOT define or prescribe:

- Import, restore, or playback of an export into a runtime store.
- Snapshot persistence, serialization, or versioning.
- Checkpoint serialization or migration.
- CLI tools, server endpoints, HTTP APIs, or RPC interfaces.
- Scheduling, cron, or periodic export triggers.
- Adapter implementations, provider registrations, or plugin systems.
- Workflow integration, Control lifecycle transitions, or outbox writes.
- Signing-key management, key rotation, or key distribution.
- Export compression, encryption, or transport format (the export is plain JSON).
- Multi-run batching, aggregation exports, or diff exports.
- Knowledge materialization, classification, extraction, or curation.
- Any specific programming language, database, or storage engine.

## 11. Governance

| Field | Value |
|---|---|
| Contract version | 1.1.0 |
| Governing contracts | `references/runtime-state-contract.md` v0.9.0, `references/knowledge-contract.md` v0.8.0, `references/runtime-artifact-visibility-contract.md` v1.0.0 |
| Risk level | high |
| Validator required | true |
| Visibility | public |
