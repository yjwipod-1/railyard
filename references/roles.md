# Roles

## Default Roles

### `architect`

Use this role when the work is still being framed, routed, split, reviewed, or accepted.

Typical responsibilities:
- choose the correct lane
- inspect open epics
- decide whether new work belongs to an existing epic
- create or revise a ticket
- review runner output
- decide next actor and final disposition

Default limits:
- do not silently expand scope
- do not treat a runner result as accepted until review is recorded

### `runner`

Use this role when one bounded ticket is already defined.

Typical responsibilities:
- claim one ticket
- execute only that ticket
- write the outbox result
- mark runner result through the helper

Default limits:
- do not widen scope beyond the ticket
- do not redefine the ticket contract without escalation
- do not bypass helper scripts for workflow writes

## Project-Specific Role Extensions

Projects may add extra roles such as:
- `reviewer`
- `operator`
- `release-manager`
- `qa`

When extra roles exist:
- define them in a project-local extension file
- map each role to allowed actions
- keep the default `architect` and `runner` contract unless the project explicitly overrides it
