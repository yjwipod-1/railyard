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

## Working Sequence

1. Resolve lane: `Domain` or `System`.
2. Resolve role: `architect` or `runner`.
3. Resolve object type: `Epic` or `Ticket`.
4. Use the official helper script for that lane and object.
5. Read the specific inbox or outbox file only after control-plane state is known.
6. Record runner and review outcomes through helper commands.

## Recommended Entry Commands

```powershell
python railyard/scripts/resolve_control_surface.py --lane domain --role architect --epic-id DOMAIN-E001
python railyard/scripts/epic.py --lane domain list-open
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
