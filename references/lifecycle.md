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

## MCP-lite Lifecycle Boundary

The optional MCP-lite server is a stdio adapter over helper-backed lifecycle behavior. It does not change the workflow state model.

Canonical state remains in SQLite. Lifecycle transitions remain owned by the helper functions. MCP write tools are limited to the same bounded transitions that a role may perform through helpers:

- `claim_ticket`
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
