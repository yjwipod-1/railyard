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
