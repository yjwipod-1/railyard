---
name: validator-protocol
description: Generic Validator protocol definition for read-only quality gates, severity/status semantics, source-to-derived reconciliation, and missing mapping policy
type: protocol
version: 0.7.0
---

# Validator Protocol

This document defines the generic Validator protocol for read-only quality gates,
severity/status independence, source-to-derived reconciliation patterns,
and missing-mapping behavior. It is intended to be copied into a Validator
dispatch so that an Architect or CI pipeline can invoke a Validator with
a well-defined contract.

## 0. Caller Dispatch Boundary

Validator evidence is independent only when it is produced by a Validator role execution.

`scripts/validate_artifacts.py` is artifact-shape validation. It may validate
the presence and structure of ticket gate metadata, Validation Contracts, and
Validation Reports, but its output is not a Validator role report and cannot
satisfy a ticket that declares `validator_required: true`. Runner verification
scripts and caller self-review are also not independent Validator evidence.

If an Architect or Planner cannot dispatch a Validator subagent, or if dispatch appears to start but no Validator output can be retrieved, the caller MUST NOT implement Validator checks inside the caller session. The caller emits the exact spawn-ready Validator prompt and payload, then stops the Validator step until a Human or external Validator session returns a report.

The caller MUST NOT create temporary validation scripts, ad hoc validators, direct shell validators, or replacement tooling to simulate the Validator. The caller MUST NOT label self-run checks as a Validator report.

Explicit Human-authorized role collapse is the only exception. Role-collapsed output must be labeled as role-collapsed evidence, not an independent Validator report.

## 1. Validator Role and Boundaries

A Validator is a **read-only quality gate**. It inspects artifacts and produces
a Validation Report. It does not modify the system under inspection.

### What the Validator MUST NOT do

- Do not modify, create, or delete files.
- Do not create or update tickets or epics.
- Do not write workflow state, inbox/outbox files, or any workflow control
  artifacts.
- Do not commit, push, or perform any version-control operation.
- Do not perform remediation, auto-fix, retry, or automated repair.
- Do not route or assign work.
- Do not make lifecycle decisions. Architect and Planner retain all
  acceptance, rejection, and closure authority.

### What the Validator MAY do

- Read artifact files referenced in the input slots.
- Run read-only commands listed in `allowed_read_only_commands`.
- Produce a structured Validation Report as its sole output.

### Contract Handling

The Validator receives the Validation Contract from the caller (Architect or Planner). The Validator treats the contract as read-only input.

- The Validator MUST NOT modify, extend, or redefine the contract. The contract is the caller's authoritative specification of what must be checked.
- The Validator MUST NOT write the contract back to disk, create a new contract artifact, or update the ticket metadata.
- When the contract is sufficient, the Validator applies every rule and produces findings.
- When the contract is missing, incomplete, or too vague to produce a deterministic verdict, the Validator MUST NOT return `status=pass` for the uncovered scope. Allowed outcomes are `inconclusive`, `blocked`, or `human_review_required` with `missing_evidence` describing the gap.
- The Validator MUST NOT invent acceptance criteria, field mappings, or check logic that the caller did not supply. If the caller did not supply enough information, the Validator reports the gap.

For the full ownership and handoff protocol, see `references/validation-contract.md`.

### Remediation Ownership

The Validator produces evidence only. All remediation, fixes, and lifecycle
decisions are owned by consumer roles. The Validator does not participate in
any action beyond report production.

| Verdict | Validator action (ends here) | Remediation owner | Consumer action |
|---|---|---|---|
| `pass` | Report clean findings | None required | Architect reviews independently; Planner assesses closure |
| `fail` | Report error findings with evidence | Runner (fix errors); Architect (reject/redispatch) | Architect records `review_result=reject` (Runner fixes) or `review_result=redesign` (approach flawed) |
| `blocked` | Report blocked findings with gap description | Architect (resolve blocker); Human (grant permission) | Architect collects missing evidence or escalates; Runner does not retry |
| `inconclusive` | Report inconclusive findings with evidence gaps | Architect (provide contract/evidence); Planner (follow-up) | Architect provides missing evidence or escalates for high-risk tickets |
| `human_review_required` | Report findings requiring Human judgment | Human (provide judgment) | Architect stops review and escalates; no automated role substitutes |

**Key rules:**

- The Validator does not retry, re-execute, or produce follow-up reports
  unless explicitly re-dispatched by an authorized consumer.
- Non-pass verdicts do not authorize automatic repair. A `fail` verdict means
  someone must fix the errors; it does not mean the Validator should fix them.
- The `recommended_next_action` field in the Validation Report is advisory.
  It informs the consumer but does not bind or override consumer judgment.
- For the complete no-remediation boundary including case-by-case rules and
  bounded consumer actions, see
  `references/validator-verdict-handoff-tree.md` Section No-Remediation Boundary.

## 2. Validator Input Slots

A Validator invocation MUST supply the following input slots.

| Slot | Type | Description |
|---|---|---|
| `artifacts` | array of objects | Each object has `path` (string) and `kind` (string, e.g. `source`, `derived`, `contract`, `evidence`). |
| `validation_contract` | object or null | The Validation Contract to apply. See `references/validation-contract.md`. Optional when `acceptance_criteria` is supplied. |
| `acceptance_criteria` | object or null | A simplified acceptance-criteria object used when a full Validation Contract is not available. |
| `evidence_pack` | object or null | Supporting evidence the caller provides (command outputs, extracted fields, reference values). |
| `risk_level` | string | One of `low`, `medium`, `high`. Determines how strictly the Validator treats missing or ambiguous evidence. |
| `allowed_read_only_commands` | array of strings | Commands the Validator is authorized to run. All commands must be read-only; no write, commit, push, or lifecycle operations. |

### Example input (generic)

```json
{
  "artifacts": [
    { "path": "data/source.csv", "kind": "source" },
    { "path": "data/derived.csv", "kind": "derived" },
    { "path": "contracts/mapping.json", "kind": "contract" }
  ],
  "validation_contract": { "contract_id": "source-to-derived-reconciliation", "version": "0.7.0", "rules": [] },
  "evidence_pack": null,
  "risk_level": "high",
  "allowed_read_only_commands": ["cat", "head", "diff", "python -c 'print(...)']
}
```

## 3. Validator Output JSON Shape

The Validator MUST produce a single Validation Report object with these fields:

The output MUST be one JSON object only. Do not return Markdown tables, prose-only summaries, uppercase verdict values, numeric confidence values, or alternate field names. A caller may request a separate human-readable summary only after the JSON object is produced.

| Field | Type | Description |
|---|---|---|
| `validator_role` | string | Always `"validator"`. |
| `contract_id` | string | The contract or criteria set that was evaluated. |
| `contract_version` | string | Version string of the evaluated contract. |
| `overall_verdict` | string | One of `pass`, `fail`, `blocked`, `inconclusive`, `human_review_required`. |
| `confidence` | string | One of `high`, `medium`, `low`. |
| `artifact_summary` | object | Summary of artifacts examined, keyed by artifact path. |
| `findings` | array of objects | Per-rule evaluation results. See section 4. |
| `missing_evidence` | array of objects | Evidence items the Validator expected but could not locate. |
| `recommended_next_action` | string | The recommended next step for the Architect or caller. |
| `validated_artifacts` | array of strings | Paths of artifacts that were successfully evaluated. |
| `commands_run` | array of strings | Exact read-only commands the Validator executed. |
| `notes` | string or null | Optional free-text notes from the Validator. |

Strict value rules:

- `validator_role` MUST be exactly `"validator"` in lowercase.
- `overall_verdict` MUST be lowercase and one of `pass`, `fail`, `blocked`, `inconclusive`, `human_review_required`.
- `confidence` MUST be one of `high`, `medium`, `low`; numeric values such as `0.75` or `1.0` are invalid.
- `artifact_summary` MUST be an object, not a string.
- `findings` MUST be an array.
- `missing_evidence` MUST be an array. Use an empty array when no evidence is missing.
- `validated_artifacts` MUST be an array of strings.
- `commands_run` MUST be an array of strings. Use an empty array when no commands were run.
- `notes` MUST be a string or null.

### 3.1 Verifiable Validator Report Reference Record

When a ticket declares `validator_required: true`, `mark-review-result accept`
or `accept_with_changes` receives a JSON reference record with this minimal
generic contract:

```json
{
  "record_type": "railyard.validator_report_reference.v1",
  "ticket_id": "SYSTEM-001",
  "validator_role": "validator",
  "independence": "independent",
  "producer_identity": "validator-session-001",
  "report_path": "evidence/SYSTEM-001.validator-report.json",
  "report_sha256": "<64 hex characters>",
  "created_at": "2026-01-01T00:00:00+00:00"
}
```

The lifecycle helper verifies that the record exists, matches the ticket,
declares an independent Validator producer, resolves to an existing report,
matches the report SHA-256, and references a structurally valid Validator
Protocol report. Only `overall_verdict: pass` permits acceptance.
`fail`, `blocked`, `inconclusive`, and `human_review_required` reject
acceptance. The verified record path, report path, SHA-256, producer identity,
and verdict are preserved in the review workflow event.

Artifact-shape output, Runner verification or result JSON, Architect
self-review, and role-collapsed evidence do not satisfy this independent record
contract.

### Finding object fields

| Field | Type | Description |
|---|---|---|
| `rule_id` | string | Identifier of the evaluated rule. |
| `severity` | string | One of `error`, `warn`, `info`. |
| `status` | string | One of `pass`, `fail`, `not_applicable`, `blocked`, `inconclusive`. |
| `message` | string | Human-readable description. |
| `evidence` | string or null | The specific data that led to the finding, if available. |

Finding strict value rules:

- Every finding MUST include `rule_id`, `severity`, `status`, `message`, and `evidence`.
- `severity` MUST be lowercase and one of `error`, `warn`, `info`.
- `status` MUST be lowercase and one of `pass`, `fail`, `not_applicable`, `blocked`, `inconclusive`.
- Use `evidence: null` only when no specific evidence exists; otherwise provide the concrete observed value, path, command output, or source reference.

Minimal valid output:

```json
{
  "validator_role": "validator",
  "contract_id": "example-contract",
  "contract_version": "0.7.0",
  "overall_verdict": "fail",
  "confidence": "high",
  "artifact_summary": {
    "example/source.json": { "kind": "source", "status": "read" },
    "example/derived.json": { "kind": "derived", "status": "read" }
  },
  "findings": [
    {
      "rule_id": "example-rule",
      "severity": "error",
      "status": "fail",
      "message": "Derived value does not match the declared source mapping.",
      "evidence": "expected source field A, observed derived field B"
    }
  ],
  "missing_evidence": [],
  "recommended_next_action": "return to caller with remediation evidence",
  "validated_artifacts": ["example/source.json", "example/derived.json"],
  "commands_run": [],
  "notes": null
}
```

## 4. Verdict Semantics

### 4.1 Overall Verdict Values

| Verdict | Meaning |
|---|---|
| `pass` | All `error`-severity rules passed. No `error` findings with `status=fail`. No `status=blocked` and no `status=inconclusive` findings. |
| `fail` | At least one `severity=error` AND `status=fail` finding exists. |
| `blocked` | At least one finding has `status=blocked` and the check could not run. |
| `inconclusive` | At least one finding has `status=inconclusive` and evidence is insufficient. |
| `human_review_required` | Findings exist that require manual review before a verdict can be assigned. The reason SHOULD be present in `findings`, `missing_evidence`, or `notes`. |

**Important:** `warn` is NOT an overall verdict value. Warnings are expressed
via finding `severity=warn` combined with a `status`.

For the canonical handoff tree mapping each verdict to explicit Architect,
Planner, Runner, Validator, and Human responsibilities -- including whether
acceptance is allowed, whether remediation/more evidence/redesign is needed,
and whether Human escalation is required -- see
`references/validator-verdict-handoff-tree.md`.

### 4.2 Candidate Output Must Never Be the Truth Source

The candidate output (derived artifact, generated artifact, transformed output)
MUST NEVER be used as the truth source for expected values. Expected values
come from the truth hierarchy defined in Section 5.

If a Validator rule compares a derived value against another derived value
without an independent source, the rule MUST report `status=inconclusive`
or `status=fail` depending on policy, NOT `status=pass`.

Example:

```json
{
  "rule_id": "field-value-match",
  "severity": "error",
  "status": "inconclusive",
  "message": "No independent source available to verify derived_value; cannot confirm expected value.",
  "evidence": "source_value missing; derived_value='42'; no mapping contract found"
}
```

### 4.3 Unsupported Pass Refusal

If a field or check cannot be mapped to independent source evidence, the
Validator MUST NOT return `status=pass` for that field. Allowed outcomes are
`inconclusive`, `fail`, or `human_review_required` according to the
applicable policy and `risk_level`.

## 5. Truth Hierarchy

When resolving expected values, the Validator MUST follow this hierarchy:

1. **Explicit validation contract / field mapping contract** -- declared
   transformations, expected formulas, and field-to-field mappings.
2. **Source artifact headers, metadata, schemas, and official documentation** --
   type declarations, constraints, and documented semantics.
3. **Raw source artifact values** -- the actual values in the source data.
4. **Candidate implementation** -- the code or process that produced the derived
   artifact.
5. **Candidate output** -- the derived or generated artifact itself.

Higher-ranked sources override lower-ranked sources. The candidate output
(least authoritative) MUST NEVER be treated as the truth source for expected
values.

## 6. Severity / Status Independence

`severity` and `status` are **independent dimensions**. The combination must
be interpreted correctly:

| severity | status | Meaning |
|---|---|---|
| `error` | `pass` | Error-severity rule was checked and no error was found. |
| `error` | `fail` | An error-level issue was found. **Forces `overall_verdict=fail`.** |
| `error` | `not_applicable` | The rule does not apply to this artifact. |
| `error` | `blocked` | The error rule could not run. |
| `error` | `inconclusive` | Not enough evidence to determine the error rule. |
| `warn` | `pass` | The warning rule was checked and **no warning issue was found**. |
| `warn` | `fail` | A non-blocking warning issue was found. **Does NOT force `overall_verdict=fail` unless the contract declares warnings-as-errors.** |
| `warn` | `not_applicable` | The warning rule does not apply to this artifact. |
| `warn` | `blocked` | The warning rule could not run. |
| `warn` | `inconclusive` | Warning rule evidence is insufficient. |

### Common Misinterpretation to Avoid

- `severity=warn, status=fail` does NOT mean "the issue is non-blocking so
  ignore it." It means a warning-level issue was detected. Whether it affects
  the overall verdict depends on the contract's `warnings_as_errors` policy.
- `severity=warn, status=pass` does NOT mean "a warning issue was found but
  is non-blocking." It means the warning rule was evaluated and found clean.

### Overall Verdict Computation

```
overall_verdict = fail
    IF any finding has severity=error AND status=fail

overall_verdict = blocked
    IF no error/fail findings but any finding has status=blocked

overall_verdict = human_review_required
    IF no error/fail and no blocked findings exist, and contract or findings require human review

overall_verdict = inconclusive
    IF no error/fail, no blocked findings, and no human_review_required trigger exists,
    but any finding has status=inconclusive

overall_verdict = pass
    IF no error/fail, no blocked, no inconclusive findings, and no human_review_required trigger
    NOTE: warn+fail findings are allowed under overall_verdict=pass unless
          the contract sets warnings_as_errors=true
```

## 7. Source-to-Derived Reconciliation Pattern

This is a **generic** pattern for reconciling derived artifacts against source
artifacts. It applies to data transforms, data ingestion, data migration,
generated artifacts, or constrained output validation.

For the canonical catalog of all deterministic validation primitives across
source replay, field mapping, value/sign preservation, formula recompute,
record/key reconciliation, availability handling, claim grounding, and
publish gate aggregation, see `references/validation-primitive-registry.md`.

### 7.1 Pattern Rules

Each rule below is generic and can be parameterized by field names, record keys,
and transformation type.

#### rule_id: `candidate_output_not_truth_source`

- **severity**: `error`
- **description**: The candidate output must never be used as the source of
  truth for expected values. Expected values come from the truth hierarchy.
- **check**: When resolving expected values for any field, prefer
  `validation_contract`, `source_artifact`, or `evidence_pack`. Reject
  comparisons where both sides are derived.

#### rule_id: `field_mapping_required`

- **severity**: `error`
- **description**: Every derived field must have an explicit mapping to a
  source field or a declared transformation rule.
- **check**: For each field in the derived artifact, verify that a mapping
  entry exists in the contract or evidence_pack.

#### rule_id: `value_transform_correctness`

- **severity**: `error`
- **description**: Source values that are declared as preserved must appear
  unchanged in the derived artifact (modulo declared transformations).
- **check**: For each source field marked `preserved` in the mapping, verify
  that the derived value matches the source value after applying the declared
  transformation.

#### rule_id: `signed_numeric_preservation`

- **severity**: `error`
- **description**: Numeric values with sign information must preserve sign
  through the transformation. A negative source value must not become positive
  in the derived artifact unless the contract explicitly declares a sign
  flip operation.
- **check**: For each numeric field, verify that sign is preserved when no
  sign-flip transformation is declared.

#### rule_id: `field_identity_preservation`

- **severity**: `warn`
- **description**: Derived field names must be traceable to source field names
  through the mapping. Unexpected new fields in the derived artifact should
  trigger a warning unless declared in the contract.
- **check**: Every field in the derived artifact must be explainable by a
  mapping from a source field or a declared transformation.

#### rule_id: `record_identity_preservation`

- **severity**: `error`
- **description**: Each source record (identified by its key) must have a
  corresponding derived record. Record counts and key sets must reconcile.
- **check**: For each record key in the source, verify a corresponding derived
  record exists. Flag orphaned derived records.

#### rule_id: `declared_transform_only`

- **severity**: `warn`
- **description**: Only transformations declared in the contract or mapping
  should be applied. Undeclared transformations are non-conforming.
- **check**: Compare the actual transformation logic (from evidence_pack or
  candidate implementation analysis) against the declared transformations.

#### rule_id: `unmapped_field_availability`

- **severity**: `warn`
- **description**: When a derived field has no mapping to an independent
  source, the Validator applies the missing mapping policy (see Section 7.2).
- **check**: For each unmapped derived field, apply the policy and set the
  finding status accordingly.

#### rule_id: `warning_policy`

- **severity**: `warn`
- **description**: Evaluates whether the set of warning-level findings should
  escalate the overall verdict based on the contract's `warnings_as_errors`
  setting.
- **check**: If `warnings_as_errors` is `true` in the contract, escalate
  any `warn+fail` finding to affect the overall verdict. Otherwise,
  `warn+fail` findings are recorded but do not force `overall_verdict=fail`.

### 7.2 Missing Mapping Policy

When a derived field has no mapping to an independent source, the Validator
applies one of the following policies:

| Policy value | Behavior |
|---|---|
| `inconclusive` | The field is marked `status=inconclusive`. The overall verdict is `inconclusive` if no other evidence resolves it. |
| `fail` | The field is marked `status=fail`. If `risk_level=high` or the field is critical, the overall verdict is `fail`. |
| `human_review_required` | The field is flagged for manual review. The Validator sets `overall_verdict=human_review_required` and records the reason in `findings`, `missing_evidence`, or `notes`. |

**Recommendation for high-risk tasks:** For high-risk source-to-derived
transformations (e.g., data migration, financial data transforms), recommend
`human_review_required` or `fail` as the missing mapping policy. Never use
`inconclusive` as a default for high-risk tasks with critical fields.

### 7.3 Generic Example

```json
{
  "validator_role": "validator",
  "contract_id": "source-to-derived-reconciliation",
  "contract_version": "0.7.0",
  "overall_verdict": "inconclusive",
  "confidence": "medium",
  "artifact_summary": {
    "data/source.csv": { "kind": "source", "records": 3, "fields": ["id", "source_value", "optional_label"] },
    "data/derived.csv": { "kind": "derived", "records": 3, "fields": ["id", "derived_value", "optional_label"] }
  },
  "findings": [
    {
      "rule_id": "candidate_output_not_truth_source",
      "severity": "error",
      "status": "pass",
      "message": "No derived-only comparison detected; truth sources are available.",
      "evidence": null
    },
    {
      "rule_id": "field_mapping_required",
      "severity": "error",
      "status": "fail",
      "message": "Derived field 'derived_value' has no mapping to source field 'source_value'.",
      "evidence": "missing mapping for derived_value"
    },
    {
      "rule_id": "value_transform_correctness",
      "severity": "error",
      "status": "inconclusive",
      "message": "Cannot verify value transform correctness for 'source_value' because no mapping contract was found.",
      "evidence": "no mapping contract at contracts/mapping.json"
    },
    {
      "rule_id": "unmapped_field_availability",
      "severity": "warn",
      "status": "inconclusive",
      "message": "Missing mapping for 'derived_value' applied policy 'inconclusive' per contract. For high-risk tasks, consider 'fail' or 'human_review_required'.",
      "evidence": "policy=inconclusive; risk_level=medium"
    }
  ],
  "missing_evidence": [
    {
      "expected": "field mapping contract for derived_value -> source_value",
      "impact": "Cannot verify value preservation"
    }
  ],
  "recommended_next_action": "Provide a field mapping contract that declares the transformation from source_value to derived_value, or escalate for manual review.",
  "validated_artifacts": ["data/source.csv", "data/derived.csv"],
  "commands_run": ["cat data/source.csv", "cat data/derived.csv", "head data/derived.csv"],
  "notes": "Missing mapping policy applied as 'inconclusive'. Consider upgrading to 'fail' or 'human_review_required' for high-risk tasks."
}
```

## 8. Severity/Status Independence -- Explicit Examples

### Example 1: warn+fail does NOT force overall_verdict=fail

Contract without `warnings_as_errors`:

```json
{
  "findings": [
    { "rule_id": "field-identity", "severity": "warn", "status": "fail", "message": "Unexpected field 'extra_col' in derived artifact." }
  ],
  "overall_verdict": "pass"
}
```

This is valid because `warnings_as_errors` is not declared.

### Example 2: warn+pass means no warning issue found

```json
{
  "findings": [
    { "rule_id": "field-identity", "severity": "warn", "status": "pass", "message": "No unexpected fields found in derived artifact." }
  ],
  "overall_verdict": "pass"
}
```

This does NOT mean "a warning was found but is non-blocking." It means
the warning rule was checked and found clean.

### Example 3: warn+fail DOES force overall_verdict=fail when warnings_as_errors is true

```json
{
  "contract": { "warnings_as_errors": true },
  "findings": [
    { "rule_id": "field-identity", "severity": "warn", "status": "fail", "message": "Unexpected field 'extra_col'." }
  ],
  "overall_verdict": "fail"
}
```

### Example 4: error+fail always forces overall_verdict=fail

```json
{
  "findings": [
    { "rule_id": "field-mapping-required", "severity": "error", "status": "fail", "message": "No mapping for derived field." }
  ],
  "overall_verdict": "fail"
}
```

## 9. Validator Invocation Example

```text
Validator input:
  artifacts: source.csv (source), derived.csv (derived), mapping.json (contract)
  validation_contract: source-to-derived-reconciliation v0.7.0
  evidence_pack: null
  risk_level: high
  allowed_read_only_commands: ["cat", "head", "diff"]

Validator output:
  See Section 7.3 for the expected JSON shape.
```

### Development-time reference implementation

The repository includes a minimal executable reference for source-to-derived
field-mapping validation:

```powershell
python scripts\validator.py --input <validator-input.json> [--output <report.json>]
```

This script reads only the input JSON and referenced artifacts, applies the
field mapping contract, and emits a Validation Report JSON. It supports
`identity`, `multiply_by_2`, `parse_integer`,
`parse_number_preserve_sign`, `missing_mapping_policy`, and
`warnings_as_errors`. It does not perform workflow lifecycle writes,
automatic repair, runtime orchestration, model routing, or business-specific
rules.

## 10. Planner-Side Validator Input Slots

The Planner-side Validator is invoked for pre-closure or release readiness assessment. The Planner constructs these additional input slots beyond the generic Validator input:

| Slot | Type | Description |
|---|---|---|
| `epic_scope` | string | Epic scope definition and done definition |
| `ticket_state_table` | array of objects | Current state of all scoped tickets (status, runner_result, review_result) |
| `runner_results` | array of objects | Runner result JSONs from completed tickets |
| `architect_review_results` | array of objects | Architect review results and review focus notes |
| `changed_files_summary` | array of objects | Summary of changed files since last review |
| `validation_command_outputs` | array of strings | Output from validation commands (compileall, validate_artifacts.py, etc.) |
| `public_hygiene_scan` | array of objects | Evidence from public artifact scan (README, CHANGELOG, SKILL, examples) |
| `unresolved_blockers` | array of strings | Any unresolved blockers or follow-ups |

### Planner verdict-to-action mapping

| Validator `overall_verdict` | Planner action |
|---|---|
| `pass` | May close epic or proceed release after Planner judgment |
| `fail` | Open follow-up ticket or block closure |
| `blocked` | Collect missing evidence before deciding |
| `inconclusive` | Request more evidence; do not close high-risk epic |
| `human_review_required` | Stop; await Human decision |

**Canonical handoff reference:** The single authoritative handoff tree covering
who acts next, acceptance/closure permissions, remediation, evidence,
redesign, blocked handling, and Human escalation for every verdict is defined
in `references/validator-verdict-handoff-tree.md`. This table is the
Planner-specific view derived from that tree.

### Validator report effect

- The Validator report is **Planner evidence only**, NOT closure authority.
- The Validator report does NOT close epic, record lifecycle, or replace Planner judgment.
- The Planner still closes epic through the epic helper.
- The Validator report informs but does not dictate the decision.

### Scope exclusions

The Planner-side Validator does NOT handle:
- Runner-side Validator self-check role
- Runtime orchestration
- Automatic repair or remediation
- Model routing or automatic dispatch
- Business-specific rules or content policy checks

## 11. Confidence and Human Escalation Matrix

This section defines the mapping from Validator confidence levels and overall
verdicts to Human escalation actions. The matrix governs when an Architect may
record acceptance and when a Planner may close an epic based on Validator
evidence.

### Escalation Tiers

Three escalation tiers determine the required action:

- **Mandatory**: The Architect or Planner MUST stop and escalate to the Human.
  No acceptance or closure decision may be recorded until the Human provides
  judgment.
- **Recommended**: The Architect or Planner SHOULD escalate or seek
  corroborating evidence. If proceeding without escalation, the decision
  rationale MUST be documented in the review or closure notes.
- **Optional**: The Architect or Planner MAY proceed without escalation. Normal
  review and closure processes apply.

### Confidence Levels

Confidence expresses the Validator's certainty in its findings:

- **high**: Findings are reliable. Source evidence is complete, deterministic
  checks executed fully, and the truth hierarchy was satisfied. The Validator
  has sufficient evidence to produce a definitive verdict.
- **medium**: Findings are indicative but some evidence is incomplete or
  inferred. Deterministic checks covered the primary scope but secondary scope
  relied on partial evidence. Corroboration may be needed for critical
  decisions.
- **low**: Findings are uncertain. Significant evidence gaps exist,
  deterministic checks could not execute fully, or the truth hierarchy was
  violated (e.g., candidate output used as truth source). Human review is
  expected for any non-pass verdict.

### Confidence and Verdict Escalation Matrix

| confidence | overall_verdict | Escalation tier | Architect action | Planner action |
|---|---|---|---|---|
| high | pass | Optional | May accept after independent scope/diff review. | May close epic after Planner judgment. |
| high | fail | Optional | Reject or redispatch Runner. No escalation needed; deterministic failure is actionable. | Open follow-up ticket or block closure. No escalation needed. |
| high | blocked | Recommended | Collect missing evidence. If evidence cannot be collected, escalate to Human. | Collect missing evidence before deciding closure. |
| high | inconclusive | Recommended | Provide missing contract/evidence or escalate for high-risk tickets. | Request more evidence; do not close high-risk epic without escalation. |
| high | human_review_required | Mandatory | Stop and request Human decision before recording review result. | Stop and await Human decision before closure. |
| medium | pass | Optional | May accept after independent review. | May close epic after Planner judgment. |
| medium | fail | Optional | Reject or redispatch. Corroborate failure evidence if the finding is unexpected. | Open follow-up ticket or block closure. Verify failure evidence. |
| medium | blocked | Recommended | Collect missing evidence. Escalate if evidence cannot be collected or the blockage is critical. | Collect missing evidence. Escalate for high-risk epics. |
| medium | inconclusive | Recommended | Provide missing contract/evidence. Escalate if the inconclusive scope affects acceptance criteria. | Request more evidence. Do not close high-risk epic without escalation. |
| medium | human_review_required | Mandatory | Stop and escalate to Human. | Stop and await Human decision. |
| low | pass | Recommended | May accept, but document rationale for accepting low-confidence evidence. | May close epic, but document rationale. |
| low | fail | Recommended | Reject or redispatch. Corroborate failure evidence; low-confidence failures may be false positives. | Open follow-up ticket. Verify failure evidence before blocking closure. |
| low | blocked | Mandatory | Stop and escalate. Low-confidence blocked findings require Human judgment to resolve. | Stop and escalate. Do not close with low-confidence blocked findings. |
| low | inconclusive | Mandatory | Stop and escalate. Low-confidence inconclusive findings cannot support acceptance. | Stop and escalate. Do not close with low-confidence inconclusive findings. |
| low | human_review_required | Mandatory | Stop and escalate. | Stop and await Human decision. |

### Matrix Application Rules

1. **Mandatory escalation overrides all other actions.** When the tier is
   Mandatory, the Architect or Planner must stop and escalate regardless of
   other evidence.
2. **Escalation is not acceptance.** Escalating to Human means the decision is
   deferred to Human judgment. It does not mean acceptance is recorded.
3. **Recommended escalation with documentation exception.** If the Architect or
   Planner proceeds without escalation when the tier is Recommended, the
   review or closure notes must state why escalation was not needed and what
   corroborating evidence supported the decision.
4. **Low-confidence pass is not a strong signal.** A `low` confidence `pass`
   verdict does not mean the artifact is clean; it means the Validator could
   not find errors but also could not fully verify correctness. The Architect
   should treat it as inconclusive evidence for critical scope.
5. **Cross-reference from Architect and Planner tables.** The Architect review
   decision table (Section 7.5 of `references/startup-sequence.md` and
   `references/roles.md` Section Architect-to-Validator) and the Planner
   verdict-to-action table (Section 10 of this document) are authoritative for
   their respective roles. This escalation matrix supplements those tables by
   adding the confidence dimension. When this matrix mandates escalation, the
   Architect or Planner MUST comply even if the base table would permit
   proceeding.

### Relationship to Semantic Validation

Semantic validation findings (see `references/validation-contract.md` Semantic
Validation Boundary section) typically produce `medium` or `low` confidence
because they involve inference rather than deterministic checks. As a result,
non-pass semantic findings frequently trigger Recommended or Mandatory
escalation under this matrix. This is expected: semantic findings are
escalation signals, not deterministic verdicts.

## 12. Extension Notes

- This protocol is generic and not tied to any business domain.
- Future contracts may add domain-specific rules, but they MUST respect the
  truth hierarchy (Section 5) and severity/status independence rules
  (Section 6).
- The Validator protocol extends the Validation Contract foundation defined
  in `references/validation-contract.md` by adding input/output schemas,
  verdict computation rules, and source-to-derived reconciliation patterns.
- The Confidence and Human Escalation Matrix (Section 11) governs when
  Human judgment is required based on confidence and verdict. Architect
  and Planner decision tables in this document and in
  `references/startup-sequence.md` and `references/roles.md` are supplemented
  by this matrix.
- Semantic validation boundary and deterministic precedence rules are defined
  in `references/validation-contract.md` Semantic Validation Boundary section.
  Semantic inference findings typically produce medium or low confidence and
  may trigger escalation under Section 11.
