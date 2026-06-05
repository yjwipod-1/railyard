---
name: primitive-coverage-status
description: Coverage status of deterministic validation primitives in the executable validator, documenting gaps between the primitive registry and current fixture coverage
type: reference
version: 0.7.2
---

# Primitive Coverage Status

This document records the coverage status of deterministic validation
primitives (defined in `references/validation-primitive-registry.md`) relative
to the executable Validator implementation in `scripts/validator.py` and its
associated primitive fixture suite under `examples/primitive_fixtures/`.

All coverage is **executable coverage**: each fixture exercises the
`validator.py` binary with a specific input and asserts the expected
`overall_verdict` and decisive findings. Fixtures do not independently
reimplement registry check logic; they validate the executable's observed
behavior.

## 1. Currently Covered Primitives

The executable validator (`scripts/validator.py`) implements 6 of the 26+
registry primitives. The 11 primitive fixture directories cover these 6
primitives as follows:

| Primitive | Registry Sec. | Pass | Fail | Blocked | Inconclusive |
|---|---|---|---|---|---|
| `source_availability` | 7.3 | 1 fixture | - | - | - |
| `field_mapping_required` | 3.1 | 1 fixture | 1 fixture | - | - |
| `value_transform_correctness` | 4.3 | 1 fixture | 1 fixture | - | - |
| `signed_numeric_preservation` | 4.2 | 1 fixture | 1 fixture | - | - |
| `record_identity_preservation` | 6.1 | 1 fixture | 1 fixture | - | - |
| `unmapped_field_availability` | 7.1 | 1 fixture | - | - | 1 fixture |

**Total: 11 fixtures covering 6 primitives.**

All fixtures map to the **executable validator's actual behavior** as
implemented in `scripts/validator.py`. They validate that the current code
path produces the expected report shape and verdict for each configured
input. The fixture set does not independently verify the registry's check
logic; rather, it captures the current implementation's observable semantics.

## 2. Executable Behavior vs. Registry Spec: `source_availability`

### Registry Spec (Sec.7.3)

> Source artifacts declared in the contract MUST be present and readable.
> Missing or unreadable source artifacts MUST produce `status=blocked`
> findings, not silent passes.

The registry primitive expects a general-purpose check: verify each declared
source artifact exists on disk and is parsable. If not, emit a
`source_availability` finding with `status=blocked`.

### Executable Behavior

The executable validator (`scripts/validator.py`) has two distinct code paths:

1. **`artifact_read`** (validator-specific, lines 724-736 of `run_validator`):
   During artifact loading, each referenced artifact file is read from disk.
   If a file is missing or contains invalid JSON, an `artifact_read` finding
   is emitted with `status=blocked`. If any artifact fails to load, the
   validator returns immediately at line 741-752 with `overall_verdict=blocked`
   **without** proceeding to `validate_source_to_derived()`.

2. **`source_availability`** (registry-aligned, lines 341-348 of
   `validate_source_to_derived`): After all artifacts have been loaded
   successfully, this check fires only when **neither** a `source`-kind nor a
   `derived`-kind artifact entry exists in the artifacts dict. This is a
   structural-absence check (e.g. the input JSON's `artifacts` array has no
   entry with `"kind": "source"`), not a file-readability check.

### Coverage Gap

The registry's `source_availability` blocked path - an artifact file that
exists in the file system but is unreadable - is **not exercised by the
executable `source_availability` primitive**. Instead:

| Scenario | What fires | Primitive type | Coverage |
|---|---|---|---|
| All artifacts present and readable | `source_availability` pass | Registry-aligned | yes (pass fixture exists) |
| Artifact file missing or invalid JSON | `artifact_read` blocked | Validator-specific | no fixture |
| Artifacts array lacks source/derived kind | `source_availability` blocked | Registry-aligned | no fixture |

The **blocked** path of `source_availability` (as defined in the registry) is
structurally unreachable when the blocking cause is a file-read failure,
because `artifact_read` at the I/O layer intercepts before
`validate_source_to_derived()` begins.

The `primitive-source-availability-pass` fixture exercises the happy path:
all artifacts load successfully, `source_availability` finds no issue, and
the verdict is `pass`. A hypothetical
`primitive-source-availability-blocked` fixture would need to trigger the
structural-absence path (no `source`-kind artifact in the input), not a file
I/O error - because a file I/O error produces an `artifact_read` finding, not
a `source_availability` finding.

**The name `source_availability` in the executable validator is narrower than
its registry definition.** The registry primitive implies a general-purpose
source-readability gate; the executable primitive is a structural-presence
check that fires only after the I/O layer has already verified readability.

## 3. Implications

### For Fixture Authors

- A fixture that provides intentionally invalid JSON for a source artifact
  (as in the current `artifact_read` code path) exercises
  **validator-specific behavior**, not the `source_availability` registry
  primitive. Its `primitive_id` in `primitive-fixture.json` should reflect
  the primitive whose behavior it actually tests.
- To exercise the `source_availability` blocked path per the registry, a
  fixture must provide a `validator-input.json` whose `artifacts` array
  structurally lacks a `source`-kind entry while the derived and contract
  entries are loadable. This tests the structural-absence check.

### For Registry Evolution

- If the registry's `source_availability` is intended to cover file I/O
  failures, the executable validator would need to reclassify or restructure
  the `artifact_read` responsibility. This is a design decision, not a bug:
  the current architecture separates I/O infrastructure
  (`artifact_read`) from semantic validation (`source_availability`).
- Alternatively, the registry entry for `source_availability` could be
  scoped to structural presence only, with I/O failures treated as a
  separate infrastructure concern outside the primitive registry.

### Coverage Completeness

- 5 of the 6 aligned primitives have at least pass/fail fixture coverage.
- `unmapped_field_availability` has pass/inconclusive but no fail fixture.
- `source_availability` has pass but no blocked fixture.
- Neither gap affects current executable correctness: the code paths are
  exercised indirectly (e.g. `artifact_read` covers I/O failure; other
  fixtures exercise fail verdicts for other primitives).

## 4. Non-Covered Primitives

The following registry primitives have **no executable implementation and no
fixture coverage** in v0.7.2:

| Category | Primitives without coverage |
|---|---|
| Source Replay (Sec.2) | `source_replay_provenance`, `source_replay_completeness`, `source_replay_fidelity` |
| Declared Field Mapping (Sec.3) | `field_mapping_target_resolution`, `field_mapping_no_undeclared` |
| Value/Sign Preservation (Sec.4) | `source_value_preservation`, `numeric_precision_preservation` |
| Formula Recompute (Sec.5) | `formula_recompute_match`, `formula_input_availability`, `formula_declaration_consistency` |
| Record/Key Reconciliation (Sec.6) | `record_key_set_match`, `record_count_reconciliation` |
| Availability Handling (Sec.7) | `evidence_availability` |
| Claim Grounding (Sec.8) | `claim_source_grounding`, `claim_precision_grounding`, `claim_chain_integrity` |
| Publish Gate Aggregation (Sec.9) | `publish_gate_aggregate`, `publish_gate_completeness`, `publish_gate_independence` |

These primitives are defined in the registry as generic check patterns.
Adding executable coverage requires both an implementation in the Validator
and a corresponding fixture. No timeline is assigned; this document records
the current gap.

## 5. Validator-Specific Checks (No Registry Counterpart)

The executable validator also emits findings for checks that have no
corresponding registry primitive. These are infrastructure or methodology
checks scoped to the reference implementation:

| Check | Purpose | Has fixture? |
|---|---|---|
| `artifact_read` | I/O-layer artifact loading | Indirectly (via invalid JSON) |
| `candidate_output_not_truth_source` | Truth-hierarchy methodology | No |
| `declared_transform_only` | Validator capability constraint | No |
| `record_materialization` | Structural prerequisite | No |
| `unsupported_contract` | Contract-type routing | No |
| `validator_input` | Input-loading infrastructure | No |
| `warning_policy` | Meta-policy (warnings_as_errors) | No |

These are not gaps - they are validator-internal checks that do not belong in
the generic primitive registry.
