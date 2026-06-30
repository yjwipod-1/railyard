---
name: validation-primitive-registry
description: Generic deterministic validation primitive registry for source replay, field mapping, value/sign preservation, formula recompute, record/key reconciliation, availability handling, claim grounding, and publish gate aggregation
type: reference
version: 0.7.2
---

# Validation Primitive Registry

This document defines the generic deterministic validation primitive registry
for the Railyard framework. It catalogs all validation primitives organized by
category, with each primitive specifying its stable rule identifier, default
severity, check logic pattern, required input slots, and finding contract.

All primitives are **generic**. They contain no business-specific rules, no
domain-specific field names, and no hardcoded domain values. Every primitive is
parameterized by the Validation Contract, field mapping contract, and evidence
pack supplied at invocation time.

## 0. Purpose and Relationship

The primitive registry is the canonical catalog of deterministic validation
checks available to Validator implementations and contract authors within the
Railyard framework.

Relationship to existing references:

- `references/validator-protocol.md` Section 7 defines the source-to-derived
  reconciliation **pattern** (rule semantics, invocation context, missing
  mapping policy, and example report). This registry catalogs each primitive
  individually with a uniform entry contract, covering reconciliation and
  additional categories beyond source-to-derived.
- `references/validation-contract.md` defines the Validation Contract and
  Validation Report models. Primitives in this registry are applied within
  those contracts and produce findings within those reports.
- `references/validator-protocol.md` Sections 4-6 define verdict semantics,
  truth hierarchy, and severity/status independence. All primitives in this
  registry obey those rules.

A Validator implementation MAY implement any subset of these primitives. A
Validation Contract references the primitives it requires by their stable
rule_id. A Validator report records findings using the same rule_ids.

## 1. Primitive Entry Contract

Each primitive in this registry is defined with the following fields:

| Field | Type | Description |
|---|---|---|
| `rule_id` | string | Stable identifier for the primitive. Used in Validation Contract rules and Validation Report findings. |
| `default_severity` | string | Default severity when the contract does not override: `error`, `warn`, or `info`. A contract MAY override per rule. |
| `description` | string | Human-readable description of what the primitive checks. |
| `check_logic` | string | Generic check pattern. Parameterized by contract, mapping, and evidence inputs. Not a code snippet; describes the algorithm. |
| `required_inputs` | array of strings | Input slot names the primitive requires. If any are missing, the finding MUST be `blocked` or `inconclusive`, never `pass`. |

### Generic Finding Contract

Every primitive produces findings with the standard fields defined in
`references/validator-protocol.md` Section 3:

| Field | Type | Description |
|---|---|---|
| `rule_id` | string | The primitive's stable identifier. |
| `severity` | string | `error`, `warn`, or `info`. From the contract override or the primitive's `default_severity`. |
| `status` | string | `pass`, `fail`, `not_applicable`, `blocked`, or `inconclusive`. |
| `message` | string | Human-readable description of the finding. |
| `evidence` | string or null | Specific data that led to the finding. `null` only when no specific evidence exists. |

Status semantics follow `references/validator-protocol.md` Section 6
(severity/status independence). The overall verdict computation follows
Section 4 of the same document.

### Severity Override

A Validation Contract MAY override the `default_severity` of any primitive by
specifying the severity in the contract rule entry. The Validator MUST use the
contract-specified severity when present and the primitive's default severity
otherwise.

### Required Input Absence

When a required input is missing, the primitive MUST NOT return
`status=pass`. The finding MUST be:

- `status=blocked` if the input is structurally absent (file not found, field
  missing, artifact unreadable).
- `status=inconclusive` if the input is present but insufficient to evaluate.

This rule is consistent with the Unsupported Pass Refusal defined in
`references/validator-protocol.md` Section 4.3.

## 2. Source Replay

Source replay primitives verify that derived artifacts can be traced back to
declared source artifacts, that all declared sources are accounted for, and
that the source replay chain uses only declared transformations.

### 2.1 `source_replay_provenance`

- **default_severity**: `error`
- **description**: Every derived artifact MUST be traceable to at least one
  declared source artifact. A derived artifact with no source provenance is
  unverifiable.
- **check_logic**: For each derived artifact in the Validator input, verify
  that the contract or evidence_pack declares at least one source artifact. If
  no source is declared for a derived artifact, the finding is
  `severity=error, status=fail`.
- **required_inputs**: `derived_artifact`, `source_declaration`

Finding examples:

- pass: `"Derived artifact traced to 2 declared sources."`
- fail: `"Derived artifact 'output.json' has no declared source provenance."`

### 2.2 `source_replay_completeness`

- **default_severity**: `error`
- **description**: All source artifacts declared in the contract or evidence
  pack MUST be present and accessible. Missing declared sources prevent
  complete validation.
- **check_logic**: For each source artifact declared in the contract or
  evidence_pack, verify that the artifact exists and is readable. If any
  declared source is missing, the finding is `severity=error, status=blocked`
  and the missing source is recorded in `missing_evidence`.
- **required_inputs**: `source_declaration`, `artifacts`

Finding examples:

- pass: `"All 3 declared source artifacts present and accessible."`
- blocked: `"Declared source 'data/source.csv' not found in artifact list."`

### 2.3 `source_replay_fidelity`

- **default_severity**: `warn`
- **description**: The replay chain from source to derived MUST use only
  declared transformations. Undeclared intermediate steps or side-channel
  inputs reduce replay confidence.
- **check_logic**: Compare the actual derivation path (from evidence_pack or
  candidate implementation analysis) against the declared transformation chain.
  If undeclared intermediate steps are detected, the finding is
  `severity=warn, status=fail`. If the chain fully matches declarations, the
  finding is `severity=warn, status=pass`.
- **required_inputs**: `source_artifact`, `derived_artifact`, `declared_transforms`

Finding examples:

- pass: `"Replay chain matches declared transformation sequence."`
- fail: `"Undeclared intermediate step detected between source field 'A' and derived field 'B'."`

## 3. Declared Field Mapping

Declared field mapping primitives verify that every derived field is covered
by an explicit mapping, that mapping targets resolve correctly, and that no
undeclared mappings are applied.

### 3.1 `field_mapping_required`

- **default_severity**: `error`
- **description**: Every derived field MUST have an explicit mapping to a
  source field or a declared transformation rule. Unmapped derived fields
  prevent deterministic validation.
- **check_logic**: For each field in the derived artifact, verify that a
  mapping entry exists in the contract or evidence_pack. If a derived field
  has no mapping, the finding is `severity=error, status=fail`. The message
  identifies the unmapped field.
- **required_inputs**: `derived_artifact`, `field_mapping_contract`

Finding examples:

- pass: `"All 12 derived fields have declared mappings."`
- fail: `"Derived field 'total_amount' has no mapping to any source field or transformation."`

### 3.2 `field_mapping_target_resolution`

- **default_severity**: `error`
- **description**: Each declared field mapping MUST resolve to an existing
  source field. Mappings that reference nonexistent source paths indicate
  contract errors.
- **check_logic**: For each field mapping in the contract, verify that the
  `source_path` resolves to an existing field in the source artifact. If the
  target does not resolve, the finding is `severity=error, status=fail`.
- **required_inputs**: `source_artifact`, `field_mapping_contract`

Finding examples:

- pass: `"All 8 mapping source paths resolve to existing source fields."`
- fail: `"Mapping source_path 'record.unit_price' does not resolve in source artifact."`

### 3.3 `field_mapping_no_undeclared`

- **default_severity**: `warn`
- **description**: Derived fields MUST NOT be produced by undeclared
  transformations or from source fields not referenced in the mapping
  contract.
- **check_logic**: For each derived field, verify that the transformation
  applied matches a declared mapping entry. If an undeclared transform or
  source reference is detected (from evidence_pack or implementation
  analysis), the finding is `severity=warn, status=fail`.
- **required_inputs**: `derived_artifact`, `field_mapping_contract`, `evidence_pack`

Finding examples:

- pass: `"No undeclared mappings detected."`
- fail: `"Derived field 'computed_total' appears to use an undeclared source field 'raw_subtotal'."`

## 4. Value/Sign Preservation

Value and sign preservation primitives verify that source values are correctly
preserved or transformed in the derived artifact according to the contract.

### 4.1 `source_value_preservation`

- **default_severity**: `error`
- **description**: Source values declared as preserved (identity transform)
  MUST appear unchanged in the derived artifact. Comparison uses exact value
  matching via Decimal or string comparison.
- **check_logic**: For each source field with declared transform `identity`,
  compare the raw source value and the derived value. Use exact comparison
  (Decimal for numeric, string equality for text). If values differ, the
  finding is `severity=error, status=fail`.
- **required_inputs**: `source_artifact`, `derived_artifact`, `field_mapping_contract`

Finding examples:

- pass: `"Source field 'record_id' value 'R-1001' preserved exactly in derived."`
- fail: `"Source field 'name' value 'Acme Corp' does not match derived value 'Acme Corp.' (trailing period)."`

### 4.2 `signed_numeric_preservation`

- **default_severity**: `error`
- **description**: Numeric values with sign information MUST preserve sign
  through the transformation. A negative source value MUST NOT become positive
  unless the contract explicitly declares a sign-flip operation.
- **check_logic**: For each numeric field with `preserve_sign: true` in the
  mapping, compare the sign of the source value and the derived value. If the
  sign differs and no sign-flip transform is declared, the finding is
  `severity=error, status=fail`.
- **required_inputs**: `source_artifact`, `derived_artifact`, `field_mapping_contract`

Finding examples:

- pass: `"Source value -42.50 preserves sign in derived value -42.50."`
- fail: `"Source value -100.00 became positive 100.00 in derived; no sign-flip transform declared."`

### 4.3 `value_transform_correctness`

- **default_severity**: `error`
- **description**: When a non-identity transform is declared, applying the
  declared transform to the source value MUST produce the derived value.
- **check_logic**: For each field mapping with a non-identity declared
  transform, apply the transform to the source value and compare with the
  derived value. If the result does not match, the finding is
  `severity=error, status=fail`. The comparison uses the same precision rules
  as `source_value_preservation`.
- **required_inputs**: `source_artifact`, `derived_artifact`, `field_mapping_contract`, `transform_implementation`

Finding examples:

- pass: `"Transform 'multiply_by_2' applied to source 21.00 produces derived 42.00."`
- fail: `"Transform 'multiply_by_2' applied to source 21.00 produces 42.00 but derived value is 43.00."`
- inconclusive: `"Transform 'multiply_by_2' declared but implementation not available in evidence."`

### 4.4 `numeric_precision_preservation`

- **default_severity**: `warn`
- **description**: Numeric transformations MUST NOT introduce unexpected
  precision loss or gain. Decimal precision in the derived value MUST be
  consistent with the declared transform semantics.
- **check_logic**: For numeric fields, compare the decimal precision of the
  derived value against the expected precision from the transform. If
  precision is lost or gained beyond what the transform declares, the finding
  is `severity=warn, status=fail`.
- **required_inputs**: `source_artifact`, `derived_artifact`, `field_mapping_contract`

Finding examples:

- pass: `"Derived precision (2 decimal places) consistent with source and transform."`
- fail: `"Source value 1.005 truncated to 1.00 in derived; no rounding declaration in contract."`

## 5. Formula Recompute

Formula recompute primitives verify that derived fields computed from formulas
produce correct results when independently recalculated.

### 5.1 `formula_recompute_match`

- **default_severity**: `error`
- **description**: For derived fields declared as formula results,
  independently recomputing the formula with source input values MUST produce
  a result matching the derived value.
- **check_logic**: For each derived field with a declared formula, resolve
  input bindings from source artifacts, apply the formula expression, and
  compare the result with the derived value. If the recomputed result does not
  match, the finding is `severity=error, status=fail`.
- **required_inputs**: `source_artifact`, `derived_artifact`, `formula_declaration`

Finding examples:

- pass: `"Formula 'sum(unit_price * quantity)' recomputed to 150.00, matches derived."`
- fail: `"Formula 'sum(unit_price * quantity)' recomputed to 150.00, derived is 155.00."`
- inconclusive: `"Formula declared but expression not parseable from contract."`

### 5.2 `formula_input_availability`

- **default_severity**: `error`
- **description**: All input bindings referenced by a declared formula MUST
  be resolvable from source artifacts. Missing formula inputs prevent
  recomputation.
- **check_logic**: For each input binding in a formula declaration, verify
  that the binding resolves to an existing source field. If any input binding
  is unresolved, the finding is `severity=error, status=blocked` and the
  missing binding is recorded in `missing_evidence`.
- **required_inputs**: `source_artifact`, `formula_declaration`

Finding examples:

- pass: `"All 3 formula input bindings resolve to source fields."`
- blocked: `"Formula input 'tax_rate' does not resolve to any source field."`

### 5.3 `formula_declaration_consistency`

- **default_severity**: `warn`
- **description**: The formula expression declared in the contract MUST match
  the formula implementation used to produce the derived artifact.
- **check_logic**: Compare the declared formula expression with the
  implementation evidence (from evidence_pack or candidate analysis). If the
  implementation differs from the declaration, the finding is
  `severity=warn, status=fail`.
- **required_inputs**: `formula_declaration`, `evidence_pack`

Finding examples:

- pass: `"Formula declaration matches implementation evidence."`
- fail: `"Declared formula 'a + b' but implementation evidence shows 'a + b + c'."`
- inconclusive: `"Formula implementation evidence not available."`

## 6. Record/Key Reconciliation

Record and key reconciliation primitives verify that source and derived
record sets correspond correctly by key and by count.

### 6.1 `record_identity_preservation`

- **default_severity**: `error`
- **description**: Each source record, identified by its key, MUST have a
  corresponding derived record. Orphaned source records indicate data loss.
- **check_logic**: For each record key in the source artifact, verify that a
  corresponding derived record exists with the same key. If a source record
  has no derived counterpart, the finding is `severity=error, status=fail`.
  The evidence lists the orphaned source keys.
- **required_inputs**: `source_artifact`, `derived_artifact`, `record_key_mapping`

Finding examples:

- pass: `"All 25 source records have corresponding derived records."`
- fail: `"Source records with keys ['R-1003', 'R-1017'] have no derived counterpart."`

### 6.2 `record_key_set_match`

- **default_severity**: `error`
- **description**: The set of record keys in the source artifact MUST match
  the set in the derived artifact. Both orphaned source records and
  fabricated derived records are reconciliation failures.
- **check_logic**: Compute the key set from the source artifact and the key
  set from the derived artifact. Compare both directions: source keys missing
  from derived (orphans) and derived keys not present in source (fabricated).
  Report orphans and fabrications as `severity=error, status=fail`. If the
  sets match exactly, the finding is `status=pass`.
- **required_inputs**: `source_artifact`, `derived_artifact`, `record_key_mapping`

Finding examples:

- pass: `"Source and derived key sets match exactly: 25 keys each."`
- fail: `"Orphaned source keys: ['R-1003']. Fabricated derived keys: ['R-9999']."`

### 6.3 `record_count_reconciliation`

- **default_severity**: `warn`
- **description**: Source and derived record counts MUST reconcile. If the
  contract declares filtering, aggregation, or expansion, the count
  relationship must match the declaration.
- **check_logic**: Compare source record count and derived record count. If
  the contract declares a filter or aggregation, verify the count relationship
  matches (e.g., derived count <= source count for filter). If counts diverge
  without a declared reason, the finding is `severity=warn, status=fail`.
- **required_inputs**: `source_artifact`, `derived_artifact`, `field_mapping_contract`

Finding examples:

- pass: `"Source: 30 records, derived: 25 records. Filter declaration accounts for difference."`
- fail: `"Source: 30 records, derived: 28 records. No filter or aggregation declared for 2-record difference."`

## 7. Availability Handling

Availability handling primitives govern Validator behavior when inputs,
mappings, or evidence are missing or insufficient.

### 7.1 `unmapped_field_availability`

- **default_severity**: `warn`
- **description**: When a derived field has no mapping to an independent
  source, the Validator applies the contract's `missing_mapping_policy` to
  determine the finding status.
- **check_logic**: For each unmapped derived field, apply the contract's
  `missing_mapping_policy`:
  - `inconclusive`: finding is `status=inconclusive`.
  - `fail`: finding is `status=fail`.
  - `human_review_required`: finding is `status=fail` with a message
    recommending human review.
  If the contract does not declare a policy, the default is `inconclusive`.
- **required_inputs**: `derived_artifact`, `field_mapping_contract`

Finding examples:

- inconclusive: `"Missing mapping for 'derived_label'; policy=inconclusive."`
- fail: `"Missing mapping for 'total_tax'; policy=fail."`

### 7.2 `evidence_availability`

- **default_severity**: `warn`
- **description**: Evidence items declared as required by the contract or by
  primitive `required_inputs` MUST be present. Missing evidence reduces
  validation confidence and may block verdicts.
- **check_logic**: For each required evidence item, verify presence in the
  evidence_pack or artifacts list. If missing, record in `missing_evidence`
  with the expected item description and impact. If critical evidence is
  missing, affected findings become `status=blocked` or
  `status=inconclusive`.
- **required_inputs**: `evidence_pack`, `required_evidence_declaration`

Finding examples:

- pass: `"All required evidence items present."`
- inconclusive: `"Missing evidence: 'source schema for field types'. Impact: cannot verify type correctness."`

### 7.3 `source_availability`

- **default_severity**: `error`
- **description**: Source artifacts declared in the contract MUST be present
  and readable. Missing or unreadable source artifacts MUST produce
  `status=blocked` findings, not silent passes.
- **check_logic**: For each source artifact referenced in the contract or
  input slots, verify the artifact exists and is readable. If missing or
  unreadable, all primitives that depend on that source produce
  `status=blocked` findings. The missing source is recorded in
  `missing_evidence`.
- **required_inputs**: `artifacts`, `source_declaration`

Finding examples:

- pass: `"All declared source artifacts present and readable."`
- blocked: `"Source artifact 'data/source.csv' not found; all dependent checks blocked."`

## 8. Claim Grounding

Claim grounding primitives verify that assertions in derived artifacts are
traceable to source evidence and do not exceed what the source supports.

### 8.1 `claim_source_grounding`

- **default_severity**: `error`
- **description**: Every derived assertion (value, label, classification, or
  computed result) MUST be traceable to source data through a declared
  mapping, transformation, or evidence_pack entry.
- **check_logic**: For each derived assertion, verify that a source grounding
  exists. Trace the assertion through the field mapping contract or
  evidence_pack to a source field or declared computation. If no grounding is
  found, the finding is `severity=error, status=fail`.
- **required_inputs**: `derived_artifact`, `source_artifact`, `field_mapping_contract`, `evidence_pack`

Finding examples:

- pass: `"All 15 derived assertions traced to source data."`
- fail: `"Derived classification 'premium' has no grounding in source data or declared transformation."`

### 8.2 `claim_precision_grounding`

- **default_severity**: `warn`
- **description**: Derived assertions MUST NOT claim precision, granularity,
  or specificity that exceeds what the source data supports.
- **check_logic**: For each derived assertion, compare its precision against
  the source data precision. If the derived value is more precise than the
  source supports without a declared rounding or precision transformation, the
  finding is `severity=warn, status=fail`.
- **required_inputs**: `derived_artifact`, `source_artifact`, `field_mapping_contract`

Finding examples:

- pass: `"Derived precision (2 decimal places) within source precision (4 decimal places)."`
- fail: `"Source value '3.1' (1 decimal) produced derived '3.14159' (5 decimals) without declared precision transform."`

### 8.3 `claim_chain_integrity`

- **default_severity**: `error`
- **description**: The chain from source to derived MUST NOT contain gaps
  where an intermediate value is fabricated, assumed, or sourced from outside
  the declared chain.
- **check_logic**: Trace the complete derivation chain for each derived
  assertion. If any intermediate step lacks a declared source or declared
  transformation, the finding is `severity=error, status=fail`. The evidence
  identifies the gap location.
- **required_inputs**: `derived_artifact`, `source_artifact`, `field_mapping_contract`, `evidence_pack`

Finding examples:

- pass: `"Complete derivation chain verified for all 8 derived fields."`
- fail: `"Derivation chain for 'net_total' has gap at step 2: intermediate 'discount_rate' has no declared source."`

## 9. Publish Gate Aggregation

Publish gate aggregation primitives define how multiple validation gates are
combined into a composite publish decision.

### 9.1 `publish_gate_aggregate`

- **default_severity**: `error`
- **description**: When multiple validation gates are declared for a publish
  decision, the aggregate verdict MUST reflect the worst individual gate
  verdict. A single failing gate MUST block the aggregate publish verdict.
- **check_logic**: Collect the `overall_verdict` from each validation gate in
  the gate set. Compute the aggregate verdict using the worst-verdict
  hierarchy defined in Section 9.4. If any gate verdict is `fail`, the
  aggregate is `fail`. The finding summarizes each gate's contribution.
- **required_inputs**: `gate_verdicts`

Finding examples:

- pass: `"All 3 gates passed: source-replay=pass, field-mapping=pass, formula-recompute=pass."`
- fail: `"Gate 'formula-recompute' returned fail; aggregate verdict is fail."`

### 9.2 `publish_gate_completeness`

- **default_severity**: `error`
- **description**: All declared gates MUST produce a valid Validator report
  before the aggregate verdict is computed. Missing gate reports block the
  aggregate.
- **check_logic**: For each gate declared in the publish gate set, verify
  that a valid Validation Report exists. If any gate report is missing, the
  finding is `severity=error, status=blocked` and the missing report is
  recorded in `missing_evidence`.
- **required_inputs**: `gate_declarations`, `gate_reports`

Finding examples:

- pass: `"All 4 declared gates produced valid reports."`
- blocked: `"Gate 'record-reconciliation' has no report; aggregate blocked."`

### 9.3 `publish_gate_independence`

- **default_severity**: `error`
- **description**: Individual gate verdicts MUST be independently produced. A
  gate MUST NOT derive its verdict from another gate's output. Each gate
  evaluates its own contract and evidence.
- **check_logic**: For each gate in the set, verify that its report was
  produced from independent evaluation of its own contract and evidence, not
  from another gate's verdict or findings. If gate dependence is detected, the
  finding is `severity=error, status=fail`.
- **required_inputs**: `gate_reports`

Finding examples:

- pass: `"All gate reports produced from independent evaluations."`
- fail: `"Gate 'value-preservation' verdict appears derived from gate 'field-mapping' output."`

### 9.4 Aggregate Verdict Computation

The aggregate publish verdict follows the worst-verdict hierarchy:

```
aggregate = fail
    IF any gate verdict is fail

aggregate = blocked
    IF no fail gate verdicts but any gate verdict is blocked

aggregate = human_review_required
    IF no fail and no blocked gate verdicts,
    and any gate verdict is human_review_required

aggregate = inconclusive
    IF no fail, no blocked, no human_review_required gate verdicts,
    but any gate verdict is inconclusive

aggregate = pass
    IF all gate verdicts are pass
```

This hierarchy is consistent with the overall verdict computation defined in
`references/validator-protocol.md` Section 6. The aggregate verdict preserves
the worst individual gate verdict and surfaces all gate findings in the
aggregate report.

## 10. Extension Notes

- This registry is generic and not tied to any business domain. All primitives
  are parameterized by contract and evidence inputs.
- Future versions MAY add new primitive categories. Each new primitive MUST
  follow the entry contract defined in Section 1 and MUST respect the truth
  hierarchy and severity/status independence rules from
  `references/validator-protocol.md`.
- Business-specific rules MUST NOT be added to this registry. They belong in
  domain-specific Validation Contracts that reference these primitives.
- New transform implementations are out of scope for this registry. Transform
  names referenced by primitives (e.g., `identity`, `multiply_by_2`) are
  defined in the Validator implementation and the Validation Contract.
- This registry does not prescribe runtime orchestration, dispatch logic, or
  lifecycle integration. Those concerns are handled by the Validator protocol
  and the Railyard lifecycle model.
- Section 11 defines the Semantic Inference primitive namespace for v0.7.4
  contract authors. Each entry includes bounded contract check logic
  parameterized by contract-supplied inputs. This registry definition does not
  imply executable support in `scripts/validator.py`.
- The semantic validation boundary and deterministic precedence rules that
  govern semantic primitives are defined in
  `references/validation-contract.md` Semantic Validation Boundary section.

## 11. Semantic Inference

This section defines the semantic validation primitive namespace. Each entry
specifies the stable rule_id, default severity, description, bounded contract
check logic, and required inputs. Semantic inference primitives are advisory or
escalation signals under the deterministic precedence rule; they must not
override deterministic primitive findings. The entries are contract-level
definitions; executable Validator support is explicitly out of scope unless a
future ticket adds it.

Semantic inference primitives evaluate logical consistency, cross-artifact
coherence, domain-level correctness, or meaning-based properties. They operate
at the artifact level rather than the field level. Under the deterministic
precedence rule (see `references/validation-contract.md` Semantic Validation
Boundary), semantic inference findings are advisory or escalation signals and
must not override deterministic primitive findings.

### 11.1 `semantic_coherence`

- **default_severity**: `error`
- **description**: Check cross-artifact logical consistency. Related artifacts
  (e.g., a ticket and its parent epic) must not contain logically conflicting
  statements about the same concept.
- **check_logic**: For each artifact pair declared in `coherence_scope`, compare
  the relevant assertions across artifacts. If both artifacts make claims about
  the same concept and those claims are logically inconsistent, the finding is
  `severity=error, status=fail`. If only one side of the pair is available
  (evidence state is `missing_evidence`), the finding is `status=inconclusive`.
  If evidence is present but sources conflict (`conflicting_evidence`), the
  finding is `status=inconclusive` with escalated severity. If the claim type
  or scope is not supported (`unsupported_semantic_claim`), the finding is
  `status=fail` with `overall_verdict=human_review_required`. The deterministic
  precedence rule applies: deterministic findings override semantic coherence
  findings for the same artifact, field, or assertion.
- **required_inputs**: `primary_artifact`, `related_artifacts`, `coherence_scope`

Finding examples:

- pass: `"Coherence verified: Ticket T-001 acceptance criteria are consistent with Epic E-001 done definition."`
- fail: `"Coherence violation: Artifact A asserts requirement R1 mandatory; Artifact B asserts R1 optional for the same scope."`
- inconclusive: `"Cannot verify coherence: related artifact 'epic-002' not available for comparison."`

### 11.2 `semantic_contradiction`

- **default_severity**: `error`
- **description**: Detect directly contradictory assertions across artifacts.
  If one artifact states a requirement and another artifact states the opposite,
  the contradiction must be flagged for resolution.
- **check_logic**: Scan the primary artifact and related artifacts for directly
  contradictory assertions within the `contradiction_domain`. A contradiction
  exists when two artifacts make mutually exclusive claims about the same
  concept, property, or requirement. If a contradiction is detected, the finding
  is `severity=error, status=fail`. Both contradictory assertions are recorded
  in the evidence. If one side of the potential contradiction is unavailable
  (`missing_evidence`), the finding is `status=inconclusive`. If evidence from
  different sources disagrees about whether a contradiction exists
  (`conflicting_evidence`), the finding is `status=inconclusive`. If the claim
  type or domain is not supported (`unsupported_semantic_claim`), the finding
  is `status=fail` with `overall_verdict=human_review_required`. The
  deterministic precedence rule applies: deterministic findings override
  semantic contradiction findings.
- **required_inputs**: `primary_artifact`, `related_artifacts`, `contradiction_domain`

Finding examples:

- pass: `"No contradictory assertions detected across the artifact set."`
- fail: `"Contradiction detected: Artifact A states 'field X is required'; Artifact B states 'field X is optional'."`
- inconclusive: `"Contradiction check incomplete: related artifact 'config-v2' not available."`

### 11.3 `semantic_completeness`

- **default_severity**: `warn`
- **description**: Verify that required semantic concepts are addressed across
  the artifact set. If a ticket references a concept that the epic defines as
  required, the concept must appear in the ticket scope or acceptance criteria.
- **check_logic**: For each concept declared as required in the `completeness_scope`
  concept registry, verify that the concept appears in the primary artifact's
  scope, acceptance criteria, or body. A concept is "addressed" when the primary
  artifact references it by identifier, description, or structurally scoped
  section. If a required concept is not addressed, the finding is
  `severity=warn, status=fail`. If the concept registry is unavailable
  (`missing_evidence`), the finding is `status=inconclusive`. If multiple
  concept registries or interpretations conflict (`conflicting_evidence`), the
  finding is `status=inconclusive`. If the completeness scope or concept
  registry format is not supported (`unsupported_semantic_claim`), the finding
  is `status=inconclusive`. The deterministic precedence rule applies.
- **required_inputs**: `primary_artifact`, `concept_registry`, `completeness_scope`

Finding examples:

- pass: `"All 5 required concepts from Epic E-001 are addressed in Ticket T-001 scope."`
- fail: `"Required concept 'error_handling' from Epic E-001 is not addressed in Ticket T-001."`
- inconclusive: `"Completeness check blocked: concept registry not provided."`

### 11.4 `semantic_plausibility`

- **default_severity**: `warn`
- **description**: Flag implausible values, relationships, or assertions that
  are structurally valid but semantically unlikely. Plausibility is determined
  by the contract-supplied plausibility rules, not by external model inference.
- **check_logic**: For each plausibility rule declared in `plausibility_rules`,
  evaluate the target artifact's values, relationships, or assertions against
  the rule's bounded constraints. A plausibility rule defines a bound (range,
  set, pattern, or relationship constraint) derived from artifact context, not
  from external model inference. If a value, relationship, or assertion falls
  outside the declared bounds, the finding is `severity=warn, status=fail`. If
  the plausibility rules are unavailable (`missing_evidence`), the finding is
  `status=inconclusive`. If multiple plausibility interpretations conflict
  (`conflicting_evidence`), the finding is `status=inconclusive`. If the
  plausibility rule format or scope is not supported
  (`unsupported_semantic_claim`), the finding is `status=inconclusive`. The
  deterministic precedence rule applies: deterministic field-level checks
  (numeric precision, value preservation) override plausibility findings.
- **required_inputs**: `target_artifact`, `plausibility_rules`, `evidence_pack`

Finding examples:

- pass: `"Value 42 falls within plausibility range [0, 100] declared by context."`
- fail: `"Value 999 exceeds plausibility bound 100 derived from artifact constraints."`
- inconclusive: `"Plausibility check incomplete: plausibility rules not provided."`
