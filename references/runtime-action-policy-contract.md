---
name: runtime-action-policy-contract
description: Executable pure-function contract for the eight runtime action kinds.
type: contract
version: 2.0.0
supersedes: runtime-action-policy-contract v1.0.0
governing_contract:
  artifact_id: runtime-architecture
  artifact_kind: contract
  artifact_version: 0.8.0
  locator: references/runtime-architecture.md
frozen_references:
  - { artifact_id: runtime-state-contract, artifact_kind: contract, artifact_version: 0.9.0, locator: references/runtime-state-contract.md }
  - { artifact_id: runtime-gate-decision-contract, artifact_kind: contract, artifact_version: 2.2.0, locator: references/runtime-gate-decision-contract.md }
  - { artifact_id: knowledge-contract, artifact_kind: contract, artifact_version: 0.8.0, locator: references/knowledge-contract.md }
risk_level: high
validator_required: true
visibility: public
---

# Runtime Action Policy Contract v2.0.0

This is a pure deterministic decision contract. It accepts only explicit JSON request facts and returns exactly one `RuntimeActionDecision` or one `RuntimeActionEvaluationError`. It executes, schedules, stores, dereferences, reads, generates, and infers nothing. No new public root type and no new authorized or denied reason code is introduced beyond the v1.0.0 set; v2.0.0 is a determinism hardening and bounded-input extension.

## 1. Scope and public types

The only canonical root public types are exactly these eight:

1. `RuntimeActionPolicyDeclaration`
2. `RuntimeBoundaryFacts`
3. `RetryEligibility`
4. `CheckpointEvidence`
5. `ActionAuthorization`
6. `RuntimeActionRequest`
7. `RuntimeActionDecision`
8. `RuntimeActionEvaluationError`

`ArtifactRef`, `GateSnapshotBinding`, `PolicyExhaustionFacts`, `ProhibitedOverrideFacts`, `HistoryPreservationFacts`, `AttemptHistoryFacts`, `RunLineage`, and `EvidenceRequest` are bounded nested helper fact types. They are not additional root public types.

The action-kind enum is exactly `stop_stage`, `stop_run`, `retry`, `resume`, `more_evidence`, `redesign`, `human_intervention`, `terminate`. No action execution authority is granted by a decision.

All objects are closed (`additionalProperties: false`-equivalent). Exact built-in JSON treatment: JSON booleans are not integers; integers have no fractional part; non-finite numbers (NaN, Infinity) and non-JSON containers are malformed; coercion and permissive normalization are forbidden. `run_id` must equal `boundary_facts.parent_run_id`; inequality is `invalid_action_branch`.

## 2. Version metadata and compatibility note

- `version: 2.0.0`; `supersedes: runtime-action-policy-contract v1.0.0`.
- New required bounded facts introduced by v2.0.0 (absent in v1.0.0):
  - `GateSnapshotBinding` is the required bounded nested input on every gate-consuming branch (`stop_stage`, `stop_run`, `more_evidence`, and `human_intervention` when `intervention_source=gate_recommendation`), replacing the v1 embedded `boundary_facts.gate_decision_snapshot` for those branches.
  - `PolicyExhaustionFacts` (closed caller-supplied shape) for `human_intervention` when `intervention_source=policy_exhaustion`.
  - `ProhibitedOverrideFacts` (closed caller-supplied shape) for `human_intervention`.
  - `HistoryPreservationFacts` (closed caller-supplied shape) for `redesign`.
  - The redesign `reason_code` is frozen to a closed 5-value enum (v1.0.0 used a free string).
- Changed error precedence: the v1.0.0 evaluator truth table is replaced by the frozen first-match evaluation algorithm in Section 8. The 12-code taxonomy is unchanged in membership but its evaluation ordering promotes the structural exceptions `authorization_missing`, `authorization_role_invalid`, and `checkpoint_evidence_invalid` ahead of the catch-all `invalid_action_branch` so no structural defect is swallowed.
- Preserved v1.0.0 field names: `relevant_stage_id`, `relevant_stage_status`, `terminate_reason` (NOT `stage_status` or `termination_reason`), and the `RuntimeActionDecision` evidence/provenance surface `evidence_refs`, `parent_run_id`, `child_run_id`, `authorization_echo`, `retry_eligibility`, `checkpoint_evidence`.
- Consumer crosswalk: the next schema ticket must supersede `assets/schemas/runtime-action-policy-v1.schema.json` with a v2 schema and reconcile fixtures before the pure Runtime Action Policy core implementation ticket may start.

## 3. Common request facts

Every `RuntimeActionRequest` has closed common fields:

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `policy_declaration` | required | `RuntimeActionPolicyDeclaration` | n/a | 1 | no | all requests |
| `decision_id` | required | string (nonempty) | n/a | 1 | no | all requests |
| `evaluated_at` | required | string | n/a | 1 | no | all requests |
| `evaluated_by` | required | string (nonempty) | n/a | 1 | no | all requests |
| `run_id` | required | string (nonempty) | n/a | 1 | no | all requests; must equal `boundary_facts.parent_run_id` |
| `action_kind` | required | string | the eight action literals | 1 | no | all requests |
| `boundary_facts` | required | `RuntimeBoundaryFacts` | n/a | 1 | no | all requests |
| `evidence_refs` | optional | array of `ArtifactRef` | n/a | 0..* | no (absent => decision emits explicit empty array) | all requests; copied verbatim into decision |

`RuntimeActionPolicyDeclaration.contract_id` is constant `runtime-action-policy-contract`; `contract_version` is constant `2.0.0`; `policy_id` and `evaluated_under` are non-empty caller facts.

### 3.1 RuntimeActionPolicyDeclaration field table

`RuntimeActionPolicyDeclaration` is the root policy declaration supplied on every `RuntimeActionRequest`. It is a required caller-supplied fact, not a runtime-generated value.

| Field | Posture | Type | Const/Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `contract_id` | required const | string | `runtime-action-policy-contract` | 1 | no | all requests |
| `contract_version` | required const | string | `2.0.0` | 1 | no | all requests |
| `policy_id` | required | string (nonempty) | n/a | 1 | no | all requests |
| `evaluated_under` | required | string (nonempty) | n/a | 1 | no | all requests |

## 4. RuntimeBoundaryFacts field table

`RuntimeBoundaryFacts` is a bounded caller-supplied fact object (NOT a RunProjection snapshot, NOT runtime authority). `parent_run_id` and `parent_run_status` are always required. Per-branch required/forbidden posture for the remaining fields is enforced by the branch tables in Section 9.

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `parent_run_id` | required | string (nonempty) | n/a | 1 | no | all requests |
| `parent_run_status` | required | string | `pending`, `active`, `completed`, `failed`, `blocked`, `interrupted` | 1 | no | all requests |
| `relevant_stage_id` | optional | string or null | n/a | 1 | yes | required non-null on `stop_stage` |
| `relevant_stage_status` | optional | string or null | `pending`, `active`, `completed`, `failed`, `skipped`, null | 1 | yes | required non-null and value `active` for authorized `stop_stage` |
| `gate_decision_snapshot` | optional | `GateDecision` object or null | n/a | 1 | yes | FORBIDDEN on gate-consuming branches (use `gate_snapshot_binding`); allowed only as unused optional on non-gate branches |
| `current_retry_count` | optional | integer >= 0 or null | n/a | 1 | yes | required non-null integer on `retry` |
| `max_retries` | optional | integer >= 0 | n/a | 1 | no | required non-null integer on `retry` (structurally >= 0; evaluator eligibility restricts to 1..3) |
| `same_kind_failure_count` | optional | integer >= 0 or null | n/a | 1 | yes | required non-null integer on `retry` |
| `attempt_history_facts` | optional | `AttemptHistoryFacts` or null | n/a | 1 | yes | required non-null on `retry` |
| `checkpoint_available` | required | boolean | n/a | 1 | no | required on `resume`; `false` is valid and yields `denied_checkpoint_unavailable`; `true` requires `checkpoint` and `checkpoint_event_order`; missing or null -> `invalid_action_branch` |
| `checkpoint_event_order` | optional | integer >= 0 or null | n/a | 1 | yes | required non-null on `resume`; must equal `checkpoint.checkpoint_event_order` |
| `evidence_gap_reason` | optional | string or null | `missing_evidence`, `missing_permission`, `missing_dependency`, `missing_tool`, `unrecoverable_evidence_gap`, null | 1 | yes | required non-null on `more_evidence` |
| `parent_blocked_reason` | optional | string or null | n/a | 1 | yes | informational; no branch requires it |
| `interruption_cause` | optional | string or null | `session_lost`, `environment_terminated`, `external_signal`, null | 1 | yes | required non-null on `resume` |

## 5. ArtifactRef field table

`ArtifactRef` is a closed nested object. Identity comparison uses `(artifact_id, artifact_kind, artifact_version)` only. The evaluator never dereferences it.

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `artifact_id` | required | string (nonempty) | n/a | 1 | no | all usages |
| `artifact_kind` | required | string (nonempty) | n/a | 1 | no | all usages |
| `artifact_version` | optional | string | n/a | 1 | no | optional |
| `locator` | optional | string | n/a | 1 | no | optional portable locator |
| `digest` | optional | string | n/a | 1 | no | required on `GateSnapshotBinding.source_gate_decision_ref` and must match `^sha256:[0-9a-f]{64}$` |

## 6. GateSnapshotBinding

`GateSnapshotBinding` is required for every branch that consumes a GateDecision snapshot: `stop_stage`, `stop_run`, `more_evidence`, and `human_intervention` when `intervention_source=gate_recommendation`. It is forbidden elsewhere.

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `source_gate_decision_ref` | required | `ArtifactRef` | n/a | 1 | no | gate-consuming branches; its `digest` required and matches `^sha256:[0-9a-f]{64}$` |
| `gate_decision_snapshot` | required | complete `GateDecision` v2.2.0 JSON object | n/a | 1 | no | gate-consuming branches |
| `canonical_digest` | required | string | n/a | 1 | no | must equal `source_gate_decision_ref.digest` exactly |

### 6.1 Canonical digest algorithm (executable and unambiguous)

Given the `gate_decision_snapshot` JSON value `S`:

1. **Object key ordering**: recursively sort every object's keys by Unicode code point (ascending). Arrays preserve their given order; array element objects are canonicalized recursively.
2. **String serialization**: serialize every string with JSON string escaping. The characters `"` (U+0022) and `\` (U+005C) are escaped as `\"` and `\\`. Control characters U+0000..U+001F are escaped as lowercase four-digit `\uXXXX` (e.g. U+000A -> `\u000a`); short forms such as `\n` are NOT used. All other characters, including non-ASCII characters, are emitted verbatim (NOT escaped to `\uXXXX`); this is the `ensure_ascii=false` rule.
3. **Number domain**: integers are serialized in base-10 with no leading zeros and no sign on zero. A number that is non-finite (NaN, Infinity) or a non-JSON numeric container makes the value malformed and the binding fails with `gate_snapshot_contradiction`. If a non-integer numeric (fractional) value is present it is serialized with its shortest round-trippable IEEE-754 representation; for this contract the only numbers expected in a GateDecision snapshot are integers.
4. **Whitespace**: emit no insignificant whitespace (key/value separator `:`, item separator `,` only).
5. **Encoding**: UTF-8 encode the resulting byte string.
6. **Digest**: compute SHA-256 over those bytes; the `canonical_digest` value is the lowercase US-ASCII string `sha256:` followed by the 64 lowercase hexadecimal digits of the digest.

A recomputed canonical digest unequal to either supplied digest (`source_gate_decision_ref.digest` or `canonical_digest`) is `gate_snapshot_contradiction`. A malformed digest format is `gate_snapshot_contradiction`. This comparison never dereferences an artifact.

### 6.2 Normative vectors

ASCII vector (decoded preimage, all ASCII):

```
{"decision_id":"gd-1","gate_id":"gate-1","outcome":"fail","recommendation":"stop_stage"}
```

Expected: `sha256:af3973e20c4f1b253ceb502ba84f98f474620781e908426de763b2aca32f4fb4`

Non-ASCII vector (presented in ASCII-safe escaped form; the DECODED value contains non-ASCII characters `e` with acute accent and a space):

```
{"decision_id":"gd-2","failure_description":"\u00e9chec d\u00e9tect\u00e9","gate_id":"gate-2","recommendation":"stop_run"}
```

Decoded preimage (shown escaped above; decoded value carries non-ASCII bytes per the ASCII-safe literal): keys sorted, no whitespace, raw UTF-8 for non-ASCII. The digest is computed over the decoded UTF-8 bytes, not over the escaped form.

Expected: `sha256:f74f6ff1ac8cda04c31d78154c641b468c5b1681c4b20b74516f968ad0275dd7`

A negative mismatch vector: altering a single character of the preimage or of the expected digest must produce a `gate_snapshot_contradiction`.

## 7. Bounded branch fact type field tables

### 7.1 ActionAuthorization

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `authorized_by` | required | string | `architect`, `human`, `system` | 1 | no | required on `retry`, `resume`, `redesign`, `human_intervention`, `terminate`; FORBIDDEN on `stop_stage`, `stop_run`, `more_evidence` |
| `authorized_at` | required | string | n/a | 1 | no | same branches as `authorized_by` |
| `authorization_id` | required | string (nonempty) | n/a | 1 | no | same branches as `authorized_by` |
| `reason` | required | string (nonempty) | n/a | 1 | no | same branches as `authorized_by` |

A missing or incomplete required `ActionAuthorization` is `authorization_missing`; a complete one with a `authorized_by` not in the branch-allowed role set is `authorization_role_invalid`.

### 7.2 CheckpointEvidence

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `checkpoint_ref` | required | `ArtifactRef` | n/a | 1 | no | `resume` |
| `checkpoint_event_order` | required | integer >= 0 | n/a | 1 | no | `resume`; must equal `boundary_facts.checkpoint_event_order` |
| `checkpoint_stage_id` | required | string (nonempty) | n/a | 1 | no | `resume` |
| `recovery_action` | required | string | `replay_from_checkpoint`, `restart_stage` | 1 | no | `resume` |
| `artifacts_produced_before_checkpoint` | required | array of `ArtifactRef` | n/a | >= 1 | no | `resume` |

### 7.3 RetryEligibility (decision-only derived shape)

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `eligible` | required | boolean | n/a | 1 | no | `retry` decision; explicit null constant for non-`retry` |
| `parent_status_satisfied` | required | boolean | n/a | 1 | no | `retry` decision |
| `lineage_satisfied` | required | boolean | n/a | 1 | no | `retry` decision |
| `bounds_satisfied` | required | boolean | n/a | 1 | no | `retry` decision |
| `system_auto_authorized` | required | boolean | n/a | 1 | no | `retry` decision |
| `ineligibility_reason_code` | required | string or null | n/a | 1 | yes | `retry` decision; null when `eligible=true` |

### 7.4 AttemptHistoryFacts

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `attempt_count` | required | integer >= 1 | n/a | 1 | no | `retry` (via `boundary_facts.attempt_history_facts`) |
| `last_failure_category` | required | string or null | n/a | 1 | yes | `retry` |
| `last_failure_transient` | required | boolean or null | n/a | 1 | yes | `retry` |
| `last_failure_deterministic` | required | boolean or null | n/a | 1 | yes | `retry` |

### 7.5 RunLineage

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `parent_run_id` | required | string (nonempty) | n/a | 1 | no | `more_evidence`, `redesign` (via `proposed_child_lineage`); must equal `boundary_facts.parent_run_id` else `child_lineage_parent_mismatch` |
| `lineage_kind` | required | string | `more_evidence`, `redesign` | 1 | no | `more_evidence` const `more_evidence`; `redesign` const `redesign` |
| `lineage_id` | optional | string (nonempty) | n/a | 1 | no | optional |

### 7.6 EvidenceRequest (element of `evidence_requests`)

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `request_id` | required | string (nonempty) | n/a | 1 | no | `more_evidence` |
| `artifact_kind` | required | string (nonempty) | n/a | 1 | no | `more_evidence` |
| `description` | required | string (nonempty) | n/a | 1 | no | `more_evidence` |
| `required` | required | boolean | n/a | 1 | no | `more_evidence` |

### 7.7 PolicyExhaustionFacts

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `exhaustion_classification` | required | string | `no_permitted_action`, `conflicting_authority`, `insufficient_policy_coverage`, `normal_branch_available` | 1 | no | `human_intervention` when `intervention_source=policy_exhaustion` |

`normal_branch_available` produces `policy_exhausted_unsupported`; the other three values remain eligible subject to the branch rules. No prose, artifact, prior state, or free-text inspection is permitted.

### 7.8 ProhibitedOverrideFacts

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `required_gate_override_attempted` | required | boolean | n/a | 1 | no | `human_intervention` |
| `pass_evidence_fabricated` | required | boolean | n/a | 1 | no | `human_intervention` |
| `retry_resume_bounds_bypassed` | required | boolean | n/a | 1 | no | `human_intervention` |

Any true value produces `human_override_prohibited`; all false values permit normal evaluation.

### 7.9 HistoryPreservationFacts

| Field | Posture | Type | Enum | Cardinality | Nullable | Branch applicability |
|---|---|---|---|---|---|---|
| `original_history_preserved` | required | boolean | n/a | 1 | no | `redesign` |
| `original_evidence_preserved` | required | boolean | n/a | 1 | no | `redesign` |

Any false value produces `history_rewrite_prohibited`; both true values permit normal evaluation.

## 8. Error taxonomy, structural precedence, and evaluation algorithm

### 8.1 Canonical 12-code registry (membership and documentation order)

The single ordered 12-code error taxonomy, with no overlapping predicates. Each predicate is observable solely from the request; no code depends on hidden state, prior artifacts, artifact contents, filesystem access, model judgment, or free-text inspection.

| Order | Code | Canonical description | Canonical observable predicate |
|---:|---|---|---|
| 1 | `unknown_action_kind` | The request action kind is not one of the eight supported action literals. | `action_kind` is not one of the eight literals. |
| 2 | `authorization_missing` | A required `ActionAuthorization` is absent or incomplete. | A branch requiring authorization has it absent or lacks a required authorization field. |
| 3 | `authorization_role_invalid` | A complete `ActionAuthorization` names a role disallowed for the well-formed branch. | A complete authorization role is disallowed for the well-formed branch. |
| 4 | `checkpoint_evidence_invalid` | Resume declares an available checkpoint whose evidence is malformed or whose event order does not match. | Resume has `checkpoint_available=true` and malformed checkpoint evidence or unequal checkpoint event orders. |
| 5 | `invalid_action_branch` | The request violates a common or branch shape rule not handled by an earlier structural error. | Any other common/branch shape, type, enum, field posture, or run-id equality failure. |
| 6 | `lineage_self_reference` | A proposed child run id refers to the parent run itself. | A required proposed child run id equals `boundary_facts.parent_run_id`. |
| 7 | `child_lineage_parent_mismatch` | A supplied child lineage names a parent other than the explicit parent run. | A supplied `proposed_child_lineage.parent_run_id` differs from `boundary_facts.parent_run_id`. |
| 8 | `gate_snapshot_contradiction` | The `GateSnapshotBinding` digest is malformed, inconsistent, or does not match the canonical snapshot digest. | A required binding digest format, equality, or recomputation check fails. |
| 9 | `system_retry_unauthorized` | A system retry does not satisfy its required flags or attempt-history agreement. | A complete system retry lacks required system-only agreement or flags. |
| 10 | `policy_exhausted_unsupported` | Policy exhaustion declares that a normal branch is available. | `PolicyExhaustionFacts` says `normal_branch_available`. |
| 11 | `human_override_prohibited` | Human intervention includes a prohibited override fact. | Any `ProhibitedOverrideFacts` boolean is true. |
| 12 | `history_rewrite_prohibited` | Redesign does not preserve the required original history or evidence. | Any `HistoryPreservationFacts` boolean is false. |

### 8.2 Structural precedence exceptions (freeze)

The four structural exceptions are never collapsed into `invalid_action_branch`:

- missing/incomplete required authorization -> `authorization_missing`
- complete disallowed role -> `authorization_role_invalid`
- resume with `checkpoint_available=true` and absent/incomplete/mismatched checkpoint -> `checkpoint_evidence_invalid`
- all other shape defects -> `invalid_action_branch`

### 8.3 Evaluation precedence algorithm (authoritative first-match order)

The registry order above is the documentation order. The evaluation applies the structural exceptions ahead of the catch-all so they are never masked. The authoritative first-match order is:

1. `unknown_action_kind`
2. `authorization_missing` (branch requires authorization and it is absent/incomplete)
3. `authorization_role_invalid` (branch requires authorization, it is complete, but role disallowed)
4. `checkpoint_evidence_invalid` (`resume` with `checkpoint_available=true` and bad checkpoint)
5. `invalid_action_branch` (any other shape/type/enum/posture/run-id defect)
6. `lineage_self_reference`
7. `child_lineage_parent_mismatch`
8. `gate_snapshot_contradiction`
9. `system_retry_unauthorized`
10. `policy_exhausted_unsupported`
11. `human_override_prohibited`
12. `history_rewrite_prohibited`

`denied_gate_recommendation_mismatch` and the other denied codes are evaluated only after this table finds no error. Thus all predicates are disjoint by first match and no error requires hidden state or artifact contents.

## 9. Branch contract per action kind

All undeclared branch fields are forbidden. A known action with missing required, forbidden present, malformed, or wrong-enum branch data is `invalid_action_branch`, except the dedicated precedence cases in Section 8.

### 9.1 stop_stage

| Field | Posture | Type | Enum | Notes |
|---|---|---|---|---|
| `gate_snapshot_binding` | required | `GateSnapshotBinding` | n/a | gate recommendation must equal `stop_stage` |
| `boundary_facts.relevant_stage_id` | required non-null | string | n/a | target stage |
| `boundary_facts.relevant_stage_status` | required non-null | string | `pending`,`active`,`completed`,`failed`,`skipped` | value `active` required for authorized |
| `authorization` | FORBIDDEN | n/a | n/a | stop_stage has no `ActionAuthorization` |
| Authorized role | none | | | |

Normal denied outcomes: `denied_stage_not_active` (stage not active), `denied_gate_recommendation_mismatch` (recommendation differs), `denied_parent_status_ineligible` (parent run not in `active`, see Section 10).

### 9.2 stop_run

| Field | Posture | Type | Enum | Notes |
|---|---|---|---|---|
| `gate_snapshot_binding` | required | `GateSnapshotBinding` | n/a | gate recommendation must equal `stop_run` |
| `authorization` | FORBIDDEN | n/a | n/a | stop_run has no `ActionAuthorization` |
| Authorized role | none | | | |

Normal denied outcomes: `denied_gate_recommendation_mismatch`, `denied_parent_status_ineligible` (parent run not `active`, see Section 10).

### 9.3 retry

| Field | Posture | Type | Enum | Notes |
|---|---|---|---|---|
| `proposed_child_run_id` | required | string (nonempty) | n/a | must not equal `parent_run_id` (`lineage_self_reference`) |
| `retry_strategy` | required | string | `full`, `resume` | system role requires `full` |
| `failure_category` | required | string (nonempty) | n/a | system role requires `command_failed` |
| `transient` | required only if `authorized_by=system`; FORBIDDEN otherwise | boolean | n/a | system-only flag |
| `deterministic` | required only if `authorized_by=system`; FORBIDDEN otherwise | boolean | n/a | system-only flag |
| `authorization` | required | `ActionAuthorization` | `authorized_by` in `architect`,`human`,`system` | |
| `boundary_facts.current_retry_count` | required non-null | integer >= 0 | n/a | |
| `boundary_facts.max_retries` | required non-null | integer >= 0 | n/a | |
| `boundary_facts.same_kind_failure_count` | required non-null | integer >= 0 | n/a | |
| `boundary_facts.attempt_history_facts` | required non-null | `AttemptHistoryFacts` | n/a | |
| Authorized roles | `architect`, `human`, `system` | | | |

Numeric facts are nonnegative integers. Eligibility requires `1 <= max_retries <= 3`, `current_retry_count < max_retries`, and `same_kind_failure_count < 3`; threshold failure is `denied_retry_bounds_exceeded`, not structural rejection. System authorization requires `retry_strategy=full`, `failure_category=command_failed`, `transient=true`, `deterministic=true`, and exact agreement with `attempt_history_facts`. Failure of this system-only rule is `system_retry_unauthorized`. A retry whose well-formed parent status is not `failed` produces `denied_parent_status_ineligible`.

### 9.4 resume

| Field | Posture | Type | Enum | Notes |
|---|---|---|---|---|
| `proposed_child_run_id` | required | string (nonempty) | n/a | must not equal `parent_run_id` |
| `checkpoint` | required when `checkpoint_available=true`; forbidden when `false` | `CheckpointEvidence` | n/a | required only when `checkpoint_available=true`; must be absent when `false` |
| `authorization` | required | `ActionAuthorization` | `authorized_by` in `architect`,`human` | system NOT allowed |
| `boundary_facts.checkpoint_available` | required boolean | boolean | n/a | missing or null -> `invalid_action_branch`; `false` -> `denied_checkpoint_unavailable` (checkpoint absent); `true` -> requires `checkpoint` & `checkpoint_event_order` |
| `boundary_facts.checkpoint_event_order` | required non-null when `checkpoint_available=true`; absent when `false` | integer >= 0 | n/a | must equal `checkpoint.checkpoint_event_order` |
| `boundary_facts.interruption_cause` | required non-null | string | `session_lost`,`environment_terminated`,`external_signal` | |
| Authorized roles | `architect`, `human` | | | |

When `checkpoint_available=false`, `checkpoint` and `checkpoint_event_order` are absent and the result is `denied_checkpoint_unavailable`. When `true`, both are required; a malformed checkpoint or unequal event orders is `checkpoint_evidence_invalid`. A resume whose well-formed parent status is not `interrupted` produces `denied_parent_status_ineligible`.

### 9.5 more_evidence

| Field | Posture | Type | Enum | Notes |
|---|---|---|---|---|
| `gate_snapshot_binding` | required | `GateSnapshotBinding` | n/a | gate recommendation must equal `more_evidence` |
| `proposed_child_lineage` | required | `RunLineage` | `lineage_kind` const `more_evidence` | `parent_run_id` must equal `boundary_facts.parent_run_id` |
| `evidence_requests` | required | array of `EvidenceRequest` | n/a | minItems 1 |
| `boundary_facts.evidence_gap_reason` | required non-null | string | `missing_evidence`,`missing_permission`,`missing_dependency`,`missing_tool`,`unrecoverable_evidence_gap` | |
| `authorization` | FORBIDDEN | n/a | n/a | more_evidence has NO `ActionAuthorization` |
| Authorized role | NONE (must not list Architect or Human) | | | authority comes from the matching GateDecision recommendation and bounded lineage/evidence-gap facts |

Normal denied outcomes: `denied_gate_recommendation_mismatch` (recommendation differs) or `denied_evidence_gap_unrecoverable` (when `evidence_gap_reason=unrecoverable_evidence_gap`). The `unrecoverable_evidence_gap` value is schema-valid and is routed to `denied_evidence_gap_unrecoverable`; it is never denied for being "only recoverable".

### 9.6 redesign

| Field | Posture | Type | Enum | Notes |
|---|---|---|---|---|
| `proposed_child_lineage` | required | `RunLineage` | `lineage_kind` const `redesign` | `parent_run_id` must equal `boundary_facts.parent_run_id` |
| `revised_contract_ref` | required | `ArtifactRef` | n/a | |
| `reason_code` | required | string (closed enum) | `contract_incomplete`,`requirements_changed`,`architecture_conflict`,`evidence_invalidated`,`scope_changed` | frozen 5-value enum |
| `authorization` | required | `ActionAuthorization` | `authorized_by` in `architect`,`human` | system NOT allowed |
| `history_preservation_facts` | required | `HistoryPreservationFacts` | n/a | any false -> `history_rewrite_prohibited` |
| Authorized roles | `architect`, `human` | | | |

### 9.7 human_intervention

| Field | Posture | Type | Enum | Notes |
|---|---|---|---|---|
| `intervention_source` | required | string | `gate_recommendation`, `policy_exhaustion` | |
| `intervention_evidence` | required | array of `ArtifactRef` | n/a | minItems 1 |
| `authorization` | required | `ActionAuthorization` | `authorized_by` in `architect`,`human` | system NOT allowed |
| `human_intent` | required | string | `provide_evidence`,`authorize_action`,`redesign`,`terminate` | |
| `gate_snapshot_binding` | required IFF `intervention_source=gate_recommendation` | `GateSnapshotBinding` | n/a | gate recommendation must be in the human-intervention-eligible set `proceed_with_warning` |
| `policy_exhaustion_facts` | required IFF `intervention_source=policy_exhaustion` | `PolicyExhaustionFacts` | n/a | |
| `prohibited_override_facts` | required | `ProhibitedOverrideFacts` | n/a | any true -> `human_override_prohibited` |
| Authorized roles | `architect`, `human` | | | |

Gate-sourced authorized when binding valid, recommendation eligible, all override booleans false, role allowed. Gate-sourced with valid binding but ineligible recommendation -> `denied_gate_recommendation_mismatch`. Policy-sourced authorized when classification is not `normal_branch_available`, all override booleans false, role allowed; classification `normal_branch_available` -> `policy_exhausted_unsupported`.

### 9.8 terminate

| Field | Posture | Type | Enum | Notes |
|---|---|---|---|---|
| `authorization` | required | `ActionAuthorization` | `authorized_by` in `architect`,`human` | system NOT allowed |
| `terminate_reason` | required | string (nonempty) | n/a | v1 field name preserved (NOT `termination_reason`) |
| Authorized roles | `architect`, `human` | | | |

A terminate against a terminal parent (`completed`, `failed`, `blocked`) -> `denied_terminal_run`. Against a non-terminal parent (`pending`, `active`, `interrupted`) -> authorized `terminate`.

## 10. Stop action parent-status freeze (explicit)

For each parent status, the stop disposition is frozen. `active` is the only stoppable run-level status; all other statuses deny stop with `denied_parent_status_ineligible`. The stage check (`denied_stage_not_active`) applies only when the parent run is `active`. A gate recommendation mismatch (`denied_gate_recommendation_mismatch`) is evaluated before the parent-status rule.

| Parent status | stop_stage (stage active) | stop_stage (stage not active) | stop_run |
|---|---|---|---|
| `active` | authorized `stop_stage` | `denied_stage_not_active` | authorized `stop_run` |
| `pending` | `denied_parent_status_ineligible` | `denied_parent_status_ineligible` | `denied_parent_status_ineligible` |
| `completed` | `denied_parent_status_ineligible` | `denied_parent_status_ineligible` | `denied_parent_status_ineligible` |
| `failed` | `denied_parent_status_ineligible` | `denied_parent_status_ineligible` | `denied_parent_status_ineligible` |
| `blocked` | `denied_parent_status_ineligible` | `denied_parent_status_ineligible` | `denied_parent_status_ineligible` |
| `interrupted` | `denied_parent_status_ineligible` | `denied_parent_status_ineligible` | `denied_parent_status_ineligible` |

No stop row is `malformed` from parent status alone; parent status yields only authorized or denied. Competing defects resolve by the frozen first-match precedence in Section 8.3 (unknown action kind, authorization, checkpoint, run-id equality, lineage, gate snapshot, then the decision rules above).

## 11. Decisions, reason codes, and exhaustive truth tables

`RuntimeActionDecision` is closed with exactly the 14 fields in Section 12. `RuntimeActionEvaluationError` is closed with exactly the 9 fields in Section 12.

The exact authorized codes (disposition `authorized`): `action_authorized_stop_stage`, `action_authorized_stop_run`, `action_authorized_retry`, `action_authorized_resume`, `action_authorized_more_evidence`, `action_authorized_redesign`, `action_authorized_human_intervention`, `action_authorized_terminate`.

The exact denied codes (disposition `denied`): `denied_parent_status_ineligible`, `denied_stage_not_active`, `denied_gate_recommendation_mismatch`, `denied_retry_bounds_exceeded`, `denied_checkpoint_unavailable`, `denied_evidence_gap_unrecoverable`, `denied_terminal_run`. They do not overlap evaluation error codes.

### 11.1 Exhaustive decision truth table

Rows are evaluated after the Section 8.3 error table finds no error. Each row is mutually exclusive; the union covers all eight actions over all parent statuses, all authorized/denied reason codes, and all 12 error codes.

| Action | Parent status | Condition after prior errors | Result (reason_code) | Disposition |
|---|---|---|---|---|
| any | any | `action_kind` outside the eight literals | `unknown_action_kind` | error |
| any auth-required | any | authorization absent/incomplete | `authorization_missing` | error |
| any auth-required | any | authorization complete but role disallowed | `authorization_role_invalid` | error |
| resume | any | `checkpoint_available=true` and checkpoint malformed/order mismatch | `checkpoint_evidence_invalid` | error |
| any | any | other malformed common/branch shape, type, enum, posture, or `run_id != parent_run_id` | `invalid_action_branch` | error |
| retry/resume | any | proposed child run id equals `parent_run_id` | `lineage_self_reference` | error |
| more_evidence/redesign | any | `proposed_child_lineage.parent_run_id` != `parent_run_id` | `child_lineage_parent_mismatch` | error |
| stop_stage/stop_run/more_evidence/human_intervention(gate) | any | binding digest format/equality/recomputation fails | `gate_snapshot_contradiction` | error |
| retry | any | complete system retry lacks flags/agreement | `system_retry_unauthorized` | error |
| human_intervention(policy) | any | `exhaustion_classification=normal_branch_available` | `policy_exhausted_unsupported` | error |
| human_intervention | any | any `ProhibitedOverrideFacts` true | `human_override_prohibited` | error |
| redesign | any | any `HistoryPreservationFacts` false | `history_rewrite_prohibited` | error |
| stop_stage | active | binding rec `stop_stage`, `relevant_stage_status=active` | `action_authorized_stop_stage` | authorized |
| stop_stage | active | binding rec `stop_stage`, `relevant_stage_status` not active | `denied_stage_not_active` | denied |
| stop_stage | active | binding rec differs from `stop_stage` | `denied_gate_recommendation_mismatch` | denied |
| stop_stage | non-active | (any stage status) | `denied_parent_status_ineligible` | denied |
| stop_run | active | binding rec `stop_run` | `action_authorized_stop_run` | authorized |
| stop_run | active | binding rec differs from `stop_run` | `denied_gate_recommendation_mismatch` | denied |
| stop_run | non-active | (any) | `denied_parent_status_ineligible` | denied |
| retry | failed | role valid, bounds satisfied, system rule satisfied if system | `action_authorized_retry` | authorized |
| retry | failed | bounds fail | `denied_retry_bounds_exceeded` | denied |
| retry | failed | system rule fails | `system_retry_unauthorized` | error |
| retry | non-failed | (pending/active/completed/blocked/interrupted) | `denied_parent_status_ineligible` | denied |
| resume | interrupted | `checkpoint_available=false` | `denied_checkpoint_unavailable` | denied |
| resume | interrupted | `checkpoint_available=true`, evidence valid, role allowed | `action_authorized_resume` | authorized |
| resume | non-interrupted | `checkpoint_available=true`, evidence valid, role allowed | `denied_parent_status_ineligible` | denied |
| more_evidence | any | binding rec `more_evidence`, `evidence_gap_reason=unrecoverable_evidence_gap` | `denied_evidence_gap_unrecoverable` | denied |
| more_evidence | any | binding rec `more_evidence`, `evidence_gap_reason` recoverable | `action_authorized_more_evidence` | authorized |
| more_evidence | any | binding rec differs from `more_evidence` | `denied_gate_recommendation_mismatch` | denied |
| redesign | any | preservation both true, role allowed | `action_authorized_redesign` | authorized |
| human_intervention | any | gate-sourced, valid binding, eligible rec, overrides false, role allowed | `action_authorized_human_intervention` | authorized |
| human_intervention | any | gate-sourced, valid binding, ineligible rec | `denied_gate_recommendation_mismatch` | denied |
| human_intervention | any | policy-sourced, classification not normal, overrides false, role allowed | `action_authorized_human_intervention` | authorized |
| human_intervention | any | policy-sourced, `normal_branch_available` | `policy_exhausted_unsupported` | error |
| terminate | non-terminal | role allowed | `action_authorized_terminate` | authorized |
| terminate | terminal | role allowed | `denied_terminal_run` | denied |

Every malformed or competing-defect cell resolves to the first matching Section 8.3 row before this table. Therefore each of the eight actions has one and only one outcome for every JSON request.

## 12. Field-origin crosswalk (set-equal)

### 12.1 RuntimeActionDecision (exactly 14 fields)

| Field | Origin | Derivation / constant / source |
|---|---|---|
| `decision_id` | copied request field | `request.decision_id` |
| `action_kind` | copied request field | `request.action_kind` |
| `disposition` | closed-table derivation | `authorized` or `denied` per Section 11 |
| `policy_declaration` | copied request field | `request.policy_declaration` |
| `evaluated_at` | copied request field | `request.evaluated_at` |
| `evaluated_by` | copied request field | `request.evaluated_by` |
| `reason_code` | closed-table derivation | one of the 15 authorized/denied codes per Section 11 |
| `evidence_refs` | copied request field | `request.evidence_refs` if present else explicit empty array constant |
| `run_id` | copied request field | `request.run_id` |
| `parent_run_id` | copied request field | `request.boundary_facts.parent_run_id` |
| `child_run_id` | copied request field / explicit null constant | `request.proposed_child_run_id` if present; explicit `null` for `more_evidence` and `redesign` |
| `authorization_echo` | copied request field / explicit null constant | `request.authorization` if present; explicit `null` for `more_evidence` and any branch without authorization |
| `retry_eligibility` | derived shape / explicit null constant | `RetryEligibility` (Section 7.3) for `retry`; explicit `null` otherwise |
| `checkpoint_evidence` | copied request field / explicit null constant | `request.checkpoint` for `resume`; explicit `null` otherwise |

No output id, timestamp, status, evidence, or lineage is generated. No field originates from filesystem access, artifact dereference, model judgment, prior state, free text, clock, random source, or hidden runtime state.

### 12.2 RuntimeActionEvaluationError (exactly 9 fields)

| Field | Origin | Derivation / constant / source |
|---|---|---|
| `policy_declaration` | copied request field / explicit null constant | `request.policy_declaration` if extractable else `null` |
| `decision_id` | copied request field / explicit null constant | `request.decision_id` if extractable else `null` |
| `evaluated_at` | copied request field / explicit null constant | `request.evaluated_at` if extractable else `null` |
| `evaluated_by` | copied request field / explicit null constant | `request.evaluated_by` if extractable else `null` |
| `run_id` | copied request field / explicit null constant | `request.run_id` if extractable; `null` for `unknown_action_kind` when unextractable |
| `action_kind` | copied request field / explicit null constant | `request.action_kind` if extractable; `null` for `unknown_action_kind` |
| `error_code` | explicit constant | the matched code from the Section 8.3 first-match table |
| `description` | explicit constant | the canonical description for `error_code` from Section 8.1 |
| `field_paths` | closed-table derivation | array of JSON pointers to offending fields; explicit empty array when not applicable |

## 13. Consumer and boundary crosswalk

The future v2 schema must supersede the v1 schema (`assets/schemas/runtime-action-policy-v1.schema.json`) and reconcile v1 fixtures before the pure core starts. The pure evaluator implements only Sections 1-12. Sidecars, adapters, schedulers, retries, resumes, event recording, and human action execution consume decisions but do not extend this contract. This contract neither modifies nor reinterprets the frozen Runtime Architecture, Runtime State, Gate Decision, or Knowledge contracts.

## 14. Public hygiene

This file is ASCII-safe and contains no ticket identifiers, Control paths, machine-local paths, agent brands, secrets, or private project identifiers.
