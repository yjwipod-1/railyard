# Helper Commands

These commands are the default working surface.

## Initialize

```powershell
python railyard/scripts/init_workflow.py --project-root .
python railyard/scripts/workflow_schema.py ensure --db .workflow/workflow.db
```

## Epic Helpers

```powershell
python railyard/scripts/epic.py --lane domain sync-docs
python railyard/scripts/epic.py --lane domain list-open
python railyard/scripts/epic.py --lane domain next-open
python railyard/scripts/epic.py --lane domain show --epic-id DOMAIN-E001
python railyard/scripts/epic.py --lane system upsert --epic-id SYSTEM-E001 --title "Platform hardening" --status queued --priority high
```

## Ticket Helpers

```powershell
python railyard/scripts/ticket.py --lane domain sync-mailbox
python railyard/scripts/ticket.py --lane domain next --actor runner
python railyard/scripts/ticket.py --lane domain show --ticket-id DOMAIN-001
python railyard/scripts/ticket.py --lane system claim --ticket-id SYSTEM-001 --actor runner --claimed-by codex
python railyard/scripts/ticket.py --lane domain mark-runner-result --ticket-id DOMAIN-001 --runner-result done
python railyard/scripts/ticket.py --lane domain mark-review-result --ticket-id DOMAIN-001 --review-result accept
```

## Routing Helper

```powershell
python railyard/scripts/resolve_control_surface.py --lane domain --role architect --epic-id DOMAIN-E001
python railyard/scripts/resolve_control_surface.py --lane system --role runner --ticket-id SYSTEM-001
```

## Bootstrap Import

```powershell
python railyard/scripts/bootstrap_epics.py --lane domain --input queue.json
```
