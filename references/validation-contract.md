# Validation Contract

v0.7 introduces a generic, development-time-first validation contract foundation for Railyard. It defines how a declared Validation Contract is applied to an artifact and produces a structured Validation Report, without embedding any project-specific business rules and without adding runtime orchestration.

## Design Goals

- Generic: no encoding of a specific business domain, data schema, or content policy.
- Development-time first: the primary use case is validating Railyard artifacts (tickets, epics, results, queues) during authoring or CI.
- Read-only Validator: validation reports findings only. Architect and Planner retain all lifecycle decisions.
- Extension boundary: future external or runtime-adjacent validation is explicitly acknowledged as an extension, not implemented in this foundation ticket; later queued validation work may extend the same contract model.

## Core Concepts

### Validation Contract

A Validation Contract is a declarative specification that describes what checks must be satisfied by a target artifact. Each contract is generic and self-contained.

Required fields:

- `contract_id`: a stable identifier for the contract (e.g., `v0.7-artifact-schema`).
- `version`: a semver-like version string for the contract itself.
- `description`: a human-readable summary of what the contract validates.
- `applies_to`: a list of artifact kind strings the contract targets (e.g., `ticket`, `epic`, `result`, `queue`, or generic kinds such as `document`, `schema`, `config`).
- `rules`: an ordered array of rule objects, each describing a single check.

Rule object fields:

- `rule_id`: a stable identifier for the rule.
- `description`: a human-readable summary of the rule.
- `severity`: one of `error`, `warn`, or `info`.
- `check`: an object describing the check logic:
  - `type`: the check type (e.g., `required_field`, `enum_value`, `pattern_match`, `non_empty`, `type_check`).
  - `path`: a dot-separated path to the field or target within the artifact (optional for root-level checks).
  - `expected`: the expected value, pattern, set, or type, depending on `type` (optional).

Example:

```json
{
  "contract_id": "v0.7-artifact-schema",
  "version": "0.7.0",
  "description": "Generic structural validation for the validation_contract artifact kind.",
  "applies_to": ["validation_contract"],
  "rules": [
    {
      "rule_id": "has-contract-id",
      "description": "A contract must have a non-empty contract_id.",
      "severity": "error",
      "check": {
        "type": "non_empty",
        "path": "contract_id"
      }
    },
    {
      "rule_id": "has-version",
      "description": "A contract must have a non-empty version.",
      "severity": "error",
      "check": {
        "type": "non_empty",
        "path": "version"
      }
    }
  ]
}
```

### Validation Report

A Validation Report is the structured output produced when a Validation Contract is applied to one or more artifacts.

Required fields:

- `contract_id`: the contract that was applied.
- `contract_version`: the version of the applied contract.
- `results`: an array of per-artifact validation results.

Per-artifact result object fields:

- `artifact_path`: the relative file path of the validated artifact.
- `artifact_kind`: the kind of artifact (e.g., `ticket`, `epic`, `result`).
- `overall_verdict`: one of `pass`, `fail`, `blocked`, `inconclusive`, or `human_review_required`.
  - `pass`: all `error`-severity rules passed; no `error` findings.
  - `fail`: at least one `error`-severity rule failed.
  - `blocked`: at least one rule or artifact was blocked and could not be evaluated.
  - `inconclusive`: not enough information to determine pass or fail.
  - `human_review_required`: findings require manual review before a verdict can be assigned.
  - Note: `warn` is not an overall verdict value. Warnings are expressed via finding `severity` instead.
- `findings`: an array of finding objects for each rule evaluation.

Finding object fields:

- `rule_id`: the rule that was evaluated.
- `severity`: the severity of the rule (`error`, `warn`, or `info`).
- `status`: one of `pass`, `fail`, `not_applicable`, `blocked`, or `inconclusive`.
  - `pass`: the check succeeded.
  - `fail`: the check did not succeed.
  - `not_applicable`: the rule does not apply to this artifact (e.g., the target field is absent).
  - `blocked`: the check could not be executed due to a missing dependency or error.
  - `inconclusive`: the check result cannot be determined with available information.
  - Note: `warn` is not a finding status value. Warnings are expressed via finding `severity`.
- `message`: a human-readable description of the finding.
- `evidence`: an optional string containing the specific data that led to the finding (e.g., the actual field value, the error message, or a reference to the artifact location).

Example:

```json
{
  "contract_id": "v0.7-artifact-schema",
  "contract_version": "0.7.0",
  "results": [
    {
      "artifact_path": "examples/validation-contract-example/contract.json",
      "artifact_kind": "validation_contract",
      "overall_verdict": "pass",
      "findings": [
        {
          "rule_id": "has-contract-id",
          "severity": "error",
          "status": "pass",
          "message": "contract_id is present and non-empty.",
          "evidence": "v0.7-artifact-schema"
        }
      ]
    }
  ]
}
```

### Validator

A Validator is the component (script, function, or process) that applies a Validation Contract to one or more artifacts and produces a Validation Report.

Boundary rules:

- The Validator is read-only by default. It inspects artifacts and produces reports; it does not modify artifacts, create tickets, close epics, or make lifecycle decisions.
- The Validator does not own ticket review or epic closure. These remain Architect and Planner responsibilities as defined in `references/lifecycle.md`.
- The Validator does not perform automatic repair, retry, or remediation. When a finding is `fail`, the report records the finding; fixing the artifact is a separate step performed by the ticket owner.
- The Validator does not route or assign work. It does not interact with the workflow database or the mailbox files beyond reading artifact content.
- The Validator does not implement runtime orchestration. It has no awareness of ticket state, claim status, or review queue.

### Validator Protocol and Input/Output Contract

For the complete Validator protocol including input slots, output JSON shape, verdict computation, truth hierarchy, severity/status independence rules, source-to-derived reconciliation patterns, and missing mapping policy, see `references/validator-protocol.md`. That document provides a copyable Validator session contract that Architects and CI pipelines can use to dispatch Validator invocations.

### Severity and Status Semantics

The Validation Contract and Validation Report use `severity` and `status` as independent dimensions:

- `severity` (`error`, `warn`, `info`) expresses the importance of the rule.
- `status` (`pass`, `fail`, `not_applicable`, `blocked`, `inconclusive`) expresses whether the check succeeded for this artifact.

Key rules:

- `severity=error, status=fail` forces `overall_verdict=fail`.
- `severity=warn, status=fail` does NOT force `overall_verdict=fail` unless the contract declares `warnings_as_errors: true`.
- `severity=warn, status=pass` means the warning rule was checked and no warning issue was found. It does NOT mean a warning issue was found.
- `warn` is not an overall verdict value; it is a finding severity only.

For detailed examples and the overall verdict computation algorithm, see `references/validator-protocol.md` Section 6.

### Contract-Level Optional Knobs

Validation Contracts MAY include these optional generic knobs:

- `warnings_as_errors` (boolean, optional): When `true`, any finding with `severity=warn` AND `status=fail` escalates the `overall_verdict` to `fail`. Default is `false`.
- `missing_mapping_policy` (string, optional): When a derived field has no mapping to an independent source, this policy determines the finding status. Allowed values: `inconclusive`, `fail`, `human_review_required`. For high-risk source-to-derived tasks, the Validator recommends `human_review_required` or `fail` over `inconclusive`.

### Source-to-Derived Reconciliation Pattern

For generic source-to-derived reconciliation patterns (field mapping requirements, source value preservation, record identity, missing mapping policy), see `references/validator-protocol.md` Section 7. This pattern is designed to be generic across data transforms, ingestion, migration, generated artifacts, and constrained output validation.

For the canonical catalog of all deterministic validation primitives, including source replay, field mapping, value/sign preservation, formula recompute, record/key reconciliation, availability handling, claim grounding, and publish gate aggregation, see `references/validation-primitive-registry.md`.

### Field Mapping Contract Shape

The field mapping contract is a generic framework-level contract shape for source-to-derived field reconciliation. It is not tied to any specific data format, domain, or business logic.

#### Root-level fields

| Field | Type | Required | Description |
|---|---|---|---|
| `contract_id` | string | Yes | Stable identifier for the field mapping contract. |
| `version` | string | Yes | Semver-like version string for the contract itself. |
| `applies_to` | string | Yes | Target artifact kind (e.g., `source`, `derived`, `mapping`, `report`). |
| `validation_scope` | enum | Yes | One of: `extract_only`, `transform_only`, `ingest_to_db`. This prevents misjudging extract-only workflows as failures for missing DB write. |
| `record_selector` | object or null | No | Filter expression to select specific records for validation. |
| `field_mappings` | array | Yes | Array of field mapping objects. |
| `derived_field` | object | Yes | The primary derived field being validated, with expected_transform and optional preserve_sign. |
| `source_path` | string | Yes | Path to the source artifact file or location. |
| `source_header` | string or null | No | Source artifact header/metadata for schema reference. |
| `source_description` | string or null | No | Human-readable description of the source artifact. |
| `missing_mapping_policy` | enum | No | One of: `inconclusive`, `fail`, `human_review_required`. Default is `inconclusive`. For high-risk source-to-derived tasks, the Validator recommends `fail` or `human_review_required`. |
| `warnings_as_errors` | boolean | No | When `true`, any `severity=warn` AND `status=fail` finding escalates the `overall_verdict` to `fail`. Default is `false`. |

#### Field mapping object fields

Each element in `field_mappings` must include:

| Field | Type | Required | Description |
|---|---|---|---|
| `source_path` | string | Yes | Dot-separated path to the source field. |
| `derived_path` | string | Yes | Dot-separated path to the derived field. |
| `transform` | string | Yes | Declared transformation name (e.g., `identity`, `multiply`, `to_uppercase`). |
| `required` | boolean | No | Whether the mapping is required. Default is `true`. |
| `preserve_sign` | boolean | No | When `true`, negative source values must remain negative in derived output. Default is `false`. |

#### Core Principles

1. **Candidate output is NOT the truth source.** Expected values must come from the truth hierarchy (Section 5 of `references/validator-protocol.md`): validation contract / field mapping contract > source artifact headers, metadata, schemas > raw source values > candidate implementation > candidate output.

2. **Source-to-derived validation must prefer explicit field mapping contract.** When a field mapping contract exists, the Validator uses it as the primary source for expected values and transform rules.

3. **Missing mapping in high-risk contexts must fail or require human review.** For high-risk source-to-derived tasks, `missing_mapping_policy` should be `fail` or `human_review_required`, never a silent `inconclusive` or pass.

4. **`warn+fail` does not auto-fail unless `warnings_as_errors=true`.** The contract-level `warnings_as_errors` knob controls escalation. Default `false` means warnings are non-blocking.

#### Example

```json
{
  "contract_id": "field-mapping-example",
  "version": "0.1.0",
  "applies_to": "mapping",
  "validation_scope": "transform_only",
  "record_selector": null,
  "source_path": "data/source.json",
  "source_description": "Generic source artifact",
  "field_mappings": [
    {
      "source_path": "record_id",
      "derived_path": "record_id",
      "transform": "identity",
      "required": true,
      "preserve_sign": false
    },
    {
      "source_path": "source_amount",
      "derived_path": "derived_amount",
      "transform": "identity",
      "required": true,
      "preserve_sign": true
    }
  ],
  "derived_field": {
    "derived_path": "derived_amount",
    "source_path": "source_amount",
    "expected_transform": "identity",
    "preserve_sign": true
  },
  "missing_mapping_policy": "fail",
  "warnings_as_errors": false
}
```

#### Test Fixtures

Generic, public-safe test fixtures are provided under `examples/field_mapping_contract_fixtures/`:

| Fixture | Description | Expected Verdict |
|---|---|---|
| `fixture-valid-mapping.json` | Valid source-to-derived mapping with all fields correctly mapped. | `pass` |
| `fixture-missing-mapping.json` | Derived field with no source mapping; policy=fail. | `fail` |
| `fixture-wrong-mapping.json` | Derived field mapped to wrong source field/type. | `fail` |
| `fixture-sign-flip.json` | Signed numeric with unexpected sign flip; preserve_sign=true. | `fail` |
| `fixture-undeclared-transform.json` | Undeclared transform applied (warn-level only). | `pass` (warn+fail, warnings_as_errors=false) |
| `fixture-warn-policy.json` | Warn+fail with warnings_as_errors=false. | `pass` |
| `fixture-warn-policy-errors.json` | Warn+fail with warnings_as_errors=true. | `fail` |

## v0.7 First Application

The first application of the validation contract in v0.7 is development-time validation of Railyard artifacts:

- Tickets, epics, result files, and queue examples that ship with the repository.
- CI or pre-commit checks that run the artifact validator before merging.

The validation contract is designed to be extended. Future versions may accept caller-supplied external artifacts (e.g., user project tickets, data schemas, or configuration files). This extension path is documented as an explicit boundary: external or runtime-adjacent validation is not implemented in this foundation ticket; later queued v0.7 work may extend the same generic contract model.

## Extension Boundary

The v0.7 foundation deliberately excludes the following from scope. These are acknowledged as future extension paths:

- Business-specific rules (e.g., finance field validation, content policy checks, data quality rules).
- Runtime governance (e.g., live ticket state validation, API response validation).
- Automatic repair or remediation (e.g., auto-fixing failed fields).
- External artifact ingestion beyond what is strictly necessary for development-time artifact validation.
- Data transformation or enrichment.

When these capabilities are needed, they should be implemented as new contracts and new validator implementations that reference this foundation document for consistency.

## Semantic Validation Boundary

Semantic validation refers to checks that evaluate logical consistency,
cross-artifact coherence, domain-level correctness, or meaning-based properties
beyond structural and deterministic field-level constraints. In the Railyard
framework, semantic validation is distinct from and subordinate to deterministic
validation.

### Scope

Semantic validation MAY evaluate:

- Logical consistency between related artifacts (e.g., a ticket's acceptance
  criteria should not contradict its epic's done definition).
- Cross-artifact coherence (e.g., referenced ticket IDs exist and are in a
  compatible state).
- Domain-level plausibility (e.g., numeric values fall within reasonable
  ranges derived from artifact context).
- Assertion traceability (e.g., claims in a result file are supported by
  evidence in referenced artifacts).

### Deterministic Precedence Rule

Deterministic validation always takes precedence over semantic inference.
When a deterministic check (structural, field-mapping, value-preservation,
formula-recompute, record-key reconciliation, or any primitive from the
validation primitive registry) produces a finding that contradicts a semantic
inference, the deterministic finding prevails. Semantic findings that conflict
with deterministic results are downgraded to advisory or escalation signals;
they do not override deterministic verdicts.

The precedence hierarchy is:

1. **Deterministic primitive findings** -- highest authority. Structural
   checks, field mappings, value comparisons, formula recomputes, record
   reconciliations, and all primitives in the validation primitive registry.
2. **Semantic inference findings** -- advisory or escalation signals. May
   trigger Human escalation but must not reverse a deterministic finding.
3. **Undefined or inconclusive** -- lowest authority. Neither deterministic
   nor semantic evidence is available.

### Relationship to Deterministic Primitives

Semantic validation does not replace or modify deterministic primitives.
Deterministic primitives operate on checkable field-level constraints and
produce verifiable pass/fail findings. Semantic validation operates on
artifact-level meaning, coherence, and plausibility, producing advisory
findings that may escalate to Human judgment.

### v0.7.3 Non-Goals

In v0.7.3, semantic validation is scoped to this boundary definition only:

- No executable semantic Validator behavior is implemented.
- No semantic calibration fixtures or test data are added.
- No runtime orchestration or automatic repair based on semantic findings.
- No changes to `scripts/validator.py` or `scripts/validate_artifacts.py`.
- No changes to lifecycle transitions, ticket formats, or role definitions.
- No modifications to the Validator output JSON schema or verdict computation.

Semantic primitives are reserved in the validation primitive registry for
v0.7.4 implementation. See `references/validation-primitive-registry.md`
Section 11 for the reserved namespace.

### Human Escalation from Semantic Findings

When semantic validation produces findings that cannot be resolved
deterministically, the result is `human_review_required` or `inconclusive`
depending on the severity and confidence of the semantic finding. The
confidence and escalation matrix in `references/validator-protocol.md`
Section 11 governs when Human judgment is required.

### See Also

- `references/validator-protocol.md` Section 11 -- Confidence and Human
  Escalation Matrix.
- `references/validation-primitive-registry.md` Section 11 -- Semantic
  Inference reserved namespace.

## Relationship to Existing Result Format

The Validation Report is distinct from the Runner result format defined in `references/result-format.md`:

- The Runner result (`runner_status: done/partial/blocked/invalid`) expresses whether a ticket's work is complete from a Runner's perspective.
- The Validation Report (`overall_verdict: pass/fail/blocked/inconclusive/human_review_required`) expresses whether one or more artifacts satisfy one or more structural or semantic checks.
- A Runner result may include a Validation Report as structured evidence. The `validation` array in the Runner result can reference validation command outputs, including structured validation report summaries.

## Generic Schema Contract

The generic validation contract itself does not prescribe a JSON Schema or any specific validation engine. It defines the data model (Contract, Report, Finding) and the rule evaluation semantics. Implementations may use JSON Schema, custom checks, or any other mechanism as long as the Contract and Report shapes are preserved.

Two development-time reference scripts ship with this foundation:

- `scripts/validate_artifacts.py` validates Railyard artifact shape (tickets, epics, result files, queue examples, validation contracts, and validation reports) using built-in structural checks. It does not execute an independent Validator role and cannot satisfy a ticket's required Validator gate.
- `scripts/validator.py` is a minimal executable Validator for source-to-derived field-mapping validation. It accepts `python scripts\validator.py --input <validator-input.json> [--output <report.json>]`, reads only the input JSON and referenced artifacts, and emits a Validation Report JSON.

The executable Validator supports source artifact, derived artifact, field mapping contract, required field mappings, `identity`, `multiply_by_2`, `parse_integer`, `parse_number_preserve_sign`, `missing_mapping_policy`, and `warnings_as_errors`. It does not implement external runtime orchestration, workflow lifecycle writes, automatic repair, model routing, or business-specific rules. Planner closure and release-readiness inputs may be reported as `human_review_required` until a dedicated Planner-side readiness implementation exists.

## Contract Ownership, Placement, and Handoff

A Validation Contract moves through the workflow in defined stages. Each role has explicit responsibilities; no role silently redefines the contract produced by another role.

### Ownership by Role

| Role | Responsibility |
|---|---|
| **Human** | Defines unacceptable risk categories and business failure types that drive contract requirements. Approves or rejects contract adequacy when escalated. |
| **Planner** | Defines epic-level contract intent, done definition, cross-ticket dependency, and closure criteria. Contract intent lives in the Epic. |
| **Architect** | Translates Planner intent into an executable Validation Contract, schema, ticket acceptance criteria, and Validator dispatch payload. Executable contract lives in the Ticket or a ticket-scoped artifact. |
| **Runner** | Implements against the contract. Does not redefine, weaken, or bypass the contract. Reports contract inadequacy as a blocker rather than silently working around it. |
| **Validator** | Checks artifacts against the contract as given. Produces a Validation Report only. Does not modify the contract, write lifecycle state, or close epics. |

### Contract Placement

- **Contract intent** belongs in the Epic. The Planner records what the contract must achieve at the epic level: done definition, closure criteria, cross-ticket consistency requirements, and unacceptable failure modes.
- **Executable contract** belongs in the Ticket or a ticket-scoped artifact. The Architect produces the concrete rules, field mappings, check logic, and dispatch payload that the Validator applies.
- A ticket's `validator_contract_source` metadata field references the contract or acceptance criteria the Validator must apply.
- The separation ensures the Planner can express intent without prescribing implementation, and the Architect can produce precise checks without redefining epic-level goals.

### Insufficient or Missing Contract

When a Validation Contract is missing, incomplete, or too vague to produce a deterministic verdict, no role must silently pass the check. The handling is split across two phases.

**Phase 1 -- Architect pre-dispatch gate.**
Before dispatching the Validator, the Architect verifies that a sufficient executable contract exists at the ticket level.

- **Missing contract**: The Architect does not dispatch the Validator. The Architect stops and requests the contract before proceeding.
- **Incomplete contract** (e.g., rules cover some fields but not all required fields): The Architect does not dispatch the Validator for uncovered scope. The Architect escalates or provides the missing rules before dispatch.
- **Vague contract** (e.g., natural-language intent without checkable rules): The Architect translates intent into concrete, checkable rules before dispatch. If the intent cannot be translated into deterministic checks, the Architect does not dispatch the Validator and escalates to the Human for contract clarification.

**Phase 2 -- Validator post-dispatch report.**
After receiving the dispatch payload, if the Validator determines that the contract is missing, insufficient, or too vague to produce a deterministic verdict for some or all scope, the Validator returns `inconclusive`, `blocked`, or `human_review_required` with `missing_evidence` describing the gap. The Validator must not return a hard `pass` for uncovered scope.

**Runner observes missing contract.**
If the Runner receives a ticket whose contract is missing or insufficient to guide implementation, the Runner reports `blocked` with the blocker category `unresolved_dependency` rather than silently proceeding without a contract or inventing one.

In all cases, a hard `pass` is forbidden when the contract is insufficient to determine correctness.

### Handoff Sequence

1. **Human** declares unacceptable risk and failure types at the project or epic level.
2. **Planner** encodes contract intent, done definition, and closure criteria in the Epic.
3. **Architect** produces the executable contract, attaches it to the ticket or ticket-scoped artifact, and constructs the Validator dispatch payload referencing it.
4. **Runner** implements against the executable contract and references it in the result.
5. **Validator** applies the contract as given and returns a Validation Report.
6. **Architect** reviews the Runner result and Validator report against the ticket acceptance criteria.
7. **Planner** reviews closure readiness against the epic-level contract intent and done definition.

## See Also

- `references/result-format.md` - Runner result JSON format.
- `references/lifecycle.md` - Lifecycle state transitions and boundaries.
- `references/roles.md` - Role boundaries including Architect and Planner decision ownership.
- `scripts/validate_artifacts.py` - Artifact shape validator.
- `scripts/validator.py` - Minimal source-to-derived Validator reference implementation.
- `assets/skeleton/docs/templates/VALIDATION-CONTRACT.json` - Generic contract template.
