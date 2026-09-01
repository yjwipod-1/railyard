# Governance Document Taxonomy

This document defines the canonical governance meta-model for all normative and non-normative project documents. It establishes six mutually exclusive document kinds, their authority behavior, cross-kind composition rules, and a deterministic precedence matrix for resolving conflicts.

Every document that carries governance weight must declare its kind through the governance document metadata convention defined in the companion JSON Schema at `assets/schemas/governance-document-metadata-v1.schema.json`.

## 1. Document Kinds

### 1.1 Protocol

**Purpose**: Interaction and state-transition rules defining how roles and components communicate and coordinate. A Protocol states what must happen in what order, who owns which transitions, and at what state boundaries handoff occurs.

**Allowed content**:
- Explicit sequence of steps or phases that must be followed
- Role ownership of specific transitions and handoff points
- State-machine definitions with valid states and permitted transitions
- Required startup or initialization reads
- Handoff contracts between roles, including required inputs and expected outputs
- Lifecycle phases and their entry/exit conditions

**Forbidden content**:
- Conditional decision rules that answer "should I do X in situation Y" -- that is Policy
- Structural validation shapes or type definitions -- that is Schema
- Producer-consumer guarantees about artifact shape or semantics -- that is Contract
- Lists of canonical identifiers or mappings -- that is Registry
- Explanatory prose without normative sequencing -- that is Guide

**Authority behavior**: Protocol is authoritative for interaction sequencing and boundary ownership. No Policy, Guide, or ad hoc prompt may reorder a Protocol sequence, reassign transition ownership, or insert unauthorized handoff steps. Protocol boundaries may only be tightened by authorized stricter-scope constraints; they may never be weakened.

**Concrete Railyard examples**:
- `references/startup-sequence.md` -- defines the step-by-step operating sequence for adopting the workflow
- `references/lifecycle.md` -- defines ticket and epic lifecycle states and valid transitions
- `references/validator-protocol.md` -- defines the Validator role input/output contract and verdict semantics

### 1.2 Policy

**Purpose**: Conditional decision rules for what is required, allowed, preferred, or prohibited. A Policy states "when condition X is true, you must/should/must not do Y."

**Allowed content**:
- Conditional rules with explicit triggers ("when", "if", "unless")
- Normative keywords: MUST, MUST NOT, SHOULD, SHOULD NOT, MAY
- Role responsibilities, limits, and boundaries expressed as conditional obligations
- Capability selection and routing rules based on conditions
- Overrideability declarations that define how other documents may constrain behavior

**Forbidden content**:
- Interaction sequences or state-transition rules -- that is Protocol
- Structural validity shapes or type definitions -- that is Schema
- Canonical identifier mappings -- that is Registry
- Explanatory prose without conditional rules -- that is Guide

**Authority behavior**: Policy is authoritative for conditional decisions within its declared scope. A Policy may be overridden by stricter Policy when overrideability permits. Policy may not override Protocol sequencing, Contract obligations, or safety constraints. When two Policies at the same authority level conflict within the same scope, the result is blocked/inconclusive.

**Concrete Railyard examples**:
- `references/roles.md` -- defines role responsibilities, limits, and boundaries as conditional obligations
- `references/platform-dispatch.md` -- defines capability selection and routing rules based on platform conditions
- `references/routing.md` -- defines lane and role routing rules

### 1.3 Contract

**Purpose**: Producer-consumer obligations and semantic invariants. A Contract defines what a producer guarantees and what a consumer may rely upon.

**Allowed content**:
- Producer guarantees: structural shape, field presence, value ranges, format compliance
- Consumer expectations: what may be relied upon, what must be tolerated
- Semantic invariants: relationships that must hold across artifacts
- Version compatibility rules
- Required validation gates and their failure behavior
- Evidence requirements for acceptance

**Forbidden content**:
- Interaction sequences -- that is Protocol
- Conditional decision rules -- that is Policy
- Canonical identifier lists -- that is Registry
- Explanatory prose without normative obligations -- that is Guide

**Authority behavior**: Contract is authoritative for producer-consumer obligations within its declared scope. Contract boundaries may not be weakened by Policy, Guide, or ad hoc prompt. When a Contract declares a required field, evidence requirement, or semantic invariant, no other document kind may remove or relax it. Contract obligations may only be tightened by authorized stricter scope.

**Concrete Railyard examples**:
- `references/result-format.md` -- defines the required shape of Runner result JSON
- `references/ticket-format.md` -- defines the required metadata for Ticket documents
- `references/epic-format.md` -- defines the required metadata for Epic documents
- `references/sql-contract.md` -- defines SQLite schema and query guarantees
- `references/validation-contract.md` -- defines validation contract ownership and handoff obligations
- `references/knowledge-contract.md` -- defines Knowledge entry eligibility and provenance rules

### 1.4 Schema

**Purpose**: Structural validity definitions -- shapes, types, constraints, and schema-level invariants that artifacts must satisfy.

**Allowed content**:
- JSON Schema definitions
- Database table definitions with column types and constraints
- Type definitions and structural constraints
- Enumerated value sets that constrain valid inputs
- Schema-level invariants (e.g., "this field must be unique across all documents")

**Forbidden content**:
- Behavioral rules or decision logic -- that is Policy
- Interaction sequences -- that is Protocol
- Producer-consumer guarantees -- that is Contract (though a Schema may be referenced by a Contract)
- Explanatory prose -- that is Guide

**Authority behavior**: Schema is authoritative for structural validity. An artifact that does not conform to an applicable Schema is structurally invalid. Schema may be referenced by Contract to define structural obligations. Schema may be tightened (additional constraints) by stricter scope but never loosened. When Schema and Contract disagree on structure, the tighter constraint wins; when both are at the same level, the conflict is blocked/inconclusive.

**Concrete Railyard examples**:
- The tickets table definition in the workflow database schema
- `assets/schemas/governance-document-metadata-v1.schema.json` -- the JSON Schema for governance document metadata
- `assets/schemas/runtime-artifact-visibility-v1.schema.json` -- visibility constraints
- `assets/schemas/runtime-evidence-export-v1.schema.json` -- evidence export shape

### 1.5 Registry

**Purpose**: Canonical identifier mappings -- maps names to authoritative identifiers, versions, and owner records.

**Allowed content**:
- Name-to-identifier mappings
- Version records for canonical artifacts
- Owner/maintainer records
- Index entries with stable references to normative documents
- Command reference tables with canonical invocation forms

**Forbidden content**:
- Behavioral rules -- that is Policy
- Interaction sequences -- that is Protocol
- Structural constraints -- that is Schema
- Explanatory prose beyond what identifies the mapped entity -- that is Guide

**Authority behavior**: Registry is authoritative for identifier resolution. When a Registry maps a name to an identifier, no other document kind may silently remap it. Registry entries may be superseded through explicit versioned supersession links. An unresolvable or broken Registry entry is invalid. Multiple active entries for the same canonical topic are invalid and produce blocked/inconclusive.

**Concrete Railyard examples**:
- `references/validation-primitive-registry.md` -- maps validation primitive names to their definitions
- `references/helper-commands.md` -- canonical command reference with invocation forms

### 1.6 Guide

**Purpose**: Non-normative explanatory content. A Guide helps users understand concepts, provides context, offers usage examples, and summarizes other documents.

**Allowed content**:
- Explanatory prose and conceptual overviews
- Usage examples that illustrate but do not define rules
- Summaries of Protocol, Policy, Contract, Schema, or Registry content
- Mental models and abstract descriptions
- Tutorials and walkthroughs

**Forbidden content**:
- Normative rules using MUST, MUST NOT, SHOULD, SHOULD NOT -- those belong in Policy
- State-transition definitions -- that is Protocol
- Structural constraints -- that is Schema
- Producer-consumer guarantees -- that is Contract
- Canonical identifier mappings -- that is Registry

**Authority behavior**: Guide is non-normative. When Guide and any normative document (Protocol, Policy, Contract, Schema, Registry) conflict, Guide always loses. Guide may summarize normative content but never defines, extends, or overrides it. Guide must declare `authority_level: informational` and `overrideability: informational`.

**Concrete Railyard examples**:
- `README.md` -- project overview and getting-started guide
- `references/helper-commands.md` usage examples section -- illustrative command usage
- `references/model.md` -- abstract mental model of the workflow system

## 2. Cross-Kind Composition Rules

### 2.1 Ownership by Kind

| Kind | Owns |
|---|---|
| Protocol | Interaction sequence, state transitions, authority boundaries between roles/components |
| Policy | Conditional decisions: "when X, must/should/must not do Y" |
| Contract | Producer-consumer obligations, semantic invariants, evidence requirements |
| Schema | Structural validity: shapes, types, constraints, enumerated values |
| Registry | Canonical identifier mappings and version records |
| Guide | Non-normative explanation; never owns authority |

### 2.2 Mixed-Document Rules

A single document may contain multiple labeled sections belonging to different kinds. For example, a document may have a Protocol section defining a sequence, a Policy section defining conditional rules, and a Schema section defining structural constraints.

When sections are mixed:
- Each section must be labeled with its governance kind
- Each section inherits the authority behavior of its declared kind
- The document-level `governance_kind` metadata field declares the primary kind
- Sections of a different kind than the document's primary kind are still authoritative for their domain

### 2.3 Canonical Authority Rule

Every `canonical_for` topic must have exactly one active authoritative document. Two documents claiming `canonical_for` on the same topic is a conflict (see precedence matrix rule 5). Documents that are superseded (via `supersedes` links) are no longer active.

## 3. Precedence Matrix

When two governance documents make conflicting claims about the same topic, apply the following rules in order:

### Rule 1: Safety and Platform Constraints

Safety constraints and platform-level invariants are the highest authority. Nothing overrides them -- not Protocol, not Policy, not Contract, not any scoped constraint. A safety boundary is absolute.

**Action**: The safety constraint wins. Any document that contradicts a safety constraint is invalid in that scope.

### Rule 2: Explicit Canonical Authority

Active explicit `canonical_for` authority wins over summaries, references, or general statements. A document that declares itself canonical for topic T is authoritative for T over any document that merely mentions T.

**Action**: The canonical document wins. If neither document is canonical for the topic, proceed to Rule 3.

### Rule 3: Stricter Scoped Constraints

Scoped constraints (prompt-level, ticket-level, project-policy level) may narrow behavior ONLY when:
- The narrowing is monotonic (stricter, not weakening)
- The narrowing is authorized by the scoping mechanism's overrideability declaration

Scoped constraints cannot weaken:
- Non-overridable Protocol or Contract boundaries
- Safety constraints (Rule 1 always applies)
- Higher-authority Policy declarations

**Action**: Apply the stricter constraint when authorized. Reject attempted weakening.

### Rule 4: Non-Overridable Boundaries

Protocol, Contract, and safety boundaries marked `overrideability: non_overridable` cannot be weakened by any other document kind. A Policy, Guide, or scoped constraint that attempts to relax a non-overridable boundary is invalid in that scope.

**Action**: The non-overridable boundary wins. The conflicting document is invalid in that scope.

### Rule 5: Same-Level or Cross-Kind Contradictions

When two documents at the same `authority_level` make contradictory claims, or when documents of different kinds claim authority over the same topic without a clear precedence path (Rules 1-4), the result is blocked/inconclusive.

Both authorities must be identified. Resolution requires Human judgment or explicit supersession.

**Action**: Stop. Identify both authorities. Record as blocked/inconclusive.

### Rule 6: Explicit Supersession

Supersession is defined through versioned directed acyclic links in the `supersedes` metadata field. A document that declares `supersedes: ["v1-doc-id"]` replaces the superseded document's authority for overlapping scope.

Critical constraints on supersession:
- Newer timestamps or filenames never imply authority. Implicit recency is not supersession.
- Broken links (supersedes references a non-existent document_id) are invalid.
- Cycles in the supersession graph are invalid.
- Multiple active canonical owners for the same topic are invalid.
- Silent semantic replacement (changing meaning without explicit supersession) is invalid.

**Action**: The superseding document wins for overlapping scope. The superseded document's remaining scope (if any) remains authoritative.

### Rule 7: Guide Non-Authority

Guide and README content is non-normative. When any Guide content conflicts with any normative document (Protocol, Policy, Contract, Schema, Registry), the normative document always wins regardless of authority level, timestamp, or scope.

**Action**: The normative document wins. The Guide content is non-authoritative in that scope.

## 4. Metadata Convention Summary

Every governance document that carries authority must declare metadata conforming to `assets/schemas/governance-document-metadata-v1.schema.json`. The metadata model is flat (no nested objects beyond arrays) to remain compatible with simple frontmatter parsers.

Required fields: `document_id`, `governance_kind`, `version`, `authority_level`, `owner`, `scope`, `applies_to`, `overrideability`, `status`.

Conditionally required: `canonical_for` for all non-informational documents (Protocol, Policy, Contract, Schema, Registry). Informational documents (Guide) must not declare `canonical_for`.

Optional fields: `canonical_for`, `supersedes`, `description`.

Key semantic constraints:
- `governance_kind: guide` requires `authority_level: informational` and `overrideability: informational`
- `overrideability: informational` requires `authority_level: informational`
- Non-informational documents must declare non-empty `canonical_for` for canonical ownership
- Informational/Guide documents must not declare `canonical_for`
- `supersedes` must not contain the document's own `document_id`
- `canonical_for` topics must be unique within the array
- `status: superseded` documents are no longer active authorities

**Inventory Authority**: `references/governance-document-inventory.json` is the sole machine-readable governance metadata authority. Embedded frontmatter in individual documents may mirror metadata for human convenience but is not a second authority. All consumers of governance metadata must resolve canonical metadata from the inventory.

The inventory is self-inclusive: it contains entries for its own schema (`assets/schemas/governance-document-inventory-v1.schema.json`), JSON registry (`references/governance-document-inventory.json`), and human-readable Markdown companion (`references/governance-document-inventory.md`). Every governance artifact that carries authority appears in the inventory, including the inventory artifacts themselves.

**Version Provenance**: When a document has an explicit version declaration in its source content or schema contract, the inventory version and `document_id` suffix must match that declaration exactly. For artifacts with no explicit document version, the inventory assigns `0.8.0` as the first-governed-snapshot. This records governance adoption at v0.8.0 and must not claim that unchanged or current content originated in an earlier release. Explicit source declarations always take precedence over the snapshot rule.

## 5. Change Management

Human revision of canonical artifacts occurs through explicit versioning and redesign processes. An ad hoc prompt is not an override mechanism. A prompt may add scoped constraints (Rule 3) but may not redefine Protocol sequences, Policy rules, Contract obligations, Schema constraints, or Registry mappings.

When a canonical document is revised:
1. The new version receives a distinct `document_id`
2. If it replaces an earlier version, the earlier version's `document_id` appears in the new document's `supersedes` array
3. The earlier version's `status` is updated to `superseded`
4. The new version declares `canonical_for` for the same topics it inherits

## 6. Non-Goals

This taxonomy:
- Does NOT implement a policy engine, document loader, or automatic conflict resolver
- Does NOT define runtime enforcement, model routing, or automatic dispatch rules
- Does NOT classify, annotate, or rewrite existing project documents
- Does NOT implement commercial governance features
- Does NOT replace Human judgment for same-level or cross-kind contradictions
