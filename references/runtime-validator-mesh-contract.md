---
name: runtime-validator-mesh-contract
description: Public Validator Mesh Contract defining total contribution, freshness, duplicate/conflict, and unchanged-output semantics
type: contract
version: 1.2.0
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
  - artifact_id: validation-contract
    artifact_kind: contract
    artifact_version: 0.8.0
    locator: references/validation-contract.md
  - artifact_id: runtime-gate-decision-contract
    artifact_kind: contract
    artifact_version: 2.2.0
    locator: references/runtime-gate-decision-contract.md
  - artifact_id: runtime-state-contract
    artifact_kind: contract
    artifact_version: 0.9.0
    locator: references/runtime-state-contract.md
risk_level: high
validator_required: true
visibility: public
---

# Runtime Validator Mesh Contract

This v1.2.0 contract supersedes and remediates the v1.1.0 authority wording. It defines a pure evaluation from one complete `ValidatorMeshEvaluationRequest` to exactly one `ValidatorMeshResult` XOR `ValidatorMeshEvaluationError`. It does not change implementation, dispatch, sidecar, storage, lifecycle, or Gate authority.

## 1. Scope and Non-Authority

The contract defines declaration facts, dispatch-result classification, offline binding comparison, total contribution and aggregation rules, and output boundaries. It does not dereference an ArtifactRef, inspect files, use a clock, environment, model inference, event query, hidden state, retry, or mutation. The evaluator receives exactly one dispatch result for each unique declared requirement and does not dispatch again.

`ValidatorMeshResult` is the production output. A caller, smoke executor, verifier, bridge, or summary projection MAY copy stable fields from it but MUST NOT replace, reinterpret, synthesize, or mutate `aggregate_verdict`, `recommended_action`, `requirement_results`, `freshness_assessments`, a report verdict, or an error code after return. A `ValidatorMeshEvaluationError` is likewise exposed unchanged. No simulated verdict is permitted.

## 2. Public Shapes and Offline Comparison Facts

The public roots remain `ValidatorMeshDeclaration`, `ValidatorRequirement`, `ValidatorDispatchRequest`, `ValidatorDispatchResult`, `ValidationReportBinding`, `ValidatorMeshEvaluationRequest`, `ValidatorMeshResult`, and `ValidatorMeshEvaluationError`. Existing v1.0.0 and v1.1.0 shapes are read as historical inputs; a v1.2.0 producer supplies the additional required identity facts below.

### 2.1 ArtifactRef and ReportArtifactRef

Every declaration `contract_ref`, every member of declaration `artifact_scope`, and every binding `contract_ref` and `target_artifact_ref` MUST contain non-empty `artifact_id`, `artifact_kind`, `artifact_version`, and 64-lowercase-hex `digest`. `contract_ref.artifact_kind` MUST be `contract`.

`ReportArtifactRef` is a dedicated closed shape within `ValidationReportBinding`. It requires all four keys: `artifact_id`, `artifact_kind`, `artifact_version`, and `digest`. `artifact_kind` is the non-null constant `validation_report`. `artifact_id`, `artifact_version`, and `digest` are each their normal validated string form or JSON `null`. Empty strings are forbidden. `report_sha256` is a required key and is a 64-character lowercase digest or JSON `null`. A missing key, wrong type, empty string, extra field, or non-null `report_ref.digest != report_sha256` is malformed and returns `invalid_report_binding` before freshness classification.

If structural validation succeeds and any of `report_ref.artifact_id`, `report_ref.artifact_version`, `report_ref.digest`, or `report_sha256` is null, freshness is `stale` unless superseded or invalidated already won by precedence. Stale evidence contains `field_category: report_identity` and a non-empty, unique, canonical-order `missing_fields` array drawn only from those four field paths (`report_ref.artifact_id`, `report_ref.artifact_version`, `report_ref.digest`, `report_sha256`). If all four identity values are non-null and equal where required, normal mismatch, duplicate/conflict, and current classification continues.

The exact ContractKey is:

```
(artifact_id, artifact_kind, artifact_version, digest)
```

The exact TargetKey is the same four-field tuple. A requirement's `artifact_scope` is an ordered, duplicate-free list of TargetKeys. A binding has one `target_artifact_ref`; therefore a v1.2 requirement that uses this public binding shape MUST declare exactly one target ArtifactRef. A multi-target requirement is unsupported input until a future public binding shape is versioned; it is never guessed or partially compared.

Binding contract equality is ContractKey(binding.contract_ref) equals ContractKey(requirement.contract_ref). Target equality is TargetKey(binding.target_artifact_ref) equals the sole declared TargetKey. These comparisons are field equality only. They do not read an artifact, compare timestamps, infer equivalence, or accept a partial match.

### 2.2 Requirement and Dispatch Invariants

- `mesh_version` is `1.2.0`.
- `requirement_id` is unique within a declaration. `requirement_kind` is exactly `baseline` or `extension`; `required` is boolean.
- A baseline declares `missing_mapping_policy` as exactly `fail` or `human_review_required`. An extension has no missing-policy effect.
- Exactly one dispatch result is present for each requirement, keyed by its `dispatch_request_id`; every result maps to one requirement and no result maps twice.
- Canonical dispatch statuses are `report_produced`, `no_report`, `unreachable`, `degraded_storage`, and `degraded_transport`. The last two are collectively `degraded-without-binding` when they contain no binding.
- `report_produced` requires exactly one structurally valid binding. Every other status forbids a binding. A status outside this enumeration is unsupported input.
- Input objects and nested values are not mutated.

## 3. Binding, Freshness, Duplicate, and Conflict

### 3.1 Binding Structural Validity

A report binding contains unique `binding_id`, its requirement id, `validator_identity`, `role: validator`, the complete contract and target ArtifactRefs, `report_ref` (a closed `ReportArtifactRef`), matching `report_ref.digest` and `report_sha256` where both are non-null, `report_confidence` (exactly `high`, `medium`, or `low`), a five-value `report_overall_verdict`, and independent-production evidence with `no_caller_role_collapse: true`. Missing or internally inconsistent binding facts are malformed input, not freshness.

### 3.2 Freshness Classification

Each produced binding receives exactly one status in this order: `superseded`, `invalidated`, `stale`, `mismatched`, duplicate/conflict group status, then `current`.

| Status | Exact predicate | Deterministic details |
|---|---|---|
| `superseded` | a valid explicit supersession fact is present | `supersession` |
| `invalidated` | a valid explicit invalidation fact is present | `invalidation` |
| `stale` | any of `report_ref.artifact_id`, `report_ref.artifact_version`, `report_ref.digest`, or `report_sha256` is null, after superseded/invalidated win | `field_category: report_identity`; `missing_fields` array in canonical order from the null fields among the four report-identity paths |
| `mismatched` | binding ContractKey differs from declared ContractKey | `field_category: contract_binding` |
| `mismatched` | binding TargetKey differs from declared TargetKey | `field_category: target_artifact_binding` |
| `duplicate` | later binding in a comparison group has the same report digest as the first binding | `comparison_key`, `first_requirement_id` |
| `conflicting` | two or more current-candidate bindings in a comparison group differ in report digest or report verdict | `comparison_key`, ordered `requirement_ids` |
| `current` | none of the preceding predicates applies | empty object |

For a contract and target mismatch in the same binding, `contract_binding` wins because it is evaluated first. Details use only the closed keys shown above; no free-text inference is allowed.

### 3.3 Cross-Requirement Representability

Duplicate and conflict never require duplicate requirement ids, more than one result per requirement, or more than one binding per result. After the first four freshness predicates, group bindings from distinct requirements by this ComparisonKey:

```
(validator_identity, ContractKey(binding.contract_ref), TargetKey(binding.target_artifact_ref))
```

Group order is `dispatch_priority`, then declaration order, then `requirement_id`. The first report digest establishes the group digest. A later same digest is `duplicate`. If any member has a different digest or a different report verdict, every non-duplicate member of that group is `conflicting`; same-digest later members remain `duplicate`. This rule is deterministic and makes both a duplicate and a conflict representable using two distinct requirement ids and two ordinary one-result dispatch records.

## 4. Total Contribution Matrix

The following matrix covers every valid requirement/status/freshness cell. `degraded-without-binding` means either degraded canonical dispatch status without a binding. `excluded` is not a verdict contribution.

| Requirement | Dispatch/freshness condition | Requirement result | Contribution |
|---|---|---|---|
| any | `report_produced` + `current` | `report` | exact `report_overall_verdict` |
| baseline | `no_report`, `unreachable`, or degraded-without-binding | `missing_baseline` | exact `missing_mapping_policy` |
| required extension | `no_report`, `unreachable`, or degraded-without-binding | `missing_required_extension` | `blocked` |
| optional extension | `no_report`, `unreachable`, or degraded-without-binding | `optional_excluded` | excluded |
| required any kind | produced report is `superseded`, `invalidated`, `stale`, `mismatched`, `duplicate`, or `conflicting` | `unusable_required_report` | `blocked` |
| optional any kind | produced report is any preceding non-current freshness status | `optional_excluded` | excluded |

After applying the matrix, aggregate the non-excluded contributions with this strict order:

```
fail > blocked > human_review_required > inconclusive > pass
```

The highest-precedence contribution is the aggregate verdict. If and only if there are zero contributions after valid optional exclusions, the aggregate verdict is `inconclusive`. A required unusable report contributes `blocked`; it never becomes an evaluation error. `aggregate_confidence` is the least confidence among current report bindings that contribute a verdict, using `high < medium < low`; if no current binding contributes, it is exactly `low`. Stale, superseded, invalidated, mismatched, duplicate, conflicting, and unavailable branches never contribute confidence.

`requirement_results` contains one declaration-order entry per requirement with `requirement_id`, `requirement_kind`, `required`, `dispatch_status`, `report_verdict_or_null`, `freshness_or_null`, `result_kind`, and either `verdict_contribution` or `excluded_reason`. `freshness_assessments` contains one entry per produced binding in the deterministic group order.

## 5. Result/Error XOR and Error Taxonomy

Every valid matrix cell returns `ValidatorMeshResult`, including report verdicts `fail`, `blocked`, `inconclusive`, and `human_review_required`; missing baselines; unavailable required extensions; stale or mismatched bindings; duplicates; and conflicts. Validity is determined before the matrix. A result and error never coexist.

Errors are limited to malformed, internally inconsistent, or unsupported input. First match wins in this disjoint order:

| Order | Error code | Predicate |
|---|---|---|
| 0 | `invalid_mesh_declaration` | declaration is not structurally readable, has unsupported mesh version, empty requirements, invalid required declaration fields, or a declaration ArtifactRef lacks required comparison facts |
| 1 | `invalid_mesh_request` | root evaluation request is malformed other than a later listed integrity predicate |
| 2 | `duplicate_requirement_id` | two declaration requirements share an id |
| 3 | `dispatch_count_mismatch` | result count differs from requirement count |
| 4 | `duplicate_dispatch_request_id` | two results use the same dispatch id |
| 5 | `orphan_dispatch_result` | a result cannot map to a declared requirement |
| 6 | `invalid_dispatch_result` | status/binding conditional shape is malformed or unsupported |
| 7 | `invalid_report_binding` | a produced binding is incomplete or internally inconsistent, including report digest disagreement |

The v1.0 aggregate error codes `zero_valid_contributions`, `required_requirement_blocked`, and `baseline_missing_unresolved` are explicitly deprecated in v1.1.0 and remain unreachable in v1.2.0. They are valid result branches in Section 4, respectively yielding `inconclusive`, `blocked`, and the declared baseline policy. No active error code overlaps a Section 4 branch.

## 6. Output Preservation and Publish Bridge

The optional Gate bridge is a deterministic projection of an already-returned `ValidatorMeshResult`. It may map its aggregate verdict to the frozen Gate Decision Contract fields, but it cannot revise the Mesh result and cannot cause the Mesh evaluator to return an error for a valid matrix cell. The bridge does not append RuntimeEvents, write storage, dispatch, retry, publish, or change lifecycle state.

`recommended_action` is the stable direct mapping: `pass -> proceed`, `fail -> stop_run`, `blocked -> more_evidence`, `inconclusive -> human_intervention`, and `human_review_required -> human_intervention`. This advisory field does not authorize a post-return verdict replacement.

## 7. Independent Enumeration Requirements

An independent validator derives, from Sections 2-5, the Cartesian matrix of requirement kind, required flag, missing policy where applicable, canonical dispatch status, report verdict, and freshness. It verifies that every valid cell selects one row of Section 4 and returns one aggregate result; then separately verifies that every Section 5 code has a malformed, inconsistent, or unsupported witness and no Section 4 witness. Runner prose is not evidence for either enumeration.

## 8. Version History

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-02 | Initial Mesh contract. |
| 1.1.0 | 2026-08-03 | Totalized baseline/extension and freshness contributions; defined offline ContractKey and TargetKey comparison; made cross-requirement duplicate/conflict representable; deprecated aggregate error branches; and prohibited post-return output mutation. |
| 1.2.0 | 2026-08-10 | Made stale reachable through nullable closed `ReportArtifactRef` with explicit `missing_fields`; added caller-bound `report_confidence` to `ValidationReportBinding`; and froze explicit aggregate confidence precedence without changing production evaluator code. |

## 9. Public Hygiene

This public, ASCII-only contract contains no credentials, Control paths, lifecycle writes, filesystem operations, hidden authority, or model judgment. It does not authorize changes outside this contract's non-authority boundary.
