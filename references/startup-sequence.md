# Startup Sequence

This is the recommended operating sequence for adopting Railyard in a new project.

Keep this file as the step-by-step reference. Keep `README.md` as the GitHub project overview.

## 1. Install Into A Project

Copy or clone this repository into the target project as `railyard/`.

From the target project root:

```powershell
python railyard/scripts/init_workflow.py --project-root .
```

This creates the project-local workflow surface:

```text
.workflow/workflow.db
docs/domain/epics/
docs/domain/inbox/
docs/domain/outbox/
docs/system/epics/
docs/system/inbox/
docs/system/outbox/
docs/templates/
```

## 2. Confirm The Schema

```powershell
python railyard/scripts/workflow_schema.py ensure --db .workflow/workflow.db
python railyard/scripts/workflow_schema.py tables --db .workflow/workflow.db
```

Expected tables:

```text
domain_epic
domain_ticket
schema_version
system_epic
system_ticket
workflow_event
```

## 3. Establish Planner Context

The Human and Planner should define:

- project goal
- current constraints
- Domain lane scope
- System lane scope
- first epics
- any cross-lane dependencies

Do not start Runner work before the relevant lane Architect has created or approved ready tickets.

## 4. Create Or Sync Epics

Use one of these paths.

Direct helper upsert:

```powershell
python railyard/scripts/epic.py --lane domain upsert --epic-id DOMAIN-E001 --title "First domain epic" --status queued --priority high
python railyard/scripts/epic.py --lane system upsert --epic-id SYSTEM-E001 --title "First system epic" --status queued --priority high
```

Markdown sync:

```powershell
python railyard/scripts/epic.py --lane domain --project-root . sync-docs
python railyard/scripts/epic.py --lane system --project-root . sync-docs
```

Check unresolved work:

```powershell
python railyard/scripts/epic.py --lane domain next-open
python railyard/scripts/epic.py --lane system next-open
```

## 5. Create Tickets

Architects create ticket Markdown files in:

```text
docs/domain/inbox/
docs/system/inbox/
```

Use:

```text
docs/templates/TICKET.md
```

Then sync:

```powershell
python railyard/scripts/ticket.py --lane domain --project-root . sync-mailbox
python railyard/scripts/ticket.py --lane system --project-root . sync-mailbox
```

Architects may also draft tickets directly through the helper:

```powershell
python railyard/scripts/ticket.py --lane domain draft --epic-id DOMAIN-E001 --title "Define scope" --task "Write docs/scope.md."
```

## 6. Architect Dispatch

Architect can request the next ready Runner ticket and a spawn-ready prompt:

```powershell
python railyard/scripts/architect.py --lane domain --runner-name domain-runner-1 dispatch-next-runner
python railyard/scripts/architect.py --lane system --runner-name system-runner-1 dispatch-next-runner
```

The helper returns:

```text
status
lane
synced
ticket
spawn.agent_type
spawn.role
spawn.runner_name
spawn.adapter
spawn.contract
spawn.prompt_format
spawn.prompt
```

When the operating environment supports subagents, map this payload to that environment's spawn mechanism and pass `spawn.prompt` as the runner instruction.

## 7. Runner Execution

Runner finds the next ready ticket:

```powershell
python railyard/scripts/ticket.py --lane domain next --actor runner
python railyard/scripts/ticket.py --lane system next --actor runner
```

Runner claims one ticket:

```powershell
python railyard/scripts/ticket.py --lane domain claim --ticket-id DOMAIN-001 --actor runner --claimed-by runner-1
```

Runner writes a result file in the declared outbox path, normally:

```text
docs/domain/outbox/DOMAIN-001.result.json
docs/system/outbox/SYSTEM-001.result.json
```

Use:

```text
docs/templates/RESULT.json
```

Then record the result:

```powershell
python railyard/scripts/ticket.py --lane domain mark-runner-result --ticket-id DOMAIN-001 --runner-result done --outbox-path docs/domain/outbox/DOMAIN-001.result.json
```

`mark-runner-result` validates the result JSON before handing the ticket to Architect review.

## 8. Architect Review

Architect finds the next ticket waiting for review:

```powershell
python railyard/scripts/ticket.py --lane domain next --actor architect
```

Architect claims it:

```powershell
python railyard/scripts/ticket.py --lane domain start-review --ticket-id DOMAIN-001 --claimed-by architect-1
```

Architect records review:

```powershell
python railyard/scripts/ticket.py --lane domain mark-review-result --ticket-id DOMAIN-001 --review-result accept
```

Accepted tickets move to:

```text
status=finalised
next_actor=none
```

Rejected tickets move back to `ready` for `runner`.

Redesign tickets move back to `drafted` for `architect`.

## 9. Planner And Human Summary

After Architect review, the Planner summarizes:

- completed tickets
- accepted results
- rejected or blocked work
- cross-lane dependency changes
- recommended next epics or tickets

The Human makes final project-level decisions from this summary.

## 10. Minimal E2E Smoke Check

A clean smoke check should prove both lanes can complete the same lifecycle:

```text
init
epic create or sync
ticket create or sync
architect dispatch
runner next
runner claim
runner result
architect next
architect claim
architect review
ticket finalised
epic done
queues empty
```

Expected final ticket state:

```text
status=finalised
next_actor=none
runner_result=done
review_result=accept
```

Expected final queue checks:

```powershell
python railyard/scripts/ticket.py --lane domain next --actor runner
python railyard/scripts/ticket.py --lane domain next --actor architect
python railyard/scripts/ticket.py --lane system next --actor runner
python railyard/scripts/ticket.py --lane system next --actor architect
```

Each should return:

```text
null
```

## 11. Command Rule

When running helper scripts from outside the target project root, pass both `--project-root` and `--db` before the subcommand:

```powershell
python railyard/scripts/ticket.py --lane domain --project-root C:\path\to\project --db C:\path\to\project\.workflow\workflow.db next --actor runner
```
