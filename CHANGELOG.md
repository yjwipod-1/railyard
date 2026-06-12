# Changelog

All notable public-facing changes to Railyard are summarized here.

## Unreleased / v0.7.3

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
- Documented official platform dispatch notes for Codex, Claude Code, Gemini CLI, GitHub Copilot, VS Code, Windsurf, Cursor, and JetBrains agent surfaces.
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
