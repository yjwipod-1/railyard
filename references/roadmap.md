# Roadmap

Railyard is currently a portable workflow scaffold: files, SQLite tables, templates, and helper commands.

The next competitive step is to turn the scaffold into a stronger integration surface without losing the core design principle: agentic work should run inside deterministic control rails.

## Version Direction

### v0.2 - Runner Dispatch And Lifecycle Hardening

Goal:
- make Architect-to-Runner dispatch explicit and machine-readable

Target features:
- Architect helper that returns a spawn-ready Runner prompt
- stricter ticket lifecycle transitions
- result JSON validation before Architect review
- ticket lifecycle event log
- adapter-neutral dispatch payload

Non-goal:
- directly calling any specific agent runtime
- making Railyard Codex-only

### v0.3 - MCP-lite Control Surface

Goal:
- expose Railyard as a safe query, validation, and dispatch surface for MCP clients

Boundary:
- MCP is a query and dispatch surface, not the primary write authority.
- Read-only workflow state should be MCP-accessible.
- Validation should be MCP-accessible.
- Dispatch prompt generation should be MCP-accessible.
- Mutating MCP tools should be narrow, state-machine guarded, and auditable.
- Mailbox registration should remain script-first until artifact schemas and review gates are stricter.
- Admin mutations should not be exposed by default.

Recommended tools:
- `get_ticket`
- `list_tickets`
- `next_ticket`
- `get_epic`
- `list_open_epics`
- `next_open_epic`
- `ticket_events`
- `validate_ticket_artifact`
- `validate_result_artifact`
- `validate_schema`
- `dispatch_next_runner`

Optional narrow writes:
- `claim_ticket`
- `start_review`
- `mark_runner_result`
- `mark_review_result`

Explicit non-goals:
- full mailbox registration through MCP by default
- lifecycle reset through MCP by default
- force update or admin mutation through MCP by default
- replacing the SQLite control plane with an MCP server

Reason:
- a broad mutating MCP surface makes accidental claims, finalisation, reset, or registration too easy for agents
- MCP should reduce lookup and dispatch friction without weakening deterministic guardrails
- scripts remain the safer explicit write surface for registration and administrative operations

## 1. Interoperability With Agent And Project Surfaces

Goal:
- make Railyard easy to adopt inside existing agent repositories

Target integrations:
- `AGENTS.md` project memory and startup instructions
- MCP-lite tools for read-only state lookup, validation, event inspection, and dispatch
- GitHub Issues as an optional external ticket mirror

Expected shape:
- documented mapping between Railyard roles and `AGENTS.md`
- MCP read helpers for epics, tickets, queues, event logs, and result files
- MCP validation helpers for tickets, results, and schema
- MCP dispatch helper for Runner prompt generation
- optional sync between GitHub Issues and Railyard tickets

Non-goal:
- replacing the SQLite control plane with GitHub Issues
- full mutating MCP as the default control surface

## 2. GitHub Action And PR Review Integration

Goal:
- let Railyard participate in normal repository review workflows

Target features:
- GitHub Action to validate ticket and result artifacts
- GitHub Action to check SQLite schema compatibility
- PR comments summarizing changed epics, tickets, and result files
- optional PR gate requiring accepted Architect review for workflow changes

Expected shape:
- `.github/workflows/railyard-validate.yml`
- deterministic validators that can run without an LLM
- clear pass/fail output for maintainers

## 3. Minimal TUI Or Web Dashboard

Goal:
- make workflow state visible without requiring users to write SQL or remember helper commands

Target views:
- open epics by lane
- ready runner tickets
- tickets awaiting Architect review
- blocked tickets and dependencies
- recently finalised tickets

Expected shape:
- read-only first
- local-only by default
- small enough to run from the project checkout

Non-goal:
- hosted SaaS dashboard

## 4. Multi-Runner Concurrency Locks And Conflict Detection

Goal:
- support multiple runners without accidental duplicate claims or conflicting edits

Target features:
- atomic ticket claim semantics
- claim expiry or heartbeat
- explicit conflict signal when a ticket is already claimed
- optional file ownership hints per ticket
- detection of multiple runners writing the same result artifact

Expected shape:
- SQLite-backed locks
- deterministic helper behavior
- no reliance on conversation memory for coordination

## 5. Stricter Ticket And Result Artifact Schemas

Goal:
- make inbox and outbox files machine-checkable

Target features:
- formal JSON Schema for result files
- stricter Markdown frontmatter schema for tickets and epics
- validation commands for all workflow artifacts
- clear distinction between required, optional, and project-specific fields

Expected shape:
- `schemas/` directory
- `scripts/validate_artifacts.py`
- GitHub Action using the same validator

## 6. Adapter Examples For Agent Frameworks

Goal:
- show how Railyard can be used from common agent execution environments

Target examples:
- Codex
- Claude Code
- LangGraph
- generic MCP client

Expected shape:
- small examples, not full framework rewrites
- each example demonstrates lane resolution, ticket claim, result write, and review recording
- examples remain optional and do not become required runtime dependencies

## Suggested Build Order

Recommended sequence:

1. Define stricter ticket and result schemas.
2. Add validation commands.
3. Add MCP-lite read, validation, event, and dispatch tools.
4. Add multi-runner locking semantics.
5. Add GitHub Action validation.
6. Add GitHub Issues interoperability.
7. Add minimal dashboard.
8. Add agent framework adapters.

Reason:
- schema and validation create the contract
- locking protects concurrent execution
- CI makes the contract enforceable
- integrations and dashboards become safer once the contract is stable
