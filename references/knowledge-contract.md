---
name: knowledge-contract
description: Public contract for canonical Knowledge entries, typed provenance and evidence, relationships, lifecycle events, consumer mappings, and role ownership
type: contract
version: 0.8.0
---

# Knowledge Contract

This contract defines the portable shape, visibility boundary, and ownership of Knowledge. It is a contract for future producers and consumers; it does not implement extraction, persistence, retrieval, ranking, or runtime orchestration.

## 1. Canonical Types

### 1.1 ArtifactRef

`ArtifactRef` is the only machine-resolvable artifact reference in this contract. A field described as an artifact reference MUST contain this object, never a scalar identifier or path.

| Field | Type | Required | Contract |
|---|---|---|---|
| `artifact_id` | string | Yes | Stable non-empty identity in the artifact's authority domain. |
| `artifact_kind` | string | Yes | Non-empty kind. Canonical kinds used here are `epic`, `ticket`, `contract`, `validation_report`, `reference`, `script`, `runner_result`, and `lifecycle_event`. |
| `artifact_version` | string | No | Version of the referenced artifact. |
| `locator` | string | No | Portable resolver hint; it MUST NOT be a machine-local absolute path. |
| `digest` | string | No | Content digest including algorithm, for example `sha256:...`. |

If `locator` or `digest` is present it supplements identity; it never replaces `artifact_id` or `artifact_kind`. Resolvers MUST match identity first and MUST reject an ambiguous or unresolvable reference.

Example:

```json
{
  "artifact_id": "ticket-lifecycle-contract",
  "artifact_kind": "contract",
  "artifact_version": "0.8.0",
  "locator": "references/lifecycle.md",
  "digest": "sha256:0123456789abcdef"
}
```

### 1.2 Provenance

Every Knowledge entry MUST contain this typed `provenance` object:

| Field | Type | Required | Contract |
|---|---|---|---|
| `origin_epic` | ArtifactRef | Yes | Authorizing or originating Epic; `artifact_kind` MUST be `epic`. |
| `source_tickets` | array of ArtifactRef | Yes | One or more accepted source Tickets; every element's `artifact_kind` MUST be `ticket`. |
| `governing_contract` | ArtifactRef | Yes | Governing specification; `artifact_kind` MUST be `contract` or `reference`. |
| `additional_sources` | array of ArtifactRef | No | Other accepted source artifacts. |

All arrays preserve declared order and MUST NOT contain duplicate `(artifact_kind, artifact_id, artifact_version)` identities. Provenance records sources; it is not a lifecycle log.

### 1.3 Evidence

`evidence` is a required, non-empty array of `ArtifactRef`. Human explanation belongs in the separate optional `evidence_notes` array of non-empty strings. A producer or consumer MUST NOT place prose, paths, section citations, or scalar artifact identifiers in `evidence`.

## 2. Canonical Knowledge Entry

### 2.1 Entry Schema

| Field | Type | Required | Contract |
|---|---|---|---|
| `entry_id` | string | Yes | Stable, non-empty, corpus-unique identifier. |
| `entry_type` | string | Yes | `technical_fact` or `constraint`. |
| `level` | string | Yes | `domain`, `capability`, `feature`, or `behavior`. |
| `visibility` | string | Yes | `public`, `project`, or `restricted`. |
| `title` | string | Yes | Non-empty human-readable title. |
| `description` | string | Yes | Factual or normative content suitable for the declared `visibility`. |
| `version` | string | Yes | Entry content version. |
| `valid_from` | string | Yes | ISO 8601 timestamp/date or release version. |
| `immutable` | boolean | Yes | If true at acceptance, the accepted entry fields named in Section 6 are frozen. |
| `provenance` | Provenance | Yes | Typed provenance from Section 1.2. |
| `evidence` | array of ArtifactRef | Yes | Typed evidence from Section 1.3. |
| `evidence_notes` | array of string | No | Explanatory notes only. |
| `relationships` | array of Relationship | Yes | Typed union from Section 4; may be empty only for a `domain`. |
| `constraint_kind` | string | Conditional | Required only for `constraint`; `invariant`, `guard`, or `rule`. |
| `scope` | string | Conditional | Required only for `constraint`; identifies the constrained scope. |

Fields not listed in this table are non-canonical. Lifecycle values such as current confidence, review-required, superseded, archived, or invalidated are projections and MUST NOT be authored into the accepted entry as mutable truth.

### 2.2 Levels and Hierarchy

The adjacent hierarchy is `domain` -> `capability` -> `feature` -> `behavior`.

- A `domain` is the explicit relationship-free root exception. It MUST have zero `part_of` relationships and MAY have an empty `relationships` array.
- Every `capability` MUST have exactly one `part_of` relationship to a resolvable `domain`.
- Every `feature` MUST have exactly one `part_of` relationship to a resolvable `capability`.
- Every `behavior` MUST have exactly one `part_of` relationship to a resolvable `feature`.
- A non-root entry MUST NOT skip a level, have multiple parents, or target itself.
- Every `entry_id` MUST be unique in the corpus. Every Knowledge-entry relationship target MUST resolve to exactly one corpus entry; every artifact target MUST resolve under `ArtifactRef` identity rules.
- The directed `part_of`, `depends_on`, and lifecycle supersession graphs MUST each be acyclic. `part_of` traversal MUST terminate at exactly one `domain` root.

## 3. Eligibility and Aggregation

Knowledge MUST be grounded in accepted source artifacts, stable within a declared validity scope, versioned, suitable for its declared `visibility`, and structurally valid under this contract. Candidate materialization occurs only after its source Tickets are finalised and accepted, its source Epic is authoritative for the scope, and required validation evidence is available.

`public` entries MUST be safe for unrestricted public disclosure. `project` and `restricted` entries MAY contain proprietary technical facts when those facts are accepted, verifiable, and suitable for the declared visibility under project policy. Visibility does not relax grounding, acceptance, evidence, or structural requirements.

The following are always excluded from Knowledge at every visibility: secrets; credentials; tokens; machine-local paths; raw workflow rows; copied workflow state; uncurated private notes; transient session state; unfinished, rejected, or redesigned work; and platform-local configuration. A producer MUST redact or omit such material rather than treating `project` or `restricted` as permission to retain it.

One entry MAY aggregate multiple accepted Tickets. In that case every contributing Ticket MUST appear once in `provenance.source_tickets` and its supporting artifact MUST appear in `evidence` or `provenance.additional_sources`. One Ticket MAY contribute to multiple entries, but each entry remains independently scoped and validated. Conflicting assertions require a lifecycle event with `event_type: "knowledge.review_required"`; the Knowledge Curator does not resolve technical or cross-epic conflicts.

## 4. Relationships

### 4.1 Discriminated Union

Every relationship contains `target_kind`, `relationship`, and optional `scope_note`. It MUST conform to exactly one branch:

| Branch | Required target | Forbidden target |
|---|---|---|
| `target_kind: "knowledge_entry"` | `target_entry_id`: non-empty string | `target_artifact` |
| `target_kind: "runtime_artifact"` | `target_artifact`: ArtifactRef | `target_entry_id` |

Canonical `relationship` values are `part_of`, `depends_on`, `constrained_by`, `implemented_by`, `verified_by`, and `introduced_by`.

### 4.2 Relationship-to-Target Constraints

| Relationship | Required target branch | Additional constraint |
|---|---|---|
| `part_of` | `knowledge_entry` | Exactly the adjacent parent required by Section 2.2. |
| `depends_on` | `knowledge_entry` | Any resolvable entry except self; the graph MUST be acyclic. |
| `constrained_by` | `knowledge_entry` | Target MUST have `entry_type: "constraint"`. |
| `implemented_by` | `runtime_artifact` | `target_artifact.artifact_kind` MUST be `ticket`, `script`, or `reference`. |
| `verified_by` | `runtime_artifact` | `target_artifact.artifact_kind` MUST be `validation_report`. |
| `introduced_by` | `runtime_artifact` | `target_artifact.artifact_kind` MUST be `epic` or `ticket`. |

No relationship may use a scalar artifact target. Supersession is lifecycle state and is represented by the event model in Section 5, not by editing an accepted entry's relationships.

## 5. Append-Only Knowledge Lifecycle

### 5.1 Event Schema

Every lifecycle change is one append-only event with this replayable shape:

| Field | Type | Required | Contract |
|---|---|---|---|
| `event_id` | string | Yes | Globally unique event identity. |
| `event_type` | string | Yes | One of `knowledge.confidence_changed`, `knowledge.review_required`, `knowledge.superseded`, `knowledge.archived`, or `knowledge.invalidated`. |
| `schema_version` | string | Yes | Event schema version. |
| `occurred_at` | string | Yes | ISO 8601 timestamp. |
| `event_order` | integer | Yes | Positive, monotonically increasing order within the Knowledge lifecycle stream. |
| `causation_id` | string | Conditional | Direct predecessor event identity. Required when `causation_chain` is absent. |
| `causation_chain` | array of string | Conditional | Ordered predecessor event identities. Required when `causation_id` is absent. |
| `actor_role` | string | Yes | `knowledge_curator`, `architect`, `validator`, `planner`, or `human`, subject to Section 7 ownership. |
| `trigger_artifact` | ArtifactRef | Yes | Artifact whose accepted lifecycle or evidence triggered this event. |
| `affected_entry_ids` | array of string | Yes | Non-empty, corpus-resolvable entry identities. |
| `prior_state` | KnowledgeLifecycleState | Yes | Projection before the event. |
| `next_state` | KnowledgeLifecycleState | Yes | Projection after the event. |
| `reason` | string | Yes | Non-empty factual reason. |
| `propagation_chain` | array of string | Yes | Ordered, deduplicated entry identities reached after directly affected entries; may be empty. |
| `recommended_action` | string | Yes | `none`, `review`, `supersede`, `archive`, `restore`, or `human_decision`. It is advisory and does not authorize remediation. |

Exactly one of `causation_id` or `causation_chain` MUST be present. The first event in a stream uses an empty `causation_chain`; later events MUST reference earlier event identities and MUST NOT create a causation cycle.

`KnowledgeLifecycleState` is the derived projection payload:

| Field | Type | Required | Contract |
|---|---|---|---|
| `confidence` | string | Yes | `high`, `medium`, or `low`. |
| `review_required` | boolean | Yes | Whether technical or scope review is pending. |
| `superseded` | boolean | Yes | Whether a replacement has superseded the entry. |
| `superseded_by_entry_id` | string | No | Required exactly when `superseded` is true and MUST resolve. |
| `archived` | boolean | Yes | Whether active discovery should exclude the entry. |
| `invalidated` | boolean | Yes | Whether source truth no longer supports the entry. |

Consumers order by `event_order`, break no ties, reject duplicate orders, verify causation, compare each `prior_state` with the preceding derived state, and apply `next_state`. Replaying the same ordered event set MUST produce the same projection. Events are never updated or deleted; corrections append a later event.

### 5.2 Event Example

```json
{
  "event_id": "knowledge-event-0007",
  "event_type": "knowledge.invalidated",
  "schema_version": "0.8.0",
  "occurred_at": "2026-01-15T09:30:00Z",
  "event_order": 7,
  "causation_chain": ["knowledge-event-0005", "knowledge-event-0006"],
  "actor_role": "knowledge_curator",
  "trigger_artifact": {
    "artifact_id": "source-ticket-revision",
    "artifact_kind": "ticket",
    "artifact_version": "2.0.0"
  },
  "affected_entry_ids": ["knowledge-ticket-claim-rule"],
  "prior_state": {
    "confidence": "high",
    "review_required": false,
    "superseded": false,
    "archived": false,
    "invalidated": false
  },
  "next_state": {
    "confidence": "low",
    "review_required": true,
    "superseded": false,
    "archived": false,
    "invalidated": true
  },
  "reason": "An accepted source artifact no longer supports the entry.",
  "propagation_chain": ["knowledge-ticket-lifecycle-feature"],
  "recommended_action": "review"
}
```

## 6. Immutability and Source History

When an entry is accepted with `immutable: true`, its `entry_id`, `entry_type`, `level`, `visibility`, `title`, `description`, `version`, `valid_from`, type-specific fields, `provenance`, `evidence`, `evidence_notes`, and `relationships` are frozen. They MUST NOT be rewritten, patched, reordered, or replaced.

Source provenance and evidence are historical facts. A later Ticket, Contract, report, or source revision is recorded in `trigger_artifact` and lifecycle events; it MUST NOT replace the accepted entry's provenance or evidence. Technical-content changes require a new candidate entry with its own provenance and evidence. Supersession, confidence changes, review-required state, archival, and invalidation are appended as events and exposed only through replay-derived projections.

Visibility is frozen for every accepted entry, regardless of its `immutable` value. A visibility change MUST create a new candidate entry version with its own corpus-unique `entry_id`, `version`, provenance, and evidence. Acceptance of that replacement MUST append a `knowledge.superseded` or `knowledge.invalidated` event for the old version, as applicable; no producer or consumer may relabel the old accepted version in place.

An entry accepted with `immutable: false` may be replaced by a new content version, but prior accepted versions and lifecycle events remain append-only. In-place rewriting MUST NOT erase accepted history.

## 7. Role Ownership

The following wording is canonical and is repeated verbatim in `references/roles.md`:

> Knowledge Curator is the sole role that materializes candidate Knowledge entries from accepted artifacts under this contract, without inventing facts. Architect approves technical correctness. Validator verifies read-only. Runner only produces source artifacts and evidence and is prohibited from materialization, classification, extraction, or curation. Planner owns cross-epic scope and closure. Human resolves unacceptable risk.

The Knowledge Curator may append lifecycle events after the corresponding trigger and authority decision exist, but does not invent technical content or approve correctness. The Knowledge Curator applies visibility only from accepted source artifacts and project policy; it MUST NOT lower a source- or policy-required restriction, and MUST use the more restrictive classification when applicable requirements differ. Architect approval is required before a candidate becomes accepted Knowledge and for technical conflict resolution. Validator reads candidates, source artifacts, evidence, visibility, relationships, and event streams and returns verification evidence only; it does not edit them or change visibility. Planner resolves cross-epic aggregation and closure scope. Human decides escalated unacceptable risk. Runner implementation stops at source artifacts and evidence.

No consumer, Runner, Architect, Validator, Planner, or Human shares candidate-entry materialization ownership with the Knowledge Curator. Runtime and State Model consumers only preserve and replay the typed contract.

## 8. Canonical Examples

### 8.1 Technical Fact

```json
{
  "entry_id": "knowledge-ticket-claim-rule",
  "entry_type": "technical_fact",
  "level": "behavior",
  "visibility": "public",
  "title": "Claim changes a ready ticket to running",
  "description": "A valid claim changes ticket status from ready to running.",
  "version": "1.0.0",
  "valid_from": "0.8.0",
  "immutable": true,
  "provenance": {
    "origin_epic": {
      "artifact_id": "workflow-lifecycle-epic",
      "artifact_kind": "epic",
      "artifact_version": "1.0.0"
    },
    "source_tickets": [
      {
        "artifact_id": "ticket-claim-delivery",
        "artifact_kind": "ticket",
        "artifact_version": "1.0.0"
      }
    ],
    "governing_contract": {
      "artifact_id": "ticket-lifecycle-contract",
      "artifact_kind": "contract",
      "artifact_version": "0.8.0",
      "locator": "references/lifecycle.md"
    }
  },
  "evidence": [
    {
      "artifact_id": "claim-validation-report",
      "artifact_kind": "validation_report",
      "artifact_version": "1.0.0"
    }
  ],
  "evidence_notes": ["The report verifies the claim transition."],
  "relationships": [
    {
      "target_kind": "knowledge_entry",
      "target_entry_id": "knowledge-ticket-lifecycle-feature",
      "relationship": "part_of",
      "scope_note": "Adjacent feature parent."
    },
    {
      "target_kind": "knowledge_entry",
      "target_entry_id": "knowledge-lifecycle-invariant",
      "relationship": "constrained_by"
    },
    {
      "target_kind": "runtime_artifact",
      "target_artifact": {
        "artifact_id": "claim-validation-report",
        "artifact_kind": "validation_report",
        "artifact_version": "1.0.0"
      },
      "relationship": "verified_by"
    }
  ]
}
```

### 8.2 Constraint

```json
{
  "entry_id": "knowledge-lifecycle-invariant",
  "entry_type": "constraint",
  "level": "feature",
  "visibility": "public",
  "title": "Ticket state is singular",
  "description": "A ticket has exactly one lifecycle status at a time.",
  "version": "1.0.0",
  "valid_from": "0.8.0",
  "immutable": true,
  "constraint_kind": "invariant",
  "scope": "ticket lifecycle state",
  "provenance": {
    "origin_epic": {
      "artifact_id": "workflow-lifecycle-epic",
      "artifact_kind": "epic"
    },
    "source_tickets": [
      {
        "artifact_id": "lifecycle-contract-delivery",
        "artifact_kind": "ticket"
      }
    ],
    "governing_contract": {
      "artifact_id": "ticket-lifecycle-contract",
      "artifact_kind": "contract",
      "locator": "references/lifecycle.md"
    },
    "additional_sources": [
      {
        "artifact_id": "role-boundary-reference",
        "artifact_kind": "reference",
        "locator": "references/roles.md"
      }
    ]
  },
  "evidence": [
    {
      "artifact_id": "lifecycle-contract-delivery",
      "artifact_kind": "ticket"
    }
  ],
  "relationships": [
    {
      "target_kind": "knowledge_entry",
      "target_entry_id": "knowledge-workflow-capability",
      "relationship": "part_of"
    },
    {
      "target_kind": "runtime_artifact",
      "target_artifact": {
        "artifact_id": "ticket-lifecycle-contract",
        "artifact_kind": "reference",
        "locator": "references/lifecycle.md"
      },
      "relationship": "implemented_by"
    }
  ]
}
```

Every canonical Knowledge entry example declares `visibility: "public"`. Every ArtifactRef in these examples contains `artifact_id` and `artifact_kind`; every relationship contains exactly the fields allowed by its target branch.

## 9. Consumer Crosswalks

### 9.1 Runtime Architecture

Runtime Architecture consumers MUST preserve typed fields, not opaque blobs:

| Contract source | Runtime projection | Preservation rule |
|---|---|---|
| `entry_id`, `entry_type`, `level`, `visibility`, `version`, `immutable` | Same typed scalar fields | Preserve without reinterpretation, including the exact visibility value. |
| `provenance.origin_epic` | `origin_epic: ArtifactRef` | Preserve every ArtifactRef field. |
| `provenance.source_tickets` | `source_tickets: array<ArtifactRef>` | Preserve order and every ArtifactRef field. |
| `provenance.governing_contract` | `governing_contract: ArtifactRef` | Preserve every ArtifactRef field. |
| `provenance.additional_sources` | `additional_sources: array<ArtifactRef>` | Preserve order and every ArtifactRef field. |
| `evidence` | `evidence: array<ArtifactRef>` | Preserve as typed references, never strings. |
| `evidence_notes` | `evidence_notes: array<string>` | Keep separate from evidence references. |
| `relationships` | `relationships: array<Relationship>` | Preserve discriminator and exactly one typed target branch per element. |
| lifecycle event | `knowledge_lifecycle_events` | Preserve every Section 5 field, ordering, and causation. |
| replay result | `knowledge_lifecycle_projection` | Derive from ordered events; do not write back to accepted entries. |

Runtime Architecture MUST use `trigger_artifact: ArtifactRef`, `affected_entry_ids`, and `propagation_chain` for invalidation routing. It MUST preserve `prior_state`, `next_state`, `reason`, and `recommended_action` so replay is auditable. It does not materialize or curate Knowledge.

### 9.2 State Model

| Contract source | State Model field | Preservation rule |
|---|---|---|
| canonical Knowledge entry | `knowledge_entry` | Preserve content, visibility, provenance, evidence, and relationships as individually typed fields. |
| `ArtifactRef` | `artifact_ref` | Store `artifact_id`, `artifact_kind`, and optional `artifact_version`, `locator`, `digest` as addressable fields. |
| lifecycle `event_id`, `event_type`, `schema_version` | Same fields | Preserve event identity and schema discriminator. |
| lifecycle `occurred_at`, `event_order` | Same fields | Preserve timestamp and strict monotonic replay order. |
| lifecycle `causation_id` or `causation_chain` | Same union | Preserve the selected causation branch and validate references. |
| lifecycle `actor_role`, `trigger_artifact` | Same typed fields | Preserve ownership and full ArtifactRef. |
| lifecycle `affected_entry_ids`, `propagation_chain` | Same arrays | Preserve order, identity, and direct-versus-propagated distinction. |
| lifecycle `prior_state`, `next_state` | Same typed state objects | Support deterministic replay and transition validation. |
| lifecycle `reason`, `recommended_action` | Same typed fields | Preserve explanation and advisory action. |

The State Model MUST reject opaque serialization that prevents field-level validation of visibility, provenance, evidence, relationships, ArtifactRef, event ordering, causation, or replay state. Accepted entries and events are append-only inputs; current confidence, review-required, supersession, archival, and invalidation are projections rebuilt from events.

### 9.3 Future Store and Retrieval Boundary

Future Knowledge Store and Retrieval consumers MUST preserve and return the exact accepted `visibility` value with each entry and MUST NOT silently omit, broaden, downgrade, or reinterpret it. This is a data-contract obligation only. This ticket and contract do not implement authorization, permissions, RBAC, encryption, storage, indexing, or retrieval behavior.

## 10. Non-Goals

This contract does not implement extraction, classification, persistence, a Knowledge Store, vector search, embeddings, RAG, ranking, agent queries, runtime orchestration, automatic repair, commercial features, or curation tooling. It does not modify ticket, epic, validation, or workflow lifecycle contracts.

## 11. Related References

- `references/roles.md`
- `references/lifecycle.md`
- `references/validation-contract.md`
- `references/validator-protocol.md`
