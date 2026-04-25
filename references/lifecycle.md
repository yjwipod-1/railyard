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
7. `architect` reviews and records review result
8. ticket becomes `finalised` or `superseded`

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
