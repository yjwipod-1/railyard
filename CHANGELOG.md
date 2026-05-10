# Changelog

All notable public-facing changes to Railyard are summarized here.

## Unreleased / v0.5.1

- Hardened Runner dispatch so spawn-ready prompts require Railyard role/startup protocol reads before claim or edits.
- Updated Runner dispatch payloads to v3 and added `required_startup_reads`.
- Added required `protocol_reads` evidence to Runner result JSON validation.
- Updated MCP probe, artifact validation, result templates, and examples to make missing role protocol reads visible before Architect review.
- Hardened Architect review guidance so prompt text does not replace required protocol reads, rejected tickets continue through Runner redispatch when authorized, and blocked platform spawn authorization is reported explicitly.

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
- Ignored `.claude/` local agent configuration so machine-local tool settings stay out of public releases.
- Added this changelog as part of release discipline and adoption hardening.

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
