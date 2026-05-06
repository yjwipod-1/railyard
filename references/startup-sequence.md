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
.github/agents/
.workflow/workflow.db
docs/domain/epics/
docs/domain/inbox/
docs/domain/outbox/
docs/system/epics/
docs/system/inbox/
docs/system/outbox/
docs/templates/
```

The `.github/agents/` directory contains default Railyard agent profiles for platforms that support VS Code / GitHub Copilot-style custom agents. Platforms that do not read this directory can still use the same profile text as prompt material.

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
spawn.platform_agent_type
spawn.fallback_profile
spawn.profile_priority
spawn.fallback_agent_types
spawn.role
spawn.runner_name
spawn.adapter
spawn.contract
spawn.prompt_format
spawn.prompt
```

When the operating environment supports subagents, map this payload to that environment's spawn mechanism and pass `spawn.prompt` as the runner instruction.

Before spawning, apply `references/platform-dispatch.md`. Railyard Runner is a workflow role, not a required platform `agent_type`. `spawn.agent_type` and `spawn.platform_agent_type` may be `null` until the host adapter selects a documented execution-capable platform surface. Use capability matching, not name matching: Runner dispatch requires read, write, execute, scoped file edit, and result JSON capabilities. Use a documented or discovered platform-native execution agent first. If platform-native selection is missing, ambiguous, or unsafe, use the Railyard fallback profile when the platform supports custom or prompt-defined agents. Do not use read-only or planning agents for Runner implementation, and fail fast if no safe execution-capable dispatch path is known.

Architect dispatch is a closed-loop responsibility by default. The Architect that dispatches Runner work must resume after the Runner result, inspect the outbox result and validation evidence, then complete Section 8 review. Dispatch is not complete while the ticket remains in `awaiting_review`.

An Architect may leave a ticket in `awaiting_review` only when a blocker is recorded or when the ticket, handoff, or project protocol explicitly declares opt-in human-gated review.

The Architect may not approve a spawned Runner's sandbox, filesystem, network, or destructive-operation escalation unless the Human has explicitly approved that exact action. Permission denial is a blocker, not an invitation to bypass the workflow helper or write into another control surface.

Before drafting or dispatching new Runner work, the Architect should inspect running tickets:

```powershell
python railyard/scripts/ticket.py --lane domain list --status running --next-actor runner
python railyard/scripts/ticket.py --lane system list --status running --next-actor runner
```

If a ticket is still `running` but the Runner session was interrupted before writing its outbox result JSON, recover it before dispatching later tickets in the same lane:

```powershell
python railyard/scripts/ticket.py --lane system recover-stale --ticket-id SYSTEM-001 --actor runner --reason "runner interrupted before outbox"
```

Use `--dry-run` first when inspecting an uncertain case. If the outbox result JSON exists, do not recover the ticket; run `mark-runner-result` instead. `sync-mailbox --reset-lifecycle --ticket-id <ID>` remains a lower-level fallback that resets lifecycle fields from inbox and outbox files, but `recover-stale` is the intended recovery path for interrupted running Runner tickets.

Do not use `claim`, `draft`, `next --ticket-id`, or raw SQLite updates to recover stale running tickets. Those commands either require a different lifecycle state or create a different object.

If recovery, dispatch, claim, result marking, review, validation, or permission-gated work fails three times for the same ticket and intended operation, stop and record a blocker. The blocker should include the commands attempted, exact errors, current ticket state, outbox existence, and recommended next action.

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

Architect review is mandatory in the default protocol. A Runner result of `done` means the Runner claims completion; it does not mean the ticket is accepted. Acceptance exists only after `mark-review-result` records `accept` or `accept_with_changes`.

If the Architect dispatched the Runner, the Architect must not report the overall task as complete until one review result is recorded or a specific blocker is reported. Human-gated review is opt-in and must be explicit before the Architect can leave raw Runner output for Human acceptance.

Accepted tickets move to:

```text
status=finalised
next_actor=none
```

Rejected tickets move back to `ready` for `runner`.

Redesign tickets move back to `drafted` for `architect`.

## 9. Planner And Human Summary

Before summarizing completed lane work, the lane Architect should close any epic whose scoped or linked tickets satisfy the epic done definition.

Epic closure requires checking:

- finalised ticket statuses
- accepted review outcomes
- Runner result evidence
- the epic done definition
- remaining open tickets in the epic scope
- blockers and dependencies

Runners do not close epics. Planner or Human direction may request closure, but the lane Architect records it through the epic helper.

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

Smoke checks for dispatch must verify the closed loop, not only Runner handoff. A smoke that stops at `awaiting_review` proves Runner completion but does not prove Architect completion.

Validation scratch state must stay outside `.workflow/`. Probes that need writable workflow data should copy the database to a separate temporary directory, run against that copy, and verify the live database did not change unless the test is intentionally exercising a lifecycle transition through the helper.

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
python railyard/scripts/ticket.py --lane domain --project-root ../project --db ../project/.workflow/workflow.db next --actor runner
```

## 12. Optional MCP-lite Surface

The v0.3 MCP-lite server is optional. It runs over stdio and wraps existing helper-backed operations:

```powershell
python railyard/scripts/railyard_mcp_server.py --db .workflow/workflow.db --project-root .
```

Install the optional dependency from:

```powershell
python -m pip install -r railyard/requirements-mcp.txt
```

Use the probe before relying on the MCP surface:

```powershell
python railyard/scripts/probe_railyard_mcp_server.py --db .workflow/workflow.db --project-root .
```

The probe copies the database to a temporary directory, exercises read tools, dispatch, validation tools, and narrow lifecycle write tools, then verifies the live database did not change.

MCP-lite is not a replacement for the helper scripts or for a project's pinned stable Railyard runtime. It must not expose raw SQL, force reset, admin mutation, arbitrary source editing, direct ticket Markdown rewrite, or broad `sync-docs` and `sync-mailbox` replacement. Use helpers directly when a task requires workflow administration outside the MCP-lite boundary.
