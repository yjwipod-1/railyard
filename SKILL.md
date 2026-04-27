---
name: railyard
description: Use when a repository organizes work into Domain and System lanes with epic and ticket control tables, role-based routing, helper-backed SQLite access, inbox and outbox files, and explicit review gates.
---

# Railyard Workflow

Use this skill when a project follows a structured operating model with:
- two work lanes: `Domain` and `System`
- unresolved work tracked as `Epic`
- bounded execution tracked as `Ticket`
- role-based behavior such as `architect` and `runner`
- helper-backed SQLite control tables
- inbox and outbox files as task and result bodies

## Core Rules

- Treat `Epic` as unresolved planning and dependency truth.
- Treat `Ticket` as bounded execution and review truth.
- Resolve the correct lane before acting.
- Resolve the current role before acting.
- Prefer the official helper scripts over direct SQL.
- Treat mailbox files as body surfaces, not as control truth, unless the project explicitly says otherwise.
- Keep review explicit. Runner completion does not equal final acceptance.
- When dispatching execution, prefer `scripts/architect.py dispatch-next-runner` so the Architect receives a spawn-ready Runner prompt.
- Default Architect dispatch is closed-loop: the Architect that dispatches Runner work must review the Runner result and record a review result unless an explicit opt-in human-gated exception is declared.
- Treat `awaiting_review` as an intermediate handoff state, not an Architect completion state.
- Treat human-gated review as opt-in. It must be declared in the ticket, handoff, or project protocol.
- Do not place validation scratch state, copied workflow databases, or probe temp files inside `.workflow/`.
- Do not authorize a child agent's permission escalation on behalf of the Human. If a Runner hits a sandbox or permission boundary outside its ticket contract, record a blocker with the exact command and error.
- Treat MCP-lite as an optional control-plane adapter over helper-backed operations. SQLite remains canonical workflow state, and helper functions remain lifecycle authority.
- Do not expose or rely on MCP tools for raw SQL, force reset, admin mutation, arbitrary source editing, direct ticket Markdown rewrite, broad `sync-docs` or `sync-mailbox` replacement, or replacement of a project's pinned stable runtime.

## Working Sequence

1. Resolve lane: `Domain` or `System`.
2. Resolve role: `architect` or `runner`.
3. Resolve object type: `Epic` or `Ticket`.
4. Use the official helper script for that lane and object.
5. Read the specific inbox or outbox file only after control-plane state is known.
6. For Runner execution, let the Architect dispatch the next ticket and spawn a worker subagent with the returned prompt when the environment supports subagents.
7. Runner records the runner outcome through the helper.
8. Architect inspects the Runner result and validation, then records the review outcome through the helper.
9. Report Architect completion only after the ticket is finalised, routed back to Runner, routed back to Architect, or blocked with a clear next action.

## MCP-lite Boundary

The optional v0.3 MCP-lite server exists for control-plane integration through stdio. Use it only for narrow workflow operations that mirror existing helper behavior:

- inspect tickets, epics, ticket events, and schema version
- dispatch the next Runner ticket and receive a spawn-ready Runner prompt
- claim tickets, start review, mark runner result, and mark review result
- validate result payloads and ticket state

Lane-specific tools must receive an explicit `lane` value. Probes and smoke checks must operate on a copied database and keep temporary files outside `.workflow/` unless a ticket explicitly authorizes a live workflow transition.

## Recommended Entry Commands

```powershell
python railyard/scripts/resolve_control_surface.py --lane domain --role architect --epic-id DOMAIN-E001
python railyard/scripts/epic.py --lane domain list-open
python railyard/scripts/architect.py --lane domain --runner-name domain-runner-1 dispatch-next-runner
python railyard/scripts/ticket.py --lane domain next --actor runner
python railyard/scripts/ticket.py --lane system show --ticket-id SYSTEM-001
```

## Read Next

- Abstract workflow model: `references/model.md`
- Startup sequence and E2E smoke checks: `references/startup-sequence.md`
- Role behavior: `references/roles.md`
- Routing rules: `references/routing.md`
- Lifecycle contract: `references/lifecycle.md`
- SQL contract: `references/sql-contract.md`
- Helper command examples: `references/helper-commands.md`
- Epic document format: `references/epic-format.md`
- Ticket document format: `references/ticket-format.md`
- Result file format: `references/result-format.md`
