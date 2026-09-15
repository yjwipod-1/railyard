---
name: runtime-artifact-visibility-contract
description: Versioned amendment defining canonical RuntimeArtifact visibility, VisibilityContributor, VisibilityResolution, run-level visibility_context, no-downgrade invariants, event-field migration crosswalk, and export derivation rules
type: contract
version: 1.0.0
governing_contracts:
  - artifact_id: runtime-architecture
    artifact_kind: contract
    artifact_version: 0.8.0
    locator: references/runtime-architecture.md
  - artifact_id: knowledge-contract
    artifact_kind: contract
    artifact_version: 0.8.0
    locator: references/knowledge-contract.md
  - artifact_id: runtime-state-contract
    artifact_kind: contract
    artifact_version: 0.9.0
    locator: references/runtime-state-contract.md
  - artifact_id: runtime-evidence-export-contract
    artifact_kind: contract
    artifact_version: 1.0.0
    locator: references/runtime-evidence-export-contract.md
risk_level: high
validator_required: true
visibility: public
amendment: true
amendment_target: runtime-state-contract
amendment_status: incorporated
amendment_reason: Incorporated into references/runtime-state-contract.md v0.9.0. Visibility shapes, resolution rules, visibility_context, and no-downgrade invariants defined here are now normative for the runtime-state contract.
---

# Runtime Artifact Visibility Amendment Contract

This contract defines the canonical shapes, resolution rules, and invariants for
RuntimeArtifact visibility. This amendment was incorporated into
`references/runtime-state-contract.md` v0.9.0. The authoritative visibility
shapes defined here are the normative reference for the runtime-state contract.

The runtime-state contract v0.9.0 is the authoritative execution baseline
incorporating this amendment.

## 1. Canonical RuntimeArtifact Visibility Shape

### 1.1 RuntimeArtifact

Every RuntimeArtifact produced or consumed during runtime execution carries
explicit visibility metadata. The canonical shape extends the generic
`ArtifactRef` from `references/knowledge-contract.md` Sec.1.1 with runtime
provenance and visibility resolution fields.

| Field | Type | Required | Contract |
|---|---|---|---|
| `artifact_ref` | ArtifactRef | Yes | Base artifact identity: `artifact_id`, `artifact_kind`, optional `artifact_version`, `locator`, `digest`. |
| `origin_run` | string | Yes | Non-empty `run_id` of the producing run. |
| `origin_stage` | string | No | `stage_id` of the producing stage. Required when a specific stage produced the artifact. |
| `produced_by` | string | Yes | Non-empty executor identity. |
| `source_artifacts` | array of ArtifactRef | Yes | Zero or more contributing source artifacts. |
| `visibility` | string | Yes | One of `public`, `project`, or `restricted`. The resolved visibility for this artifact. |
| `visibility_resolution` | VisibilityResolution | Yes | Complete resolution evidence (Sec.1.3). |

Additional runtime metadata (size, format, MIME type, content digest) is outside
this contract's scope and may be defined by a future runtime artifact contract
extension.

### 1.2 VisibilityContributor

A `VisibilityContributor` is a single source of visibility evidence for one
artifact or one run. Every contributor carries explicit identity, kind,
asserted visibility, and authority or classification evidence.

| Field | Type | Required | Contract |
|---|---|---|---|
| `contributor_id` | string | Yes | Stable, non-empty identity for this contributor. Unique within the artifact's resolution scope. |
| `contributor_kind` | string | Yes | One of `source_artifact`, `governing_contract`, `project_policy`, or `trigger_provenance`. |
| `contributor_ref` | ArtifactRef | Yes | Typed reference to the contributing source, contract, or policy. For `trigger_provenance`, `artifact_kind` MUST be `ticket`, `pipeline_config`, `script`, or `request_artifact`. For `project_policy`, `artifact_kind` MUST be `policy`. |
| `asserted_visibility` | string | Yes | One of `public`, `project`, or `restricted`. The explicit visibility this contributor requires. |
| `authority` | string | Yes | Non-empty text describing the authority or classification basis (e.g., contract declaration, policy rule, source classification). |
| `classification_evidence` | array of ArtifactRef | Yes | At least one typed evidence reference supporting the asserted visibility. |

Absence of a restricted marker is never evidence of public visibility. Every
visibility claim must be positively supported by `authority` and
`classification_evidence`.

### 1.3 VisibilityResolution

`VisibilityResolution` is the recorded, deterministic resolution of visibility
for one artifact or one run. It captures every contributor, the resolution
outcome, and the resolution audit trail.

| Field | Type | Required | Contract |
|---|---|---|---|
| `resolution_id` | string | Yes | Stable, globally unique identifier for this resolution. |
| `resolved_at` | string | Yes | ISO 8601 timestamp when resolution was computed. |
| `contributors` | array of VisibilityContributor | Yes | Non-empty. Every contributor whose visibility was considered. |
| `resolution_rule` | string | Yes | Exactly `most_restrictive`. The only permitted resolution rule in this contract version. |
| `resolved_visibility` | string | Yes | One of `public`, `project`, or `restricted`. Computed by the resolution rule over all contributors. |
| `resolution_audit` | object | Yes | Audit trail containing `contributor_count`, `restricted_count`, `project_count`, `public_count`, and `applied_rule`. |

**Most-restrictive resolution:** The resolved visibility is determined by the
partial order `restricted > project > public`. If any contributor asserts
`restricted`, the resolution is `restricted`. If no contributor asserts
`restricted` and at least one asserts `project`, the resolution is `project`. If
all contributors assert `public`, the resolution is `public`.

An empty contributors array is non-conforming -- at least one contributor is
required for any resolution.

### 1.4 Failure codes

The following stable failure codes apply to visibility resolution and
verification:

| Code | Description |
|---|---|
| `visibility_contributor_missing` | A required contributor is absent from the resolution set. |
| `visibility_contributor_conflict` | Two contributors assert different values for the same identity without deterministic resolution. |
| `visibility_invalid_value` | An asserted visibility value is not one of `public`, `project`, `restricted`. |
| `visibility_no_evidence` | A contributor has an empty or missing `classification_evidence` array. |
| `visibility_downgrade` | An artifact or envelope visibility is less restrictive than its resolved visibility. |
| `visibility_omitted_restricted` | A `restricted` contributor existed but was not included in the resolution. |
| `visibility_context_missing` | A `run.created` event does not contain the required `visibility_context`. |

## 2. Run-Level Visibility Context

### 2.1 visibility_context

The `run.created` event payload MUST contain a `visibility_context` object.
This ensures that even a run that produces no runtime artifacts still records
the trigger, policy, and governing-contract visibility inputs and a resolved
run visibility suitable for a later offline export verifier.

| Field | Type | Required | Contract |
|---|---|---|---|
| `visibility_context.trigger_visibility` | VisibilityContributor | Yes | Visibility from the triggering source (ticket, pipeline config, script, or request artifact). |
| `visibility_context.policy_contributors` | array of VisibilityContributor | Yes | At least one project-policy contributor. May be empty only when a `governing_contract` contributor explicitly declares a visibility policy that covers the trigger scope. |
| `visibility_context.contract_contributors` | array of VisibilityContributor | Yes | At least one governing-contract contributor. |
| `visibility_context.run_visibility_resolution` | VisibilityResolution | Yes | Resolved run visibility from all contributors in the context. |
| `visibility_context.resolved_run_visibility` | string | Yes | One of `public`, `project`, or `restricted`. Must equal `run_visibility_resolution.resolved_visibility`. |

### 2.2 Run-visibility derivation for export

A complete-run export (per `references/runtime-evidence-export-contract.md`)
derives its envelope `visibility_basis` from recorded run and artifact
visibility facts, never from newly asserted claims by the exporter.

The export derivation rule is:

1. The exporter reads `run.created` payload `visibility_context` to obtain the
   run-level resolved visibility, trigger contributor, policy contributors,
   and contract contributors.
2. The exporter reads every runtime artifact produced during the run, each
   with its own `visibility` and `visibility_resolution`.
3. The exporter builds the export `visibility_basis` array by copying every
   contributor from both the run-level context and every artifact-level
   resolution, deduplicating by `(contributor_kind, contributor_ref.artifact_id,
   contributor_ref.artifact_kind)`.
4. The exporter computes the most-restrictive resolution over all basis entries.
5. The exporter sets the envelope `visibility` to the resolved value.
6. The exporter MUST NOT reclassify, reinterpret, upgrade, or downgrade any
   recorded visibility value. The export basis is a faithful copy of recorded
   facts.

### 2.3 Resolved run visibility derivation

The run-level `resolved_run_visibility` is the most-restrictive value across:

1. Every contributor in the initial `visibility_context.run_visibility_resolution.contributors`.
2. The `visibility` field of every `RuntimeArtifact` appended to the
   projection's `runtime_artifacts` array.

The derivation is:

```
let initial = visibility_context.run_visibility_resolution.resolved_visibility
for each artifact in projection.runtime_artifacts:
    if artifact.visibility is stricter than current:
        current = artifact.visibility
resolved_run_visibility = current
```

When a run has no produced artifacts, `resolved_run_visibility` equals the
initial `visibility_context.resolved_run_visibility`. When a stage produces an
artifact with a stricter visibility, only `resolved_run_visibility` tightens;
the immutable `visibility_context` and every artifact's own
`visibility_resolution` remain unchanged.

## 3. No-Downgrade Invariants

### 3.1 Produced-artifact visibility

When a stage produces a runtime artifact:
- The artifact's `visibility` MUST equal its `visibility_resolution.resolved_visibility`.
- Every source artifact that contributed content MUST appear as a `source_artifact` in the artifact and as a `contributor_kind: source_artifact` contributor in the resolution.
- The artifact's resolved visibility MUST NOT be less restrictive than any contributing source artifact's visibility.

### 3.2 Aggregate-artifact visibility

When an artifact aggregates content from multiple sources with different
visibilities:
- The artifact's resolved visibility is the most restrictive among all sources.
- Every source MUST appear as a contributor in the resolution.
- The aggregate MUST NOT be published at a visibility less restrictive than the most restrictive source.
- The `resolution_audit` MUST record the count of restricted, project, and public contributors.

### 3.3 Absence is not evidence

A missing restricted marker, an absent visibility field, or an artifact without
explicit visibility classification is never evidence of public visibility.
Processing that encounters an unclassified artifact MUST:
- Treat it as `resolution_required` (not public).
- Record it as missing evidence with `visibility_contributor_missing`.
- Not produce a `public` resolution for the artifact.

### 3.4 Run-visibility downgrade prevention

A run's `resolved_run_visibility` MUST NOT be less restrictive than the most
restrictive visibility among all artifacts produced during the run. A run that
produced a `restricted` artifact MUST have its run visibility resolved as
`restricted`, even if the trigger and policy contributors are all `public`.

## 4. Event-Field Migration Crosswalk

### 4.1 Artifact-bearing payloads

The following runtime event payloads carry or reference artifacts. The target
schema column shows the field name in the future amended payload schema. This
crosswalk does not modify the current payload schemas; it defines where
visibility fields will be inserted when the runtime-state contract is amended.

| Event type | Current artifact-bearing field(s) | Target visibility addition | Migration rule |
|---|---|---|---|
| `run.created` | `run_provenance` | `visibility_context` (new top-level field) | Add as required field. Existing events are non-conforming until migration. |
| `run.stage.completed` | `artifacts_produced` | Each element becomes a RuntimeArtifact with `visibility` + `visibility_resolution` | Transform artifact references into RuntimeArtifacts. Preserve all existing fields. |
| `run.stage.failed` | `artifacts_produced_before_failure` | Each element becomes a RuntimeArtifact with `visibility` + `visibility_resolution` | Same transformation as stage.completed. |
| `run.gate.evaluated` | `evidence` | Each ArtifactRef in evidence carries visibility from its source | Read artifact visibility from origin run/store. Do not reclassify. |
| `run.gate.blocked` | `evidence` | Each ArtifactRef in evidence carries visibility from its source | Same rule as gate.evaluated. |
| `run.gate.overridden` | `evidence` | Each ArtifactRef in evidence carries visibility from its source | Same rule as gate.evaluated. |
| `run.intervention` | `evidence` | Each ArtifactRef in evidence carries visibility from its source | Same rule as gate.evaluated. |

### 4.2 Migration compatibility

The migration rule for every event type is: add new fields, preserve all
existing fields. No existing field is renamed, removed, or re-typed. A
consumer that ignores the new visibility fields still receives all previously
defined fields unchanged.

## 5. Conformance Contract

### 5.1 Schema validation

The JSON Schema at `assets/schemas/runtime-artifact-visibility-v1.schema.json`
validates the structural shape of RuntimeArtifact, VisibilityContributor,
VisibilityResolution, visibility_context, and all conformance cases.

### 5.2 Conformance cases

`examples/runtime_artifact_visibility_contract/conformance.json` provides
minimal, generic conformance cases covering:

| Case id | Scenario | Expected behavior |
|---|---|---|
| `public-single` | Single public artifact from public source | resolved=public |
| `project-single` | Single project artifact from project source | resolved=project |
| `restricted-single` | Single restricted artifact from restricted source | resolved=restricted |
| `aggregate-most-restrictive` | Artifact from public + project + restricted sources | resolved=restricted |
| `missing-evidence` | Contributor with empty classification_evidence | rejected: visibility_no_evidence |
| `conflicting-contributor` | Two contributors with same identity, different values | rejected: visibility_contributor_conflict |
| `downgrade` | Artifact visibility less restrictive than resolved | rejected: visibility_downgrade |
| `run-without-artifacts` | Run with only visibility_context, no produced artifacts | resolved_run_visibility matches context |

All cases use generic EXAMPLE identifiers with the `example-` prefix. No
machine-local paths, secrets, credentials, Control IDs, private domains, or
internal development notes appear in any public artifact.

### 5.3 Amendment boundary

This contract, its schema, and its conformance cases were design artifacts that
defined shapes and rules now incorporated into runtime-state-contract v0.9.0.
The shapes defined in Sec.1-Sec.4 are the normative reference. The frozen
runtime-state contract v0.9.0 is the execution baseline. This amendment does not:

- Modify the runtime-state contract v0.9.0 -- the contract is authoritative.
- Implement runtime behavior, event recording, or artifact production.
- Implement export behavior or export content digest computation.
- Implement migration of existing event payloads.

These shapes and rules were incorporated into the runtime-state contract
v0.9.0 with conformance fixture validation.

## 6. Non-Goals

This contract does NOT define or prescribe:

- Implementation of visibility resolution at runtime.
- Event payload migration execution.
- Exporter implementation or export content digest computation.
- Scripts, validators, generators, or test harnesses.
- Persistence, storage, or serialization of visibility records.
- CLI tools, server endpoints, or HTTP APIs.
- Workflow integration or lifecycle transitions.
- Any modification to the runtime-state contract v0.9.0, its conformance
  fixtures, its harness, its core, journal, projection, export contract, or
  export example (these are authoritative and governed by the runtime-state
  contract itself).

## 7. Governance

| Field | Value |
|---|---|
| Contract version | 1.0.0 |
| Governing contracts | `references/runtime-architecture.md` v0.8.0, `references/knowledge-contract.md` v0.8.0, `references/runtime-state-contract.md` v0.9.0, `references/runtime-evidence-export-contract.md` v1.0.0 |
| Risk level | high |
| Validator required | true |
| Visibility | public |
| Amendment | true (incorporated into runtime-state-contract v0.9.0) |
| Amendment target | `references/runtime-state-contract.md` |
