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
python railyard/scripts/ticket.py --lane domain sync-mailbox --reset-lifecycle
python railyard/scripts/ticket.py --lane domain draft --epic-id DOMAIN-E001 --title "Define MVP spec" --task "Create docs/mvp_spec.md from the project brief." --scope "Create docs/mvp_spec.md" --acceptance-check "MVP scope and exclusions are explicit" --validator-not-required --validator-gate-reason "Documentation-only ticket with no independent semantic gate."
python railyard/scripts/ticket.py --lane domain next --actor runner
python railyard/scripts/ticket.py --lane domain list --status running --next-actor runner
python railyard/scripts/ticket.py --lane domain show --ticket-id DOMAIN-001
python railyard/scripts/ticket.py --lane system claim --ticket-id SYSTEM-DEMO-001 --actor runner --claimed-by system-runner-1
python railyard/scripts/ticket.py --lane system recover-stale --ticket-id SYSTEM-DEMO-001 --actor runner --reason "runner interrupted before outbox"
python railyard/scripts/ticket.py --lane domain mark-runner-result --ticket-id DOMAIN-001 --runner-result done --outbox-path docs/domain/outbox/DOMAIN-001.result.json
python railyard/scripts/ticket.py --lane domain start-review --ticket-id DOMAIN-001 --claimed-by domain-architect
python railyard/scripts/ticket.py --lane domain mark-review-result --ticket-id DOMAIN-001 --review-result accept
python railyard/scripts/ticket.py --lane system mark-review-result --ticket-id SYSTEM-001 --review-result accept --validator-report-record evidence/SYSTEM-001.validator-record.json
python railyard/scripts/ticket.py --lane domain events --ticket-id DOMAIN-001
```

## Architect Helpers

```powershell
python railyard/scripts/architect.py --lane domain --runner-name domain-runner-1 dispatch-next-runner
python railyard/scripts/architect.py --lane system --runner-name system-runner-1 dispatch-next-runner
```

## Routing Helper

```powershell
python railyard/scripts/route_workflow_target.py --lane domain --role architect --epic-id DOMAIN-E001
python railyard/scripts/route_workflow_target.py --lane system --role runner --ticket-id SYSTEM-DEMO-001
```

## Bootstrap Import

```powershell
python railyard/scripts/bootstrap_epics.py --lane domain --input queue.json
```
