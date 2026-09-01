# Changelog

All notable public-facing changes to Railyard are summarized here.

## v0.8.0 - 2026-08-29

### Runtime State Foundation

- Added `references/runtime-architecture.md` defining the runtime component model: event sourcing with deterministic reducer, explicit-path SQLite journal, read-only projection, evidence export, and sidecar facade.
- Added `references/runtime-state-contract.md` defining the event schema, reducer contract, journal format, projection API, and sidecar interface.
- Added `scripts/runtime_state_core.py` providing a deterministic event reducer that applies typed events to immutable state snapshots.
- Added `scripts/runtime_state_journal.py` providing an explicit-path SQLite journal storing events as append-only typed rows with globally unique event_id and run-local event_order that is positive, strictly increasing and gap-free.
- Added `scripts/runtime_state_projection.py` providing a read-only projection layer over the event journal.
- Added `scripts/runtime_evidence_export.py` providing structured evidence export from runtime state.
- Added `scripts/runtime_state_sidecar.py` providing a sidecar facade for runtime components.
- Added `references/runtime-artifact-visibility-contract.md` and `references/runtime-evidence-export-contract.md` as interface contracts.
- Added corresponding test suites for all runtime state components.

### Runtime Validation And Public Smoke

- Added the local runtime adapter, Gate Decision, Action Policy, Validator Mesh, Validator dispatch, and publish bridge, with deterministic state, staging-manifest, and smoke authorities.
- Added `requirements-test.txt` with the two direct test-only dependencies required by the public validation route. The runtime remains stdlib-only; GitHub Actions and every public quickstart install this manifest before the ordered compile, artifact validation, CI harness, 23-file regression, and 20-scenario smoke commands.
- Added the canonical local validation route: `python -m pip install -r requirements-test.txt`, then `python -m compileall -q scripts`, `python scripts/validate_artifacts.py --project-root .`, `python scripts/test_runtime_v080_ci.py`, `python scripts/runtime_v080_regression.py`, and `python scripts/runtime_v080_smoke.py --tmp-dir <caller-supplied OS temporary directory> --all run`.
- Added the 20-scenario public smoke contract. Scenarios 003-011 cover nine expected typed non-pass Mesh outcomes; a correct all-scenario conformance run remains `total=20`, `passed=20`, `failed=0` with exit 0.
- Configured Windows and Linux GitHub Actions for the local route. The configuration is locally validated; no hosted run is claimed without Human-authorized staging and push.

### Governance Taxonomy and Read Routing

- Added `references/governance-document-taxonomy.md` defining the canonical governance meta-model: Protocol, Policy, Contract, Schema, Registry, and Guide document kinds with authority levels, overrideability, and precedence rules.
- Added `references/governance-document-inventory.json` as the sole machine-readable inventory of all governance documents with their classifications and canonical relationships.
- Added `references/governance-document-inventory.md` as a human-readable companion to the JSON inventory.
- Added `references/governance-read-routing.json` as the declarative read-routing registry defining deterministic per-role startup read lists with conditional includes and fail-closed behavior.
- Added `scripts/governance_read_router.py` implementing the resolver that produces deterministic ordered read lists per role.
- Updated `scripts/architect.py` to use the governance resolver for Runner dispatch startup reads.
- Updated `SKILL.md` and `references/startup-sequence.md` to adopt resolver-backed startup reads as the canonical bootstrap path.

### Knowledge Contract and Functionality Ontology

- Added `references/knowledge-contract.md` defining the public Knowledge Contract
  with domain/capability/feature/behavior hierarchy levels, `technical_fact` and
  `constraint` entry types, seven cross-cutting relationship types (`part_of`,
  `depends_on`, `constrained_by`, `implemented_by`, `verified_by`,
  `introduced_by`, `superseded_by`), Knowledge eligibility and default exclusion
  rules, multi-ticket aggregation rules, runtime artifact identity and provenance
  requirements, supersession and invalidation hooks, and role-based Contract
  ownership.
- Added Knowledge Curator role to `references/roles.md` with defined
  responsibilities, default limits, and Validation Contract ownership.
- Added Knowledge Contract cross-links in `SKILL.md`, `README.md`,
  `README.zh-CN.md`, and `references/startup-sequence.md`.
- Added generic, public-safe calibration fixtures under
  `examples/knowledge_contract_fixtures/` covering valid entries, ineligible
  entries, missing provenance, supersession chains, broken supersession, multi-
  ticket aggregation, and ticket-to-multi-functionality contribution.

### Scope

This release delivers the local runtime state foundation, runtime adapter, Gate Decision, Action Policy, Validator Mesh with dispatch and publish bridge, deterministic smoke, staging-manifest authority, governance taxonomy and read-routing, and the Knowledge Contract. The following remain explicitly not implemented: Knowledge extraction or store, vector database, RAG, Context Ranking, hosted runtime or service, scheduler, proprietary provider or model integration, Knowledge Curator tooling, automatic release, tag, commit, or push, or full runtime framework completion.

## Unreleased / v0.7.4

### Adoption quickstart

- Added a README-first onboarding path for new users, including installation,
  Planner session setup, Architect dispatch, manual Runner fallback, and
  Validator usage when independent evidence is required.
- Added a compact role/lifecycle explanation showing disposable chat sessions
  backed by durable SQLite workflow state.
- Clarified the current bootstrap boundary: the default workflow database lives
  at `railyard/.workflow/workflow.db`, with local authority recorded in
  `railyard/.railyard-workflow.json`.
- Added `README.zh-CN.md` as a Chinese quick-start document covering what
  Railyard is, where to place it, initialization, role usage, minimal copyable
  prompts, state location, common boundaries, and further reading references.
  A Chinese entry link is placed near the top of the main README.
- Added public text hygiene validation with an explicit localized-document
  exception for `README.zh-CN.md`: localized content is not ASCII-only, but it
  must remain valid UTF-8 without BOM, mojibake, local paths, or private
  project references.

### Semantic validation contracts and calibration fixtures

- Added `references/semantic-validation-contract.md` defining fixed semantic
  claim types, evidence states, verdict branches, deterministic precedence, and
  non-goals for v0.7.4 semantic validation contracts.
- Updated `references/validation-primitive-registry.md` Section 11 with bounded
  semantic primitive contract logic for coherence, contradiction,
  completeness, and plausibility. These are contract-level definitions and do
  not add executable semantic Validator behavior.
- Added generic semantic calibration fixtures covering enough evidence, missing
  evidence, conflicting evidence, and unsupported semantic claims for each
  semantic primitive.
- Extended `scripts/validate_artifacts.py` to validate semantic calibration
  fixture shape as release artifacts.

## v0.7.3

### README onboarding

- Added a short "Start Using Railyard" section near the top of the README
  showing the minimal Planner, Architect, Runner, and Validator session
  prompts for a project adopting Railyard.
- Clarified that an existing requirements or planning conversation is the
  natural Planner session for introducing Railyard.
- Clarified that a session does not need to be permanently bound to a ticket;
  passing the ticket id as context is enough unless the platform provides a
  dedicated binding feature.
- Added session-scope guidance: Planner keeps business context, Architect can
  scope to an epic or ticket, and manual Runner fallback can use a fresh session
  per ticket to avoid context pollution.
- Documented the preferred Architect entry path: ask the Planner for the
  smallest Architect startup prompt for the current epic or ticket, then run
  that prompt in a fresh session.

### Validator verdict handoff and bounded remediation

- Defined the complete Validator verdict handoff tree covering who acts next,
  acceptance/closure permissions, remediation, evidence, redesign, blocked
  handling, and Human escalation for every verdict.
- Documented the no-remediation boundary: Validator produces evidence only;
  all remediation, fixes, and lifecycle decisions are owned by consumer roles.

### Semantic validation boundary

- Defined the semantic validation boundary distinguishing deterministic
  checks (structural, field-mapping, value-preservation, formula-recompute,
  record-key reconciliation) from semantic inference (logical consistency,
  cross-artifact coherence, domain-level correctness).
- Established deterministic precedence: a deterministic finding always takes
  precedence over a conflicting semantic inference.
- Documented the boundary in references/validation-contract.md with scope,
  precedence hierarchy, and v0.7.3 non-goals.

### Confidence and Human escalation matrix

- Defined a confidence and Human escalation matrix in
  references/validator-protocol.md mapping all 15 combinations of confidence
  level (high/medium/low) and overall verdict to mandatory, recommended, or
  optional escalation tiers.
- Documented Architect and Planner obligations per escalation tier.

### Reserved semantic primitive namespace

- Reserved the semantic inference primitive namespace in
  references/validation-primitive-registry.md for bounded v0.7.4
  implementation.
- Reserved entries include semantic_coherence, semantic_contradiction,
  semantic_completeness, and semantic_plausibility with placeholder check
  logic and stable rule_ids.
- No executable semantic Validator behavior, semantic calibration fixtures, or
  runtime orchestration was added in v0.7.3.

## v0.7.2

### Validator gate and deterministic primitive coverage

- Required every drafted or published ticket to record an explicit Validator gate decision and rationale.
- Added conditional Validator gate metadata for risk level, contract or acceptance criteria source, expected artifacts, evidence pack, and failure behavior.
- Hardened ticket drafting, mailbox sync, ticket templates, review enforcement, and examples around the Validator gate metadata contract.
- Clarified that `scripts/validate_artifacts.py`, Runner verification, and Architect self-review cannot satisfy a required independent Validator evidence gate.
- Clarified Validation Contract ownership and handoff across Human, Planner, Architect, Runner, and Validator roles.
- Added a deterministic validation primitive registry and aligned executable `scripts/validator.py` finding rule IDs where coverage exists.
- Added generic primitive fixtures plus primitive fixture artifact-shape validation.
- Added executable primitive coverage-status documentation for currently covered and non-covered deterministic checks.

## v0.7.1

### Validator usability and executable reference

- Added a public Validator role entry path in the main README, including who dispatches the Validator, what payload it consumes, and where it fits in Architect review and Planner closure.
- Added public-safe Validator examples for Architect review, source-to-derived mapping review, Planner closure review, and Planner release-readiness review.
- Added `scripts/validator.py` as a minimal executable development-time Validator reference for source-to-derived field-mapping checks.
- Clarified the boundary between artifact-shape validation and executable Validator evidence: `scripts/validate_artifacts.py` validates shape only, while `scripts/validator.py` emits Validation Reports.
- Documented the current bounded implementation limit: Planner closure and release-readiness inputs are accepted as shape examples, while the minimal executable Validator reports them as `human_review_required` until a dedicated readiness implementation exists.

## Unreleased / v0.5.1

- Hardened Runner dispatch so spawn-ready prompts require Railyard role/startup protocol reads before claim or edits.
- Updated Runner dispatch payloads to v3 and added `required_startup_reads`.
- Added required `protocol_reads` evidence to Runner result JSON validation.
- Updated MCP probe, artifact validation, result templates, and examples to make missing role protocol reads visible before Architect review.
- Hardened Architect review guidance so prompt text does not replace required protocol reads, rejected tickets continue through Runner redispatch when authorized, and blocked platform spawn authorization is reported explicitly.

## v0.7

### validation contract foundation

- Required every drafted or published ticket to record an explicit Validator gate decision and rationale.
- Added conditional Validator gate metadata for risk level, contract or acceptance criteria source, expected artifacts, evidence pack, and failure behavior.
- Hardened ticket drafting, mailbox sync, templates, artifact-shape validation, and examples around the Validator gate metadata contract.
- Clarified that `scripts/validate_artifacts.py`, Runner verification, and Architect self-review cannot satisfy a required independent Validator evidence gate.
- Added v0.7 validation contract foundation to README.md, SKILL.md, CHANGELOG.md, references/validation-contract.md, references/result-format.md.
- Validation contract defines generic, development-time-first contract/report model without business rules or runtime orchestration.
- Added `validate_contract()` function to `scripts/validate_artifacts.py` for contract.json shape validation.
- Added internal consistency checks to `validate_report()`: overall_verdict cross-validation against findings (pass/fail/blocked/inconclusive/human_review_required semantics).
- Updated overall_verdict values to pass/fail/blocked/inconclusive/human_review_required; removed warn from overall verdict.
- Updated finding status to pass/fail/not_applicable/blocked/inconclusive; finding severity to error/warn/info.
- The reference implementation provides deterministic shape validation for Railyard artifacts including tickets, epics, result files, queue examples, validation contracts, and validation reports.
- The Validator is read-only by default: it inspects artifacts and produces reports without modifying them, creating tickets, or executing lifecycle transitions.
- Added `scripts/validator.py` as a minimal executable source-to-derived Validator reference implementation with CLI input/output, field-mapping validation, supported generic transforms, missing mapping policy, and warning escalation semantics.
- `scripts/validate_artifacts.py` remains schema and shape validation only; `scripts/validator.py` provides the bounded source-to-derived rule execution described above. Neither script implements external runtime orchestration, automatic repair, model routing, lifecycle writes, or business-specific rules.
- Future queued validation work may extend the same generic contract model.

## Unreleased / v0.5

- Added a platform dispatch contract that separates Railyard workflow roles from host-platform agent type names.
- Documented platform dispatch notes for supported execution surfaces.
- Added default initialization agent profiles for Railyard Architect, Runner, Explorer, and Reviewer under `.github/agents/`.
- Updated Runner dispatch payloads to v2 so platform-native agent selection is explicit adapter work and `railyard-runner` is a fallback profile instead of a hardcoded `worker` value.
- Added the role capability contract and conservative fuzzy matching policy for platform dispatch adapters.
- Added explicit stale running ticket recovery through `ticket.py recover-stale` and MCP `recover_stale_ticket`.
- Improved ticket helper errors so failed claim, draft, and missing-result paths point to the intended stale recovery flow.
- Added a three-failed-attempt retry stop rule so unattended Architect or Runner sessions report blockers instead of looping across helper commands.
- Documented safe fallback behavior for unknown platforms, including fail-fast behavior when no execution-capable dispatch path is known.

## Unreleased / v0.4

- Documented the Epic Closure Contract so lane Architects explicitly own epic closure after verifying scoped ticket outcomes, done definitions, blockers, and dependencies.
- Added a public MCP-lite smoke example for disposable workflow validation.
- Added `scripts/validate_artifacts.py` for deterministic workflow artifact and example queue validation.
- Added GitHub Actions validation for compile checks, artifact validation, and MCP-lite smoke checks.
- Added this changelog as part of release discipline and adoption hardening.

## v0.6

Railyard v0.6 introduces enhanced execution observability through execution profiles and a standardized failure taxonomy.

### execution profile, confidence, evidence, and failure taxonomy

- Added execution profile hints (`fast`, `strong`, `local`) as advisory routing hints for dispatch adapters. These are not automatic model routing.
- Added structured `confidence` field (`high`, `medium`, `low`) to Runner result JSON.
- Added `evidence` array to Runner results for documenting file paths, command outputs, or logs that justify confidence levels.
- Added failure taxonomy for blocked result reporting: `permission_denied`, `command_failed`, `sandbox_boundary`, `authorization_required`, `environment_issue`, `unresolved_dependency`.
- Updated SKILL.md and README.md with execution profile, confidence, evidence, and failure taxonomy documentation.

### result payload validation

- Added validation for `confidence` and `evidence` fields in runner result JSON contract.
- `mark-runner-result` now validates these fields before handing the ticket to Architect review.

### runner dispatch payload v4

- Updated `architect.py dispatch-next-runner` to include `profile_hints` in spawn-ready Runner dispatch payloads.
- Profile hints flow through the dispatch contract to platform dispatch adapters.

### MCP validation and probe coverage

- Extended MCP-lite probe to validate v0.6 result fields: `confidence`, `evidence`, and `protocol_reads`.
- Probe runs against a copied workflow database to preserve the live database during validation.

### blocked result example

- Added `examples/blocked-result-example/` demonstrating the failure taxonomy in practice.

Note: v0.6 does not implement automatic model routing. Profile hints are advisory only.

## v0.3

- Added the optional MCP-lite stdio tool surface as a thin adapter over the existing helper-backed workflow contract.
- Added read and inspection tools for tickets, epics, ticket events, and workflow schema version.
- Added narrow lifecycle write tools for ticket claim, review start, runner result, and review result transitions.
- Added dispatch and validation tools that preserve the existing closed-loop Architect and Runner workflow.
- Added an MCP-lite probe that validates the tool surface against a copied workflow database.
- Documented the MCP-lite boundary and non-goals, including no raw SQL, broad admin mutation, direct source editing, or replacement of helper authority.

## v0.2

- Added helper-backed Runner dispatch support with spawn-ready handoff payloads.
- Hardened ticket lifecycle state transitions and review routing.
- Expanded workflow schema and helper behavior for durable runner results, review outcomes, and event tracking.
- Improved lifecycle, SQL, result format, and startup documentation.

## v0.1

- Introduced the core Railyard workflow scaffold.
- Added System and Domain lanes, epics, tickets, inbox and outbox files, and SQLite-backed workflow state.
- Added helper scripts for initializing workflow state, syncing epics and tickets, inspecting queues, and managing lifecycle transitions.
- Added the initial README, skill instructions, workflow references, templates, and project skeleton.
