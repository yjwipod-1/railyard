# Routing

## Startup Questions

Before acting, resolve these in order:

1. Which lane is this work in: `Domain` or `System`?
2. Which role is active: `architect` or `runner`?
3. Is this epic-oriented or ticket-oriented work?
4. Which helper script owns the next read or write?
5. If dispatching a subagent, which platform execution surface is safe for the Railyard workflow role?

## Recommended Resolution Order

```powershell
python railyard/scripts/resolve_control_surface.py --lane domain --role architect --epic-id DOMAIN-E001
```

Then use the returned helper and command suggestion.

## Default Command Map

- epic reads and writes: `scripts/epic.py`
- ticket reads and writes: `scripts/ticket.py`
- schema setup: `scripts/workflow_schema.py`
- project initialization: `scripts/init_workflow.py`
- bootstrap epic import: `scripts/bootstrap_epics.py`

## Routing Guardrails

- Do not infer open work from summary docs when control tables exist.
- Do not inspect inbox files first when the ticket lifecycle state is not yet known.
- Do not bypass lane resolution.
- Do not treat platform agent type names as Railyard workflow roles.
- Do not dispatch implementation work to read-only or planning agents.
- Use `references/platform-dispatch.md` when mapping Railyard roles to platform agent profiles, modes, or subagents.
