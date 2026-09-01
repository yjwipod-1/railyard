---
name: runtime-gate-decision-contract
description: Executable pure-function Gate Decision contract defining GateEvaluationRequest discriminated union, closed evaluation signal union, evidence canonical projection, recommendation matrix, failure-code matrix, GateEvaluationError taxonomy, re-evaluation protocol, override protocol, and full truth table
type: contract
version: 2.2.0
governing_contract:
  artifact_id: runtime-architecture
  artifact_kind: contract
  artifact_version: 0.8.0
  locator: references/runtime-architecture.md
frozen_references:
  - artifact_id: validator-protocol
    artifact_kind: protocol
    artifact_version: 0.7.0
    locator: references/validator-protocol.md
  - artifact_id: runtime-state-contract
    artifact_kind: contract
    artifact_version: 0.9.0
    locator: references/runtime-state-contract.md
risk_level: high
validator_required: true
visibility: public
---

# Runtime Gate Decision Contract

This contract defines the executable pure-function Gate Decision boundary. Gate evaluation is a pure function of its request inputs. Every output field originates from a caller-supplied value, a closed enumeration, or a deterministic table mapping. No output field requires clock, random ID, filesystem, environment, model inference, event query, artifact dereference, or hidden state.

The frozen Runtime Architecture Contract v0.8.0 is the governing source for Gate and GateDecision identity. The frozen Validator Protocol v0.7.0 is the governing source for Verdict semantics. The frozen Runtime State Contract v0.9.0 is the governing source for gate-related event payloads and reducer invariants.

## 1. Scope and Separation

### 1.1 What This Contract Defines

- GateDeclaration shape and invariants
- GateEvaluationRequest as a discriminated union with three branches
- Closed evaluation signal union keyed by gate_type
- GateEvidenceEnvelope shape
- Canonical evidence projection from GateEvidenceEnvelope to GateDecision.evidence
- GateDecision shape and outcome enumeration
- Deterministic signal-to-decision mapping by gate_type
- Evidence completeness rules and classification
- Unified empty-evidence rule
- Re-evaluation protocol with new-evidence detection
- Override protocol with authorization and validation
- Recommendation matrix (total and single-valued)
- Complete failure-code matrix
- GateEvaluationError taxonomy with first-match precedence
- Normative field-origin crosswalk
- Full field-level truth table
- failure_description and error_description provenance rules
- GateDeclaration invariant checks
- Decision precedence rules

### 1.2 What This Contract Does Not Define

- Event append, storage, or sidecar behavior
- Retry, resume, or lineage creation
- Validator dispatch, contract construction, or report production
- Workflow lifecycle, ticket/epic transitions, or review recording
- StageGraph resolution, stage scheduling, or stage lifecycle
- Adapter negotiation, provider selection, or transport configuration
- Gate engine implementation, rule evaluator, or decision executor
- Artifact dereference, file reading, or content inspection

### 1.3 Contract Hierarchy

| Governing Contract | Relationship |
|---|---|
| `references/runtime-architecture.md` v0.8.0 | Defines Gate and GateDecision identity, ownership, required-gate invariants |
| `references/validator-protocol.md` v0.7.0 | Defines Verdict semantics consumed by signal-to-decision mapping |
| `references/runtime-state-contract.md` v0.9.0 | Defines gate event payload schemas and reducer invariants that preserve this contract |

This contract does not modify, reinterpret, or override any governing contract.

## 2. Gate Declaration

### 2.1 GateDeclaration Shape

| Field | Type | Required | Contract |
|---|---|---|---|
| `gate_id` | string | Yes | Stable identifier unique within the containing stage. Non-empty. |
| `gate_type` | string | Yes | Closed enumeration: `validator`, `artifact_shape`, `diff_review`, `custom`. |
| `required` | boolean | Yes | If `true`, this gate MUST NOT be skipped, overridden, or treated as pass without evidence. |
| `allow_gate_override` | boolean | Conditional | Required when `required` is `false`. When `true`, an optional gate may be overridden by authorized intervention. |
| `contract_ref` | ArtifactRef | Conditional | Required when `gate_type` is `validator`. Must be a complete typed ArtifactRef. |
| `failure_behavior` | string | Yes | Closed enumeration: `halt_stage`, `halt_run`, `warn`, `require_intervention`. |

### 2.2 GateDeclaration Invariants

1. `gate_id` MUST be non-empty and unique within its containing stage.
2. `gate_type` MUST be exactly one of the closed enumeration values.
3. A gate with `required: true` MUST NOT have `allow_gate_override: true`. Required gates are never overridable.
4. A gate with `required: false` MUST explicitly declare `allow_gate_override` as `true` or `false`.
5. A gate with `gate_type: validator` MUST have a non-null `contract_ref` with `artifact_kind: contract`.
6. A gate with `gate_type: validator` and `required: true` requires the Validator report to have been produced by an independent Validator role.
7. A gate with `failure_behavior: warn` and `required: true` produces a GateEvaluationError with error_code `required_gate_warn_behavior`. A required gate cannot use `warn` failure_behavior.

### 2.3 Required Gate Non-Degradable Correctness

A gate with `required: true` MUST NOT be skipped, overridden, or treated as pass without evidence under any circumstance. The following paths are prohibited:

| Path | Prohibited? | Resolution |
|---|---|---|
| Degradation pass | Prohibited | `execution_mode` degradation MUST NOT change `outcome`. If correctness evidence cannot be obtained, outcome must be `blocked` or `inconclusive`, never `pass`. |
| Override to pass | Prohibited | Required gates cannot be overridden by any role. |
| Missing evidence pass | Prohibited | A required gate with empty or absent evidence MUST NOT produce `outcome: pass`. |
| Unavailable dispatch pass | Prohibited | If the gate evaluator cannot be reached, outcome must be `blocked`, never `pass`. |
| Human intervention pass | Prohibited | Human may provide evidence for re-evaluation but MUST NOT rewrite a required gate's outcome from non-pass to pass. |

## 3. GateEvaluationRequest as Discriminated Union

The `request_kind` field is the explicit discriminator. Its value MUST be one of: `initial`, `reevaluation`, `override`. No field-presence inference is used to determine the branch. Each branch's Required/Optional/Forbidden fields are declared below. Any field not listed as Required or Optional for a branch is FORBIDDEN on that branch.

### 3.1 Initial Evaluation Branch (`request_kind: initial`)

**Required fields:**

| Field | Type | Contract |
|---|---|---|
| `request_kind` | string | Value MUST be `initial`. |
| `decision_id` | string | Caller-supplied stable identifier. Evaluator copies to output. |
| `evaluated_at` | string | Caller-supplied ISO 8601 timestamp. Evaluator copies to output. |
| `evaluated_by` | string | Caller-supplied evaluator identity. Evaluator copies to output. |
| `gate_declaration` | GateDeclaration | The complete gate declaration to evaluate. |
| `evidence_envelope` | GateEvidenceEnvelope | The evidence package to evaluate. |
| `evaluation_signal` | object | The evaluation signal keyed by gate_type (see Section 4). |
| `run_context` | object | Minimal run context: `{ run_id, stage_id }`. Both non-empty. |
| `execution_mode` | string | Closed enumeration: `full`, `degraded_transport`, `degraded_storage`. |

**Optional fields:**

| Field | Type | Contract |
|---|---|---|
| `degradation_note` | string | Required when `execution_mode` is not `full`. |

**Forbidden fields:**

`previous_decision_id`, `previous_decision_snapshot`, `override_authorization`, `prior_evidence_ids`.

### 3.2 Re-evaluation Branch (`request_kind: reevaluation`)

**Required fields:**

| Field | Type | Contract |
|---|---|---|
| `request_kind` | string | Value MUST be `reevaluation`. |
| `decision_id` | string | Caller-supplied new identifier. |
| `evaluated_at` | string | Caller-supplied ISO 8601 timestamp. |
| `evaluated_by` | string | Caller-supplied evaluator identity. |
| `gate_declaration` | GateDeclaration | The complete gate declaration to evaluate. |
| `evidence_envelope` | GateEvidenceEnvelope | New evidence package. |
| `evaluation_signal` | object | The evaluation signal keyed by gate_type (see Section 4). |
| `previous_decision_snapshot` | GateDecision | Complete immutable copy of the prior GateDecision. |
| `prior_evidence_ids` | array of string | Array of ArtifactRef identity strings from the prior evaluation. |
| `run_context` | object | `{ run_id, stage_id }`. |
| `execution_mode` | string | Closed enumeration value. |

**Optional fields:**

| Field | Type | Contract |
|---|---|---|
| `degradation_note` | string | Required when execution_mode is not full. |

**Forbidden fields:**

`override_authorization`.

### 3.3 Override Branch (`request_kind: override`)

**Required fields:**

| Field | Type | Contract |
|---|---|---|
| `request_kind` | string | Value MUST be `override`. |
| `decision_id` | string | Caller-supplied new identifier. |
| `evaluated_at` | string | Caller-supplied ISO 8601 timestamp. |
| `evaluated_by` | string | Caller-supplied evaluator identity. |
| `gate_declaration` | GateDeclaration | The complete gate declaration. |
| `previous_decision` | GateDecision | Complete immutable GateDecision being overridden. |
| `override_authorization` | object | `{ intervention_id, authorized_by, authorized_at, reason }`. |
| `run_context` | object | `{ run_id, stage_id }`. |

**Optional fields:**

| Field | Type | Contract |
|---|---|---|
| `degradation_note` | string | Informational note. Not tied to execution_mode since override does not execute evaluation. |

**Forbidden fields:**

`evidence_envelope`, `evaluation_signal`, `execution_mode`, `previous_decision_snapshot`, `prior_evidence_ids`.

### 3.4 Discriminator Rules

1. The `request_kind` field is the sole and explicit discriminator. Branch identity is determined by its value, never by field-presence inference.
2. `request_kind` values are closed enumeration: `initial`, `reevaluation`, `override`. Any other value produces GateEvaluationError with error_code `invalid_input_branch`.
3. Any forbidden field present on a branch produces a GateEvaluationError with error_code `invalid_input_branch`.
4. Any required field missing on a branch produces a GateEvaluationError with error_code `invalid_input_branch`.
5. Evaluator copies caller-supplied fields verbatim. It never invents decision_id, evaluated_at, evaluated_by, execution_mode, or run_context.

## 4. Closed Evaluation Signal Union

### 4.1 Signal Shape by gate_type

`evaluation_signal` is a discriminated field whose shape is determined by `gate_declaration.gate_type`:

**gate_type = `validator`:**

| Field | Type | Required | Contract |
|---|---|---|---|
| `report_ref` | ArtifactRef | Yes | ArtifactRef to the Validation Report. |
| `overall_verdict` | string | Yes | The frozen five-value enum: `pass`, `fail`, `blocked`, `inconclusive`, `human_review_required`. |
| `failure_code` | string | Conditional | Caller-supplied. REQUIRED when `overall_verdict` != `pass`. FORBIDDEN when `pass`. Must be admissible for (outcome, evidence_classification) per the failure-code matrix (Section 14). The evaluator validates but NEVER selects among multiple candidates. |

**gate_type = `artifact_shape`:**

| Field | Type | Required | Contract |
|---|---|---|---|
| `outcome` | string | Yes | `pass` or `fail`. |
| `artifact_ref` | ArtifactRef | Yes | ArtifactRef to the shape-validated artifact. |
| `failure_code` | string | Conditional | Caller-supplied. REQUIRED when `outcome` != `pass`. FORBIDDEN when `pass`. Must be admissible per Section 14. |

**gate_type = `diff_review`:**

| Field | Type | Required | Contract |
|---|---|---|---|
| `outcome` | string | Yes | `pass` or `fail`. |
| `diff_ref` | ArtifactRef | Yes | ArtifactRef to the diff output. |
| `failure_code` | string | Conditional | Caller-supplied. REQUIRED when `outcome` != `pass`. FORBIDDEN when `pass`. Must be admissible per Section 14. |

**gate_type = `custom`:**

| Field | Type | Required | Contract |
|---|---|---|---|
| `outcome` | string | Yes | `pass`, `fail`, `blocked`, `inconclusive`, or `human_review_required`. |
| `custom_source_ref` | ArtifactRef | Yes | ArtifactRef to the custom evidence source. |
| `failure_code` | string | Conditional | Caller-supplied. REQUIRED when `outcome` != `pass`. FORBIDDEN when `pass`. Must be admissible per Section 14. |

### 4.2 Signal Processing Rules

1. The evaluator reads ONLY the closed structured fields of the signal. It MUST NOT dereference the artifact, read file content, inspect finding messages, or examine artifact body.
2. The outcome for the GateDecision is derived from the signal's structured outcome fields per Section 8.
3. An unrecognized gate_type value produces GateEvaluationError with error_code `unknown_gate_type`.
4. An unrecognized verdict or outcome value within a signal produces GateEvaluationError with the appropriate error code per Section 15.
5. For non-pass signals, the evaluator validates the caller-supplied `failure_code` against the failure-code matrix (Section 14). If the supplied code is not admissible for the (outcome, evidence_classification) pair, the evaluator produces GateEvaluationError with error_code `failure_code_invalid`. The evaluator NEVER selects a failure_code among multiple candidates.
6. For pass signals, presence of `failure_code` produces GateEvaluationError with error_code `invalid_input_branch`.
7. For non-pass signals, absence of `failure_code` produces GateEvaluationError with error_code `invalid_input_branch`.

### 4.3 Validator Report Dual-Reference Consistency

For `gate_type: validator` ONLY, the `evaluation_signal.report_ref` and `evidence_envelope.validation_report` ArtifactRef values MUST have identical identity. No other gate_type has this rule.

Identity comparison uses the triple `(artifact_id, artifact_kind, artifact_version)` from the ArtifactRef. The evaluator MUST NOT dereference or inspect either report artifact.

Mismatch rules:
- Both `report_ref` and `validation_report` present, identity triple matches -> proceed normally.
- One present, the other absent -> structural error (already handled by required field checks in Section 3 and Section 5).
- Both present, `artifact_id` mismatch -> GateEvaluationError `report_reference_mismatch`.
- Both present, `artifact_kind` mismatch -> GateEvaluationError `report_reference_mismatch`.
- Both present, `artifact_version` mismatch -> GateEvaluationError `report_reference_mismatch`.
- Any mismatch produces GateEvaluationError and MUST NOT produce a GateDecision.

## 5. GateEvidenceEnvelope Shape

### 5.1 Envelope Fields

| Field | Type | Required | Contract |
|---|---|---|---|
| `envelope_id` | string | Yes | Stable globally unique identifier. |
| `gate_id` | string | Yes | The gate this evidence supports. Must match gate_declaration.gate_id. |
| `primary_evidence` | array of ArtifactRef | Yes | Primary evidence artifacts. |
| `supporting_evidence` | array of ArtifactRef | No | Secondary or corroborating evidence. |
| `validation_report` | ArtifactRef | Conditional | Required when gate_type is `validator`. |
| `evidence_classification` | string | Yes | Closed enumeration: `complete`, `partial_recoverable`, `partial_absent`, `conflicted`. |
| `missing_evidence_description` | array of string | Conditional | Required when `evidence_classification` is not `complete`. |
| `collected_at` | string | Yes | ISO 8601 timestamp of evidence collection. |
| `collected_by` | string | Yes | Identity of evidence collector. |

### 5.2 Evidence Classification

| Classification | Meaning |
|---|---|
| `complete` | All required evidence is present and verified. |
| `partial_recoverable` | Some evidence is missing but can be obtained through re-dispatch or re-collection. |
| `partial_absent` | Required evidence does not exist and cannot be obtained. |
| `conflicted` | Evidence from two or more sources contradicts and cannot be resolved programmatically. |

The evaluator MUST NOT replace the supplied `evidence_classification` with its own inferred classification. The evaluator MUST NOT inspect artifact content, finding messages, failure descriptions, notes, or any free-text field to determine classification. Classification validation is structural-enum only: closed field presence, field type conformance, and verdict/enum compatibility.

## 6. Evidence Canonical Projection

### 6.1 Projection Algorithm

The GateDecision.evidence array is produced from the GateEvidenceEnvelope by this exact ordered algorithm:

1. Collect all ArtifactRef entries from `primary_evidence` in their declared order.
2. Append all ArtifactRef entries from `supporting_evidence` in their declared order.
3. If `validation_report` ArtifactRef is present and non-null, append it.
4. Deduplicate the combined list by ArtifactRef identity. The identity triple is `(artifact_id, artifact_kind, artifact_version)`. When two entries share the same identity triple, keep the first occurrence and discard subsequent duplicates.
5. Present the deduplicated list as the GateDecision.evidence array.

### 6.2 Projection Rules

1. Evaluator MUST NOT dereference any artifact. Identity comparison only.
2. Empty evidence rules are governed by the unified empty-evidence rule (Section 10).
3. If the evidence_classification is `complete` or `partial_recoverable` and the outcome is `pass` or `fail`, the projected evidence array MUST be non-empty. An empty array with such a combination produces a GateEvaluationError.
4. Deduplication uses identity triple only. Two ArtifactRef values with different artifact_version are distinct.
5. The canonical evidence is the projected array, never the raw envelope.

## 7. Gate Decision Shape

### 7.1 GateDecision Fields

| Field | Type | Required | Contract |
|---|---|---|---|
| `decision_id` | string | Yes | Copied from GateEvaluationRequest. |
| `gate_id` | string | Yes | Copied from gate_declaration.gate_id. |
| `outcome` | string | Yes | Closed enumeration: `pass`, `fail`, `blocked`, `inconclusive`, `human_review_required`. |
| `execution_mode` | string | Yes | initial/reevaluation: Copied from GateEvaluationRequest. override: Constant `full`. |
| `evidence` | array of ArtifactRef | Yes | initial/reevaluation: Canonical projection from GateEvidenceEnvelope (see Section 6). override: Immutable copy of `previous_decision.evidence`. |
| `recommendation` | string | Yes | Determined by recommendation matrix (Section 13). |
| `failure_code` | string | Conditional | Required when outcome is not `pass`. From failure-code matrix (Section 14). Copied from evaluation_signal (caller-supplied). |
| `failure_description` | string | Conditional | Required when outcome is not `pass`. Canonical template keyed by failure_code per Section 18. |
| `evaluated_at` | string | Yes | Copied from GateEvaluationRequest. |
| `evaluated_by` | string | Yes | Copied from GateEvaluationRequest. |
| `degradation_note` | string | Conditional | initial/reevaluation: Copied from GateEvaluationRequest when present. override: NOT APPLICABLE. |
| `previous_decision_id` | string | Conditional | reevaluation: Derived from `previous_decision_snapshot.decision_id`. override: Derived from `previous_decision.decision_id`. initial: NOT APPLICABLE. |
| `override_authorization` | object | Conditional | override: Copied from GateEvaluationRequest. initial/reevaluation: NOT APPLICABLE. |
| `run_context` | object | Yes | Copied from GateEvaluationRequest. |

### 7.2 Outcome Enumeration

| Outcome | Meaning |
|---|---|
| `pass` | Gate evaluated successfully. All required checks passed with complete evidence. |
| `fail` | Gate evaluated and found violations. Evidence of failure is present and conclusive. |
| `blocked` | Gate could not reach a definitive pass/fail verdict because evidence is recoverably missing or a dependency is unavailable. |
| `inconclusive` | Gate cannot be resolved programmatically. Evidence is insufficient, absent, or conflicted. |
| `human_review_required` | Evidence explicitly signals that Human review is mandatory. |

### 7.3 Execution Mode

| Mode | Meaning | Effect on outcome |
|---|---|---|
| `full` | Evaluation executed with all capabilities available. | No restrictions. |
| `degraded_transport` | Transport or dispatch mechanism was degraded. Correctness contract preserved. | MUST NOT change outcome. |
| `degraded_storage` | Storage or persistence mechanism was degraded. Correctness contract preserved. | MUST NOT change outcome. |

## 8. Signal-to-Decision Mapping

### 8.1 Validator Gate Type Mapping

For `gate_type: validator`, the GateDecision outcome is determined by the evaluation_signal.overall_verdict:

| overall_verdict | GateDecision outcome |
|---|---|
| `pass` | `pass` |
| `fail` | `fail` |
| `blocked` | `blocked` |
| `inconclusive` | `inconclusive` |
| `human_review_required` | `human_review_required` |

Unrecognized overall_verdict value produces GateEvaluationError with error_code `validator_verdict_mismatch`.

### 8.2 Artifact Shape Gate Type Mapping

For `gate_type: artifact_shape`, the GateDecision outcome is determined by the evaluation_signal.outcome:

| signal outcome | GateDecision outcome |
|---|---|
| `pass` | `pass` |
| `fail` | `fail` |

### 8.3 Diff Review Gate Type Mapping

For `gate_type: diff_review`, the GateDecision outcome is determined by the evaluation_signal.outcome:

| signal outcome | GateDecision outcome |
|---|---|
| `pass` | `pass` |
| `fail` | `fail` |

### 8.4 Custom Gate Type Mapping

For `gate_type: custom`, the GateDecision outcome is the evaluation_signal.outcome directly:

| signal outcome | GateDecision outcome |
|---|---|
| `pass` | `pass` |
| `fail` | `fail` |
| `blocked` | `blocked` |
| `inconclusive` | `inconclusive` |
| `human_review_required` | `human_review_required` |

### 8.5 Mapping Rules

1. Identity only: outcome is determined solely by the structured signal field. No evidence classification, finding analysis, confidence assessment, or message inspection changes the mapping.
2. No reinterpretation: `blocked` is `blocked`, not a candidate for `inconclusive`. `inconclusive` is `inconclusive`, not a candidate for `blocked`.
3. No splitting: A single signal MUST NOT produce two possible outcomes.
4. The evaluator MUST NOT inspect finding.message, failure_description, notes, artifact body, or any free-text/content field to determine outcome.
5. For validator gate type with required: true, GateDecision.outcome MUST be exactly identical to the evaluation_signal.overall_verdict value.

## 9. Evidence Completeness Rules

### 9.1 Classification Validation

Evidence classification is validated ONLY against closed structural-enum rules:

| overall_verdict or outcome | Valid evidence_classification |
|---|---|
| `pass` | `complete` |
| `fail` | `complete` |
| `blocked` | `partial_recoverable` |
| `inconclusive` | `partial_absent` OR `conflicted` |
| `human_review_required` | `partial_absent` OR `conflicted` OR `complete` OR `partial_recoverable` |

Any other combination produces a GateEvaluationError with error_code `invalid_evidence_classification` and MUST NOT produce a GateDecision.

The evaluator MUST NOT inspect finding.message, failure_description, notes, artifact body, or any free-text/content field when performing classification validation. All classification checks use only closed field presence, field type conformance, and verdict/enum compatibility.

### 9.2 Boundary Between invalid_evidence_classification and failure_code_invalid

- `invalid_evidence_classification`: The evidence_classification value does not pair with the verdict/outcome per the table in 9.1. This is strictly an enum-compatibility check. Example: a `pass` verdict paired with `partial_absent` classification.
- `failure_code_invalid`: The caller-supplied failure_code in the evaluation_signal is not admissible for the (outcome, evidence_classification) pair per the failure-code matrix (Section 14). This is strictly a code-admissibility check.

These error domains are non-overlapping and checked in sequence. Classification validation (position 6 in the error taxonomy) runs before failure_code validation (position 11). Each invalid input hits exactly one code.

## 10. Unified Empty-Evidence Rule

One rule governs evidence array emptiness across all outcome values:

| outcome | evidence rules |
|---|---|
| `pass` | Evidence array MUST be non-empty. |
| `fail` | Evidence array MUST be non-empty. |
| `blocked` | Evidence array MAY be empty (dispatch/artifact unavailable). |
| `inconclusive` | Evidence array MAY be empty. |
| `human_review_required` | Evidence array MAY be empty. |

This rule applies uniformly to the canonical evidence projection (Section 6). A pass or fail outcome with an empty evidence array from the projection algorithm produces a GateEvaluationError. Blocked, inconclusive, and human_review_required may produce empty evidence arrays.

## 11. Re-evaluation Protocol

### 11.1 New-Evidence Detection

A re-evaluation request carries `prior_evidence_ids` (array of ArtifactRef identity strings from the previous evaluation) and an `evidence_envelope` with new evidence.

Detection algorithm (zero state, zero query):
1. Compute `new_evidence_ids` from the current evidence_envelope using the canonical projection algorithm (Section 6).
2. Format each projected ArtifactRef as an identity string: `"{artifact_id}:{artifact_kind}:{artifact_version}"`.
3. Compare: if every entry in `new_evidence_ids` also exists in `prior_evidence_ids`, there is no new evidence.
4. If no new evidence exists, produce GateEvaluationError with error_code `reevaluation_no_new_evidence`.

This algorithm requires NO state lookup, NO event query, NO external database, NO artifact dereference. It operates only on the identity strings present in the request.

### 11.2 New-Evidence Re-evaluation

When new evidence is detected:
1. The evaluator maps the signal to outcome per Section 8.
2. Produces a new GateDecision with new decision_id, evaluated_at, evaluated_by from request.
3. Sets previous_decision_id to prior_decision.decision_id.
4. The original decision is referenced, not modified or deleted.

## 12. Override Protocol

### 12.1 Override Conditions

A gate MAY be overridden ONLY when ALL of:
1. `gate_declaration.required` is `false`.
2. `gate_declaration.allow_gate_override` is `true`.
3. `override_authorization` is present and complete: `{ intervention_id, authorized_by, authorized_at, reason }` with all fields non-empty.

### 12.2 Override Validation

Check in this exact order:
1. If `gate_declaration.required` is `true` -> GateEvaluationError with `invalid_override`.
2. If `gate_declaration.allow_gate_override` is not `true` -> GateEvaluationError with `invalid_override`.
3. If `override_authorization` is missing or incomplete -> GateEvaluationError with `invalid_override`.

Any condition failure produces GateEvaluationError. The previous decision remains current.

### 12.3 Valid Override Output

A valid override produces a new GateDecision:
- `decision_id`, `evaluated_at`, `evaluated_by`: copied from request
- `outcome`: `pass`
- `evidence`: evidence from previous_decision (unchanged)
- `recommendation`: `proceed`
- `override_authorization`: copied from request
- `previous_decision_id`: previous_decision.decision_id
- `execution_mode`: `full` (override does not execute evaluation)
- All other caller-supplied fields copied from request

Evidence is preserved from the previous decision. No new evidence is projected.

### 12.4 Override Rules

1. Override creates a new GateDecision with new decision_id. Original is preserved unchanged.
2. Override MUST NOT modify, delete, or rewrite the original GateDecision.
3. Override MUST NOT modify any referenced Validation Report.
4. Chain override: overriding an already-overridden decision creates another new decision referencing the most recent.
5. Required gate override, unauthorized override, disabled override -> GateEvaluationError. Previous decision remains current.

## 13. Recommendation Matrix

### 13.1 Complete Matrix

| outcome | required | failure_behavior | recommendation |
|---|---|---|---|
| `pass` | -- | -- | `proceed` |
| `blocked` | -- | -- | `more_evidence` |
| `inconclusive` | -- | -- | `human_intervention` |
| `human_review_required` | -- | -- | `human_intervention` |
| `fail` | -- | `halt_stage` | `stop_stage` |
| `fail` | -- | `halt_run` | `stop_run` |
| `fail` | -- | `require_intervention` | `human_intervention` |
| `fail` | `true` | `warn` | INVALID (routes to GateEvaluationError: required_gate_warn_behavior) |
| `fail` | `false` | `warn` | `proceed_with_warning` |

### 13.2 Recommendation Values

| Recommendation | Meaning |
|---|---|
| `proceed` | Gate satisfied. Stage may continue. |
| `more_evidence` | Evidence gap recoverable. Collect and re-evaluate. |
| `human_intervention` | Evidence gap requires Human judgment. |
| `stop_stage` | Stage cannot proceed. |
| `stop_run` | Run cannot proceed. Gate failure terminal. |
| `proceed_with_warning` | Gate satisfied with advisory warning. |

### 13.3 Recommendation Rules

1. Every valid (outcome, required, failure_behavior) maps to exactly ONE recommendation.
2. No "A or B" ambiguity.
3. Recommendation is advisory. It informs, does not bind.
4. Unsupported combinations produce GateEvaluationError with `recommendation_not_in_matrix`.
5. The `fail` + `required=true` + `warn` combination produces `required_gate_warn_behavior` error before reaching the recommendation matrix.

## 14. Failure-Code Matrix

### 14.1 Complete Matrix

Every non-pass GateDecision carries exactly one failure_code. Each failure_code maps to exactly ONE outcome. The caller supplies the failure_code in the evaluation_signal. The evaluator validates that the caller-supplied code is admissible for the (outcome, evidence_classification) pair. The evaluator NEVER selects among multiple candidates.

| outcome | evidence_classification | Failure codes (admissible) |
|---|---|---|
| `fail` | `complete` | `validator_fail_deterministic` |
| `blocked` | `partial_recoverable` | `evidence_incomplete`, `validator_unreachable`, `validator_report_missing`, `contract_unavailable`, `required_gate_blocked`, `degradation_blocked_correctness` |
| `inconclusive` | `partial_absent` | `evidence_absent_inconclusive`, `contract_insufficient_inconclusive`, `unsupported_judgment_inconclusive`, `truth_hierarchy_inconclusive` |
| `inconclusive` | `conflicted` | `evidence_conflict_inconclusive`, `contract_insufficient_inconclusive`, `unsupported_judgment_inconclusive`, `truth_hierarchy_inconclusive` |
| `human_review_required` | `partial_absent` | `evidence_absent_human`, `contract_insufficient_human`, `unsupported_judgment_human`, `truth_hierarchy_human` |
| `human_review_required` | `conflicted` | `evidence_conflict_human`, `contract_insufficient_human`, `unsupported_judgment_human`, `truth_hierarchy_human` |
| `human_review_required` | `complete` | `unsupported_judgment_human`, `truth_hierarchy_human` |
| `human_review_required` | `partial_recoverable` | `evidence_absent_human`, `contract_insufficient_human`, `unsupported_judgment_human`, `truth_hierarchy_human` |

### 14.2 Failure Code Definitions

Each code maps to exactly ONE outcome.

| Code | Outcome | Description |
|---|---|---|
| `validator_fail_deterministic` | `fail` | Validator found deterministic error-severity failures. |
| `validator_unreachable` | `blocked` | Independent Validator could not be dispatched or reached. |
| `validator_report_missing` | `blocked` | Validator dispatched but no report produced. |
| `evidence_incomplete` | `blocked` | Required evidence artifact missing but recoverable. |
| `contract_unavailable` | `blocked` | Required Validation Contract could not be resolved. |
| `required_gate_blocked` | `blocked` | Required gate cannot proceed due to unavailable dependency. |
| `degradation_blocked_correctness` | `blocked` | Execution mode degraded below correctness threshold. |
| `evidence_absent_inconclusive` | `inconclusive` | Required evidence does not exist and cannot be produced. |
| `evidence_conflict_inconclusive` | `inconclusive` | Evidence sources contradict. |
| `contract_insufficient_inconclusive` | `inconclusive` | Contract present but insufficient for deterministic verdict. |
| `unsupported_judgment_inconclusive` | `inconclusive` | Gate requires judgment the system cannot make. |
| `truth_hierarchy_inconclusive` | `inconclusive` | Validator used candidate output as truth source with no resolution. |
| `evidence_absent_human` | `human_review_required` | Required evidence does not exist and cannot be produced. Human review required. |
| `evidence_conflict_human` | `human_review_required` | Evidence sources contradict. Human review required. |
| `contract_insufficient_human` | `human_review_required` | Contract present but insufficient for deterministic verdict. Human review required. |
| `unsupported_judgment_human` | `human_review_required` | Gate requires judgment the system cannot make. Human review required. |
| `truth_hierarchy_human` | `human_review_required` | Validator used candidate output as truth source with no resolution. Human review required. |

### 14.3 Failure Code Rules

1. Every non-pass valid GateDecision has exactly ONE failure_code.
2. Each failure_code maps to exactly ONE outcome. No code maps to multiple outcomes.
3. Failure codes are closed enumeration. New codes require contract version bump.
4. The caller supplies the failure_code in the evaluation_signal. The evaluator validates it against the matrix using (outcome, evidence_classification) but NEVER selects.
5. Exactly one code per non-pass decision. Ambiguous or multiple codes produce GateEvaluationError with `failure_code_ambiguous`.
6. An inadmissible failure_code for the (outcome, evidence_classification) pair produces GateEvaluationError with `failure_code_invalid`.

## 15. GateEvaluationError Taxonomy

### 15.1 Ordered Error Codes with First-Match Precedence

Error evaluation proceeds in this exact order. First match wins. No overlap between codes.

**Structural checks (positions 0-1):** These run first, before any branch-specific semantic checks. They validate discriminator identity, field presence, and gate type enumeration. These groups cannot overlap with semantic checks.

0. `invalid_input_branch` -- checked FIRST. Discriminator/field violations: missing or forbidden field on a branch, invalid `request_kind` value (not one of `initial`, `reevaluation`, `override`), or evaluation_signal integrity violations (failure_code present when signal is pass, failure_code absent when signal is non-pass).
1. `unknown_gate_type` -- checked SECOND. `gate_declaration.gate_type` not in the closed enumeration (`validator`, `artifact_shape`, `diff_review`, `custom`).

**Semantic checks (positions 2-11):** These run after structural checks pass, in order.

2. `invalid_override` -- Gate is required, or allow_gate_override is not true, or override_authorization is missing/incomplete. Also covers override_authorization field violations.
3. `reevaluation_no_new_evidence` -- Re-evaluation request has no new evidence not present in prior_evidence_ids.
4. `required_gate_warn_behavior` -- Gate with `required: true` and `failure_behavior: warn`.
5. `report_reference_mismatch` -- For validator gate_type ONLY: evaluation_signal.report_ref and evidence_envelope.validation_report have non-identical ArtifactRef identity triples.
6. `invalid_evidence_classification` -- Evidence classification value not in closed enum, or classification/verdict pairing not in compatibility table (Section 9.1).
7. `validator_verdict_mismatch` -- Validator overall_verdict value is not a recognized five-value enum value.
8. `outcome_not_in_enum` -- Computed outcome is not in the five-value closed enumeration.
9. `recommendation_not_in_matrix` -- Computed (outcome, required, failure_behavior) not in recommendation matrix.
10. `failure_code_ambiguous` -- Multiple failure codes map or no single code resolves.
11. `failure_code_invalid` -- The caller-supplied failure_code is not admissible for the (outcome, evidence_classification) pair per the failure-code matrix (Section 14).

### 15.2 Error Shape

| Field | Type | Required | Contract |
|---|---|---|---|
| `decision_id` | string \| null | Yes | Copied from evaluation request when structurally present and valid. `null` when unextractable from request. |
| `gate_id` | string \| null | Yes | Copied from gate_declaration.gate_id when gate_declaration is structurally present and valid. `null` when gate_declaration is missing or malformed. |
| `error_code` | string | Yes | One of the error codes from taxonomy above. Always present, derived from taxonomy. |
| `error_description` | string | Yes | Canonical template keyed by error_code (see Section 18). Always present. |
| `run_context` | object \| null | Yes | Copied from evaluation request when structurally present and valid. `null` when unextractable. |

### 15.3 Error Identity Field Extraction Rule

For `decision_id`, `gate_id`, and `run_context` fields: if the field is structurally present and valid in the request, copy verbatim. If missing, malformed, or unextractable, use explicit `null`. The evaluator MUST NEVER generate fallback IDs, empty strings, default gate IDs, default run context, timestamps, or random values.

### 15.4 Error Rules

1. GateEvaluationError is deterministic: same invalid input produces same error, same error_code, same error_description, and same null pattern for identity fields.
2. GateEvaluationError is not a GateDecision. It has no outcome, recommendation, or failure_code.
3. Upon error, the evaluator MUST NOT produce a GateDecision. Exactly one: GateDecision XOR GateEvaluationError, never both, never neither.
4. Error is evidence for lifecycle authority.
5. GateEvaluationError uses only request-bound identity fields. No evaluator-generated identity or timestamp.
6. When GateEvaluationError occurs, the current decision for gate_id is unchanged.

## 16. Normative Field-Origin Crosswalk

### 16.1 GateDecision Fields

| Output Field | Origin: initial | Origin: reevaluation | Origin: override |
|---|---|---|---|
| `decision_id` | Copied from GateEvaluationRequest | Copied from GateEvaluationRequest | Copied from GateEvaluationRequest |
| `gate_id` | Copied from gate_declaration.gate_id | Copied from gate_declaration.gate_id | Copied from gate_declaration.gate_id |
| `outcome` | Derived from evaluation_signal per Section 8 | Derived from evaluation_signal per Section 8 | Constant `pass` per Section 12.3 |
| `execution_mode` | Copied from GateEvaluationRequest | Copied from GateEvaluationRequest | Constant `full` |
| `evidence` | Canonical projection from GateEvidenceEnvelope per Section 6 | Canonical projection from GateEvidenceEnvelope per Section 6 | Immutable copy of `previous_decision.evidence` |
| `recommendation` | Recommendation matrix lookup per Section 13 | Recommendation matrix lookup per Section 13 | Constant `proceed` per Section 12.3 |
| `failure_code` | Copied from evaluation_signal (caller-supplied) | Copied from evaluation_signal (caller-supplied) | NOT APPLICABLE (outcome is `pass`) |
| `failure_description` | Canonical template keyed by failure_code per Section 18 | Canonical template keyed by failure_code per Section 18 | NOT APPLICABLE (outcome is `pass`) |
| `evaluated_at` | Copied from GateEvaluationRequest | Copied from GateEvaluationRequest | Copied from GateEvaluationRequest |
| `evaluated_by` | Copied from GateEvaluationRequest | Copied from GateEvaluationRequest | Copied from GateEvaluationRequest |
| `degradation_note` | Copied from GateEvaluationRequest when present | Copied from GateEvaluationRequest when present | NOT APPLICABLE |
| `previous_decision_id` | NOT APPLICABLE | Derived from `previous_decision_snapshot.decision_id` | Derived from `previous_decision.decision_id` |
| `override_authorization` | NOT APPLICABLE | NOT APPLICABLE | Copied from GateEvaluationRequest |
| `run_context` | Copied from GateEvaluationRequest | Copied from GateEvaluationRequest | Copied from GateEvaluationRequest |

### 16.2 GateEvaluationError Fields

| Output Field | Origin |
|---|---|
| `decision_id` | Copied from GateEvaluationRequest when structurally present and valid. `null` when unextractable. |
| `gate_id` | Copied from gate_declaration.gate_id when gate_declaration is present and valid. `null` when gate_declaration is missing or malformed. |
| `error_code` | Derived: first-match from error taxonomy per Section 15. Always present. |
| `error_description` | Derived: canonical template keyed by error_code per Section 18. Always present. |
| `run_context` | Copied from GateEvaluationRequest when structurally present and valid. `null` when unextractable. |

### 16.3 Prohibited Origins

No output field of GateDecision or GateEvaluationError may originate from:
- Clock or wall-clock time computation
- Random or generated identifier creation
- Filesystem discovery or file reading
- Environment variable or configuration lookup
- Model inference or heuristic text interpretation
- Event query, database lookup, or state retrieval
- Artifact dereference or content inspection
- Hidden state or mutable store reference

## 17. Full Field-Level Truth Table

### 17.1 Gate Type: validator

| request_kind | Input Condition | Produces |
|---|---|---|
| `initial` | All required fields present; signal overall_verdict valid; classification matches table 9.1; failure_code admissible for (outcome, classification); evidence array rules satisfied | GateDecision with mapped outcome |
| `initial` | Signal overall_verdict = `pass` + classification != `complete` | GateEvaluationError: invalid_evidence_classification |
| `initial` | Signal overall_verdict = `fail` + classification != `complete` | GateEvaluationError: invalid_evidence_classification |
| `initial` | Signal overall_verdict unrecognized (not in five-value enum) | GateEvaluationError: validator_verdict_mismatch |
| `initial` | pass outcome + empty evidence array | GateEvaluationError: invalid_evidence_classification |
| `initial` | required=true + failure_behavior=warn | GateEvaluationError: required_gate_warn_behavior |
| `initial` | validator gate_type: report_ref and validation_report ArtifactRef identity triples differ | GateEvaluationError: report_reference_mismatch |
| `initial` | Non-pass overall_verdict + failure_code not admissible for (outcome, classification) | GateEvaluationError: failure_code_invalid |
| `initial` | Non-pass overall_verdict + failure_code missing from evaluation_signal | GateEvaluationError: invalid_input_branch |
| `initial` | Pass overall_verdict + failure_code present in evaluation_signal | GateEvaluationError: invalid_input_branch |
| `reevaluation` | All re-eval fields present; signal valid; report_ref and validation_report identity match; new evidence detected; failure_code admissible | GateDecision with previous_decision_id |
| `reevaluation` | validator gate_type: report_ref and validation_report ArtifactRef identity triples differ | GateEvaluationError: report_reference_mismatch |
| `reevaluation` | No new evidence relative to prior_evidence_ids | GateEvaluationError: reevaluation_no_new_evidence |
| `reevaluation` | override_authorization present (forbidden on re-eval) | GateEvaluationError: invalid_input_branch |
| `override` | all override fields present; gate optional; override authorized | GateDecision with outcome=pass, evidence from previous_decision, execution_mode=full |
| `override` | gate.required = true | GateEvaluationError: invalid_override |
| `override` | gate.allow_gate_override != true | GateEvaluationError: invalid_override |
| `override` | override_authorization missing or incomplete | GateEvaluationError: invalid_override |
| `override` | evidence_envelope present (forbidden on override) | GateEvaluationError: invalid_input_branch |
| `override` | evaluation_signal present (forbidden on override) | GateEvaluationError: invalid_input_branch |
| `override` | execution_mode present (forbidden on override) | GateEvaluationError: invalid_input_branch |
| any | request_kind value not in closed enum (`initial`, `reevaluation`, `override`) | GateEvaluationError: invalid_input_branch |
| any | gate_declaration.gate_type not in closed enum | GateEvaluationError: unknown_gate_type |
| any | Forbidden field present on branch | GateEvaluationError: invalid_input_branch |
| any | Required field missing on branch | GateEvaluationError: invalid_input_branch |

### 17.2 Gate Type: artifact_shape

| request_kind | Input Condition | Produces |
|---|---|---|
| `initial` | All fields present; signal outcome = pass; classification = complete; failure_code absent from signal | GateDecision: outcome=pass |
| `initial` | All fields present; signal outcome = fail; classification = complete; failure_code admissible | GateDecision: outcome=fail + failure_code from signal |
| `initial` | Signal outcome unrecognized (not pass/fail) | GateEvaluationError: outcome_not_in_enum |
| `initial` | pass outcome + empty evidence | GateEvaluationError (empty evidence for pass) |
| `initial` | classification not matching verdict per 9.1 | GateEvaluationError: invalid_evidence_classification |
| `initial` | fail outcome + failure_code not admissible | GateEvaluationError: failure_code_invalid |
| `initial` | fail outcome + failure_code missing | GateEvaluationError: invalid_input_branch |
| `initial` | pass outcome + failure_code present | GateEvaluationError: invalid_input_branch |
| `reevaluation` | New evidence detected; signal valid; failure_code admissible | GateDecision with previous_decision_id |
| `reevaluation` | No new evidence | GateEvaluationError: reevaluation_no_new_evidence |
| `override` | Gate optional; authorized; valid | GateDecision: outcome=pass, execution_mode=full |
| `override` | Gate required or unauthorized | GateEvaluationError: invalid_override |
| any | request_kind value not in closed enum | GateEvaluationError: invalid_input_branch |
| any | gate_type not in closed enum | GateEvaluationError: unknown_gate_type |
| any | Branch field violation | GateEvaluationError: invalid_input_branch |

### 17.3 Gate Type: diff_review

| request_kind | Input Condition | Produces |
|---|---|---|
| `initial` | All fields present; signal outcome = pass; classification = complete; failure_code absent from signal | GateDecision: outcome=pass |
| `initial` | All fields present; signal outcome = fail; classification = complete; failure_code admissible | GateDecision: outcome=fail + failure_code from signal |
| `initial` | Signal outcome unrecognized | GateEvaluationError: outcome_not_in_enum |
| `initial` | fail outcome + failure_code not admissible | GateEvaluationError: failure_code_invalid |
| `initial` | fail outcome + failure_code missing | GateEvaluationError: invalid_input_branch |
| `initial` | pass outcome + failure_code present | GateEvaluationError: invalid_input_branch |
| `reevaluation` | New evidence detected; signal valid; failure_code admissible | GateDecision with previous_decision_id |
| `reevaluation` | No new evidence | GateEvaluationError: reevaluation_no_new_evidence |
| `override` | Gate optional; authorized | GateDecision: outcome=pass, execution_mode=full |
| `override` | Gate required or unauthorized | GateEvaluationError: invalid_override |
| any | request_kind value not in closed enum | GateEvaluationError: invalid_input_branch |
| any | gate_type not in closed enum | GateEvaluationError: unknown_gate_type |
| any | Branch field violation | GateEvaluationError: invalid_input_branch |

### 17.4 Gate Type: custom

| request_kind | Input Condition | Produces |
|---|---|---|
| `initial` | All fields present; signal outcome valid (five values); classification matches table 9.1; failure_code admissible for non-pass | GateDecision with mapped outcome |
| `initial` | Signal outcome = pass/fail + classification not complete | GateEvaluationError: invalid_evidence_classification |
| `initial` | Signal outcome unrecognized | GateEvaluationError: outcome_not_in_enum |
| `initial` | Non-pass outcome + failure_code not admissible | GateEvaluationError: failure_code_invalid |
| `initial` | Non-pass outcome + failure_code missing | GateEvaluationError: invalid_input_branch |
| `initial` | Pass outcome + failure_code present | GateEvaluationError: invalid_input_branch |
| `reevaluation` | New evidence detected; signal valid; failure_code admissible | GateDecision with previous_decision_id |
| `reevaluation` | No new evidence | GateEvaluationError: reevaluation_no_new_evidence |
| `override` | Gate optional; authorized | GateDecision: outcome=pass, execution_mode=full |
| `override` | Gate required or unauthorized | GateEvaluationError: invalid_override |
| any | request_kind value not in closed enum | GateEvaluationError: invalid_input_branch |
| any | gate_type not in closed enum | GateEvaluationError: unknown_gate_type |
| any | Branch field violation | GateEvaluationError: invalid_input_branch |

### 17.5 Truth Table Rules

1. Every cell in the complete table (4 gate_types * 3 request_kinds) produces exactly one of: GateDecision XOR GateEvaluationError.
2. No cell produces both outputs. No cell produces neither output.
3. Malformed input (wrong discriminator fields) routes to `invalid_input_branch` error. When the request is malformed such that identity fields (`decision_id`, `gate_id`, `run_context`) are unextractable, the resulting GateEvaluationError uses explicit `null` for those fields per Section 15.3.
4. Incompatible input (field values that violate enums, tables, or invariants) routes to the error taxonomy with first-match precedence.
5. Structural checks (request_kind validation, field presence/absence, gate_type enumeration) always run before semantic checks.
6. Failure code validation runs after outcome and classification are determined, before the GateDecision is produced.

## 18. failure_description and error_description Provenance

### 18.1 failure_description

The `failure_description` field on a non-pass GateDecision has exactly one origin:

1. **Canonical template keyed by failure_code**: The evaluator selects the frozen template string from the table below (Section 18.3).

The evaluator MUST NOT generate, synthesize, or derive failure_description from artifact content, finding messages, free-text fields, or any request field that does not exist in the contract.

### 18.2 error_description

The `error_description` field on a GateEvaluationError has exactly one origin:

1. **Canonical template keyed by error_code**: The evaluator selects the frozen template string from the table below.

The evaluator MUST NOT generate, synthesize, or derive error_description from artifact content, finding messages, or free-text fields.

Identity fields `decision_id`, `gate_id`, and `run_context` may be `null` when unextractable from a malformed request (see Section 15.3). The error_description template does not reference or depend on these identity fields.

### 18.3 Canonical Template Table

Canonical descriptions are frozen strings:

| Code (error or failure) | Canonical description |
|---|---|
| `invalid_override` | Gate override is invalid. Required gates cannot be overridden, or the gate does not allow override, or authorization is missing or incomplete. |
| `reevaluation_no_new_evidence` | Re-evaluation request contains no evidence not present in the prior evaluation. |
| `required_gate_warn_behavior` | Gate with required=true cannot use warn failure_behavior. |
| `report_reference_mismatch` | For validator gate_type, the evaluation_signal.report_ref and evidence_envelope.validation_report ArtifactRef identity triples do not match. |
| `invalid_evidence_classification` | The evidence classification value is not recognized or does not pair with the current verdict per the compatibility table. |
| `validator_verdict_mismatch` | The validator overall_verdict value is not a recognized five-value enumeration value. |
| `outcome_not_in_enum` | The computed outcome is not in the five-value closed enumeration. |
| `recommendation_not_in_matrix` | The (outcome, required, failure_behavior) combination is not in the recommendation matrix. |
| `failure_code_ambiguous` | Multiple failure codes apply or no single code resolves for this outcome and classification. |
| `failure_code_invalid` | The caller-supplied failure_code is not admissible for the (outcome, evidence_classification) pair per the failure-code matrix. |
| `invalid_input_branch` | The request contains a forbidden field for its branch, is missing a required field, or has an invalid request_kind value. |
| `unknown_gate_type` | The gate_type value is not one of the recognized closed enumeration values (validator, artifact_shape, diff_review, custom). |
| `validator_fail_deterministic` | Validator found deterministic error-severity failures. |
| `validator_unreachable` | Independent Validator could not be dispatched or reached. |
| `validator_report_missing` | Validator was dispatched but no report was produced. |
| `evidence_incomplete` | Required evidence artifact is missing but recoverable. |
| `evidence_absent_inconclusive` | Required evidence artifact does not exist and cannot be produced. |
| `evidence_conflict_inconclusive` | Evidence from two or more independent sources contradicts. |
| `contract_unavailable` | Required Validation Contract could not be resolved. |
| `contract_insufficient_inconclusive` | Validation Contract is present but insufficient for a deterministic verdict. |
| `unsupported_judgment_inconclusive` | Gate requires a judgment the evaluation system cannot make. |
| `truth_hierarchy_inconclusive` | Validator used candidate output as truth source with no resolution path. |
| `required_gate_blocked` | Required gate cannot proceed due to unavailable dependency. |
| `degradation_blocked_correctness` | Execution mode degraded below the correctness threshold. |
| `evidence_absent_human` | Required evidence artifact does not exist and cannot be produced. Human review required. |
| `evidence_conflict_human` | Evidence from two or more independent sources contradicts. Human review required. |
| `contract_insufficient_human` | Validation Contract is present but insufficient for a deterministic verdict. Human review required. |
| `unsupported_judgment_human` | Gate requires a judgment the evaluation system cannot make. Human review required. |
| `truth_hierarchy_human` | Validator used candidate output as truth source with no resolution path. Human review required. |

## 19. Decision Precedence

### 19.1 Precedence Rules

1. GateDecision computation is a pure function. It does not select history, filter decisions, or establish precedence.
2. Runtime State append order is authoritative for determining the current decision.
3. Re-evaluation appends a new decision referencing previous_decision_id.
4. Override appends a new decision referencing the overridden decision.
5. Original decisions are preserved unchanged.
6. GateEvaluationError does not append or modify any decision.

## 20. Separation from Runtime Lifecycle

GateDecision computation is a pure function of its inputs. It does not:
- Append events to the runtime event stream
- Initiate, authorize, or execute retry or resume
- Schedule or dispatch Validator execution
- Modify stage state or run status
- Record review results or make lifecycle decisions
- Write workflow state, tickets, or epics
- Create, modify, or delete artifacts
- Communicate with external systems
- Discover files on filesystem
- Apply heuristic text parsing, model judgment, or natural language interpretation

## 21. Consumer Crosswalk

### 21.1 Runtime State Sidecar

Consumes GateDeclaration, GateDecision, outcome, evidence, failure codes. Validates gate decisions against required-gate invariants. Appends gate events. Maintains append-order precedence. Preserves decision immutability, override audit trail, evidence completeness.

### 21.2 Architect and Planner Review

Consumes GateDecision outcome, recommendation, failure_code, failure_description. GateDecision is evidence only. Lifecycle authority is never delegated to gate evaluation.

## 22. Version and Governance

| Field | Value |
|---|---|
| Contract version | 2.2.0 |
| Governing contract | `references/runtime-architecture.md` v0.8.0 (frozen) |
| Frozen reference | `references/validator-protocol.md` v0.7.0 (frozen) |
| Frozen reference | `references/runtime-state-contract.md` v0.9.0 (frozen) |
| Risk level | high |
| Validator required | true |
| Visibility | public |

Precision remediation of v2.1.0 contract defects. Changes include: set-equal crosswalk/shape removal of request_kind (Fix A), branch-specific field origins for previous_decision_id, execution_mode, evidence, override_authorization, and degradation_note (Fix B), nullable identity fields for GateEvaluationError when the request is malformed (Fix C), validator report dual-reference consistency with report_reference_mismatch error (Fix D), and synchronized truth tables and templates.

## 23. Public Hygiene

### 23.1 Visibility

This contract is `visibility: public`. All schemas, examples, and cross-references use public-safe values. No secrets, credentials, tokens, machine-local paths, proprietary configuration, or internal ticket/epic identifiers appear in this document. All content is ASCII-only.

### 23.2 OSS Boundary

All concepts, schemas, and contracts defined here are implementable with open-source tooling. No concept requires a commercial license, proprietary service, or paid API for correctness. No content path relies on proprietary classification, model judgment, or black-box reasoning.

### 23.3 Content Constraints

- All text is ASCII-only. No non-ASCII characters.
- No heuristic text parsing, model judgment, or natural language interpretation rules.
- No filesystem discovery, event append, retry/resume behavior, scheduling, or lifecycle authority.
- No proprietary inference, black-box classification, or opaque decision logic.
- No internal ticket IDs, Control paths, local machine paths, or platform-specific agent names in the body.
- No artifact dereference, file reading, or content inspection in evaluation logic.
