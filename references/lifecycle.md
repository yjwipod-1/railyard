# Lifecycle

## Epic Status

Valid epic statuses:
- `queued`
- `in_progress`
- `partial`
- `blocked`
- `done`
- `superseded`

Terminal epic statuses:
- `done`
- `superseded`

## Epic Closure Contract

Epic closure is a lane-level Architect responsibility. A Runner may complete tickets that satisfy an epic, but a Runner must not close the epic or mark its lane-level status.

Before recording an epic as `done`, the lane Architect must inspect:

- every scoped or linked ticket for the epic
- each ticket's final status, runner result, and review result
- the epic done definition
- remaining open tickets in the same epic scope
- unresolved blockers
- declared epic dependencies and external dependencies

An epic is ready for closure only when the relevant tickets are `finalised`, accepted by Architect review, and no blockers or open scoped work remain. If the epic has a single `linked_ticket_id`, that ticket must be finalised and accepted. If the epic spans multiple tickets, every ticket in the scope must be finalised and accepted or explicitly superseded by an accepted replacement.

Planner or Human direction may request or authorize closure, but the lane Architect records the closure through the epic helper after verifying the evidence. Closing an epic is separate from accepting any individual ticket.

## Ticket Status

Valid ticket statuses:
- `drafted`
- `ready`
- `running`
- `awaiting_review`
- `in_review`
- `finalised`
- `superseded`

## Ticket Role Flow

Typical flow:

1. `architect` drafts or syncs the ticket.
2. ticket becomes `ready` for `runner`
3. `runner` claims the ticket
4. ticket becomes `running`
5. runner writes outbox result and marks runner result
6. ticket becomes `awaiting_review` for `architect`
7. `architect` starts review and ticket becomes `in_review`
8. `architect` reviews and records review result
9. ticket becomes `finalised`, `ready`, or `drafted` depending on review outcome

## Closed-Loop Architect Contract

The default Railyard protocol is closed-loop at the Architect level.

When an `architect` dispatches or otherwise assigns a ticket to a `runner`, the same Architect-level workflow remains responsible for the ticket until one of these outcomes is recorded:

- `review_result=accept`
- `review_result=accept_with_changes`
- `review_result=reject`
- `review_result=redesign`

`awaiting_review` is a handoff state, not a completion state. An Architect run that created or dispatched Runner work is incomplete if it stops at `awaiting_review` without an explicit exception.

Valid exceptions:
- the Runner result is `blocked` or `invalid`
- required review evidence is missing or unreadable
- the ticket or project explicitly declares a human-gated review mode
- the Architect records a blocker and leaves a clear next action

Human-gated review is opt-in. It must be stated in the ticket, handoff, or project protocol. Otherwise the Architect must start review and record a review result through the helper.

Architect review is not a rubber stamp. The Architect must inspect the Runner result, changed files or produced artifacts, and validation evidence before recording the review result.

## Permission And Scratch-State Boundary

Permission escalation is a Human boundary, not an Architect-to-Runner delegation. An Architect may dispatch a Runner, but it must not approve a child agent's sandbox, filesystem, network, or destructive-operation escalation unless the Human has explicitly granted that approval for the requested action.

If a Runner hits a permission boundary that is outside the ticket contract, the Runner must record `runner_result=blocked` with the exact command, error, and needed approval. It must not bypass the boundary by writing to a different control surface or by mutating state outside the helper contract.

Validation and probe work must not place scratch state in `.workflow/`. Smoke tests that need writable copies of workflow data must copy the database to a temporary directory outside any `.workflow` directory, run against that copy, and verify the live database is unchanged unless the ticket explicitly calls for a lifecycle transition through the helper.

## Stale Running Recovery

A Runner ticket can remain `status=running` and `next_actor=runner` if the Runner session is interrupted after claim but before writing the outbox result JSON. This is not an Architect review state and should not be bypassed by drafting a replacement ticket or editing SQLite directly.

Recovery rule:

- If the outbox result JSON exists, run `mark-runner-result`.
- If the outbox result JSON does not exist and the Runner session is gone, run `recover-stale`.
- Use `--dry-run` before recovery when the session state is uncertain.
- Do not dispatch later tickets in the same lane while a stale running ticket blocks the lane.
- Do not use `claim` to recover a running ticket; claim only applies to ready Runner work.
- Do not use `draft` to update or reset an existing ticket.
- Do not use `next --ticket-id`; `next` selects the next ready ticket for an actor and does not accept a specific ticket id.

Command:

```powershell
python railyard/scripts/ticket.py --lane system recover-stale --ticket-id SYSTEM-001 --actor runner --reason "runner interrupted before outbox"
```

`recover-stale` only supports interrupted Runner tickets in `running` state. It resets the ticket to `status=ready`, `next_actor=runner`, clears claim and result fields, and records a `recover-stale-running` workflow event with the recovery reason and previous claim metadata.

`sync-mailbox --reset-lifecycle --ticket-id <ID>` can still reset lifecycle fields from inbox and outbox files, but it is a broad mailbox sync operation. Prefer `recover-stale` for interrupted running Runner tickets because it validates the current state and refuses recovery when an outbox result already exists.

## Retry Stop Rule

Agents must stop retry loops before they become unattended drift.

For the same ticket and the same intended lifecycle operation, an Architect or Runner may make at most three failed attempts. After the third failure, the agent must stop trying alternate commands and record or report a blocker.

The blocker must include:

- ticket id
- lane
- intended operation
- commands attempted
- exact errors
- current ticket state
- whether the outbox result exists
- recommended next action

The retry limit applies to recovery, claim, dispatch, result marking, review transitions, validation, and permission-gated commands. Repeating the same command with cosmetic changes counts as another attempt. Trying a different helper command for the same intended state change also counts as another attempt.

The retry limit does not authorize bypassing helper scripts, direct SQLite edits, broad reset commands, or cross-lane mutation.

## MCP-lite Lifecycle Boundary

The optional MCP-lite server is a stdio adapter over helper-backed lifecycle behavior. It does not change the workflow state model.

Canonical state remains in SQLite. Lifecycle transitions remain owned by the helper functions. MCP write tools are limited to the same bounded transitions that a role may perform through helpers:

- `claim_ticket`
- `recover_stale_ticket`
- `start_review`
- `mark_runner_result`
- `mark_review_result`

MCP read and inspection tools may return tickets, epics, ticket events, schema version, validation results, and Architect dispatch payloads. Dispatch returns a spawn-ready Runner payload; it does not execute the Runner and does not mark acceptance.

The MCP-lite lifecycle surface must not expose raw SQL, force reset, admin mutation, arbitrary source editing, direct ticket Markdown rewrite, broad `sync-docs` or `sync-mailbox` replacement, or replacement of a project's pinned stable Railyard runtime.

All lane-specific MCP tools require an explicit lane. Probes should run against a temporary database copy and verify the live database is unchanged unless the ticket explicitly authorizes a live helper-backed transition.

## Runner Results

Valid runner results:
- `done`
- `partial`
- `blocked`
- `invalid`

## Review Results

Valid review results:
- `accept`
- `accept_with_changes`
- `reject`
- `redesign`

Review result behavior:
- `accept` -> `finalised`, `next_actor=none`
- `accept_with_changes` -> `finalised`, `next_actor=none`
- `reject` -> `ready`, `next_actor=runner`
- `redesign` -> `drafted`, `next_actor=architect`

Runner completion is not acceptance. Acceptance exists only after review is recorded.
