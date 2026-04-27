# Roles

## Default Roles

### `architect`

Use this role when the work is still being framed, routed, split, reviewed, or accepted.

Typical responsibilities:
- choose the correct lane
- inspect open epics
- decide whether new work belongs to an existing epic
- create or revise a ticket
- dispatch or assign Runner work when execution is ready
- review runner output
- decide next actor and final disposition

Default limits:
- do not silently expand scope
- do not treat a runner result as accepted until review is recorded
- do not stop at `awaiting_review` after dispatching Runner work unless a blocker or explicit opt-in human-gated review mode is recorded
- do not ask the Human to accept raw Runner output by default; give the Human the Architect-reviewed outcome
- do not authorize a Runner's sandbox, filesystem, network, or destructive-operation escalation unless the Human explicitly approved that action

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
- do not record Architect review or final acceptance for your own ticket
- do not write scratch files, copied databases, or probe temp state inside `.workflow/`
- do not work around permission denial by mutating another control surface

## Default Closed-Loop Ownership

The Architect owns a ticket from scoping through review disposition when the Architect dispatches Runner execution.

Default sequence:

1. Architect creates or approves a ready ticket.
2. Architect dispatches or spawns a Runner.
3. Runner claims, executes, writes result JSON, and marks runner result.
4. Architect reads the result and validation evidence.
5. Architect starts review and records one review result.

Stopping after step 3 leaves the ticket in `awaiting_review` and is not a completed Architect workflow unless an opt-in human-gated review exception was explicitly declared.

Human-gated review is not the default. It must be declared in the ticket, handoff, or project protocol before raw Runner output can wait on Human acceptance instead of Architect review.

## Permission Boundaries

Agents can request permission when the operating environment requires it, but only the Human can approve that request. An Architect may not approve an escalation for a spawned Runner as a substitute for Human approval.

When a Runner cannot complete a ticket because a command needs blocked filesystem, network, sandbox, or destructive-operation access, the correct result is `blocked` with the exact command, error, and requested permission. The Runner should not broaden the ticket, write into `.workflow/` scratch space, switch to an unapproved helper path, or mutate raw SQL to get around the denial.

## MCP-lite Role Boundary

MCP-lite does not create a new authority role. It is an optional control-plane adapter for the same Human, Planner, Architect, and Runner responsibilities.

Architect-facing MCP tools may inspect workflow state, request the next Runner dispatch payload, and perform review transitions through helper-backed logic. Runner-facing MCP tools may claim a ticket and mark a runner result through helper-backed logic.

No role should treat MCP-lite as permission to bypass the lifecycle contract. The MCP surface must not expose raw SQL, force reset, admin mutation, arbitrary source editing, direct ticket Markdown rewrite, broad `sync-docs` or `sync-mailbox` replacement, or replacement of a project's pinned stable Railyard runtime.

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
