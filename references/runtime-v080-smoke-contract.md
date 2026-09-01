# Runtime v0.8 Smoke Scenario Contract

**Document ID**: railyard-runtime-v080-smoke-contract-v1.2.0

**Version**: 1.2.0

## 1. Purpose and Boundary

This contract fixes the previously identified smoke authority boundary. It is a catalog-facing specification for the frozen `examples/runtime_v080_smoke/conformance.json` catalog and the immutable production callable shapes. It does not modify the catalog, executor, Mesh evaluator, sidecar, schemas, tests, or runtime behavior.

The smoke executor records the exact production call ledger and exposes the production `ValidatorMeshResult` or `ValidatorMeshEvaluationError` unchanged. It may project stable fields into a summary, but it MUST NOT replace or simulate a verdict, recommendation, failure code, requirement result, freshness assessment, or error. Raw verification remains independent from stable summary projection under the deterministic stable-summary boundary. The scenario-012 deep-copy tamper correction remains unchanged.

## 2. Call and RuntimeEvent Boundary

For every scenario, the frozen catalog pipeline is the sole call ledger authority. A production call is exactly one catalog pipeline operation mapped through the catalog dispatch table. A RuntimeEvent exists only where an explicit `runtime_state_sidecar` catalog step declares an event type.

For scenarios 003 through 011, the exact and complete ledger is:

```
runtime_state_sidecar.create_run
runtime_validator_dispatch.dispatch
runtime_validator_mesh.evaluate_validator_mesh
```

Their only RuntimeEvent is `run.created`, from the explicit `create_run` Sidecar call. There is no `run.started`, `run.stage.started`, gate event, completion event, later call, implicit bridge, or inferred event. "Zero-later-call" means no production call after `evaluate_validator_mesh` and no RuntimeEvent after `run.created`; it does not assert that the database was never changed by the permitted Sidecar call.

## 3. Exact Catalog Matrix

The following is an exact operation and Sidecar-event transcription of the frozen 20-scenario catalog. Repeated operation names are distinct catalog calls in listed order.

| Scenarios | Operations | RuntimeEvents |
|---|---|---|
| 001, 002 | `create_run, append_event, append_event, dispatch, evaluate_validator_mesh, publish_to_gate, append_event, commit_stage, export_run, publish_to_gate` | `run.created, run.started, run.stage.started, run.gate.evaluated, run.stage.completed` |
| 003-011 | `create_run, dispatch, evaluate_validator_mesh` | `run.created` |
| 012 | `create_run, append_event, append_event, dispatch, evaluate_validator_mesh, publish_to_gate, append_event, commit_stage, export_run, publish_to_gate` | `run.created, run.started, run.stage.started, run.gate.evaluated, run.stage.completed` |
| 013 | `create_run, append_event, append_event, evaluate_runtime_action, dispatch, evaluate_validator_mesh, publish_to_gate, append_event, commit_stage, export_run, publish_to_gate` | `run.created, run.started, run.stage.started, run.gate.evaluated, run.stage.completed` |
| 014 | `create_run, evaluate_runtime_action, dispatch, evaluate_validator_mesh` | `run.created` |
| 015 | `create_run, evaluate_runtime_action, export_run, publish_to_gate` | `run.created` |
| 016 | `create_run, evaluate_runtime_action, dispatch, evaluate_validator_mesh` | `run.created` |
| 017 | `create_run, append_event, append_event, evaluate_runtime_action, dispatch, evaluate_validator_mesh, publish_to_gate, append_event, commit_stage, export_run, publish_to_gate` | `run.created, run.started, run.stage.started, run.gate.evaluated, run.stage.completed` |
| 018-020 | `create_run, append_event, append_event, dispatch, evaluate_validator_mesh, publish_to_gate, append_event, commit_stage, export_run, publish_to_gate` | `run.created, run.started, run.stage.started, run.gate.evaluated, run.stage.completed` |

`append_event` and `commit_stage` are Sidecar calls. `dispatch`, `evaluate_validator_mesh`, `evaluate_runtime_action`, `publish_to_gate`, and `export_run` return in-memory production values and do not themselves add RuntimeEvents.

## 4. Mesh Failure Scenario Inputs

Scenarios 003-011 use the three-call boundary in Section 2 and declare semantic inputs sufficient to build a production Mesh request. In addition to catalog run, timestamp, identity, and seed facts, their fixed matrix inputs are below. These are contract requirements for future compatible fixtures/executors; they do not authorize rewriting the frozen catalog.

| Scenario | Requirement kind | Required | Missing policy | Canonical dispatch status | Report verdict | Binding fact | Expected Mesh result |
|---|---|---:|---|---|---|---|---|
| 003 | extension | true | n/a | `no_report` | null | none | `blocked` |
| 004 | baseline | true | `fail` | `report_produced` | `fail` | `current` | `fail` |
| 005 | baseline | true | `fail` | `report_produced` | `blocked` | `current` | `blocked` |
| 006 | baseline | true | `fail` | `report_produced` | `inconclusive` | `current` | `inconclusive` |
| 007 | baseline | true | `human_review_required` | `no_report` | null | none | `human_review_required` |
| 008 | baseline | true | `fail` | `report_produced` | any | `mismatched: target_artifact_binding` | `blocked` |
| 009 | baseline | true | `fail` | `report_produced` | any | `mismatched: contract_binding` | `blocked` |
| 010 | extension | true | n/a | `report_produced` | distinct current candidates | `conflicting` ComparisonKey group across two requirements | `blocked` |
| 011 | extension | true | n/a | `unreachable` | null | none | `blocked` |

Scenario 007 may instead use `report_produced + current + human_review_required`; its declared outcome is the same. Each selected fixture uses one row, never an executor-side result correction. Scenario 010 has two unique requirement ids, one result and one binding per requirement, the same validator/contract/target ComparisonKey, and differing report digest or verdict. It is structurally representable under the Mesh contract.

## 5. Scenario-Specific Assertions

For scenarios 003-011, the smoke verifier asserts all of the following from raw production outputs and the frozen catalog:

1. Call ledger length is exactly three, in the Section 2 order.
2. Journal contains exactly `run.created` and no event after it.
3. `evaluate_validator_mesh` output is exposed unchanged as `ValidatorMeshResult` or `ValidatorMeshEvaluationError`.
4. The fixed inputs select the Section 4 Matrix row above; expected outcomes are 003 blocked missing required extension, 004 fail report, 005 blocked report, 006 inconclusive report, 007 human-review result, 008 blocked target mismatch, 009 blocked contract mismatch, 010 blocked conflict, and 011 blocked required extension unreachable.
5. No call to `publish_to_gate`, `append_event`, `commit_stage`, `export_run`, or `evaluate_runtime_action` occurs after Mesh evaluation.

A valid non-pass Mesh result is not a smoke failure or an evaluator error. It is the expected typed output for its scenario. A malformed request may expose an unchanged `ValidatorMeshEvaluationError`, but no scenario may synthesize one to replace a valid Mesh result.

## 6. Pre-Fix Defects Recorded

Version 1.1 incorrectly described a five-call, three-event failure prefix for scenarios 003-011 despite the frozen catalog declaring three calls and one Sidecar event. It also allowed an executor-side `aggregate_verdict` correction after `evaluate_validator_mesh` returned. Neither statement is authoritative in v1.2.0.

The related Mesh v1.0 defects are resolved by `references/runtime-validator-mesh-contract.md` v1.1.0: contradictory missing-baseline/required-unreachable behavior, incomplete target/contract comparison facts, non-representable conflict wording, and aggregate errors for valid non-pass inputs.

## 7. Scenario 012 and Later Scenarios

Scenario 012 retains v1.1.0's correction: the smoke verifier creates and mutates a deep copy of a valid exported envelope, detects the digest mismatch itself, preserves the original production output, and suppresses the final publish operation when appropriate. No production output is mutated.

Scenarios 013-020 retain their frozen catalog operations, dependencies, raw-verification rules, stable-summary boundary, and visibility/recovery meanings. This remediation only changes the authority text for Mesh failure semantics and records the exact catalog matrix in Section 3.

## 8. Version History

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-07-31 | Initial 20-scenario smoke contract. |
| 1.1.0 | 2026-08-02 | Established catalog call-ledger authority, Sidecar RuntimeEvent boundary, and scenario-012 verifier correction. |
| 1.2.0 | 2026-08-03 | Made scenarios 003-011 exactly three calls and one event, froze their Mesh input/outcome matrix, prohibited post-return output mutation and simulated verdicts, and recorded the exact 20-scenario catalog matrix. |

## 9. Versioning and Hygiene

This contract is version `1.2.0`. It is public and ASCII-only. It introduces no runtime, lifecycle, release, schema, test, catalog, or Control authority and authorizes no output mutation after a production return.
