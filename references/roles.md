# Roles

## Default Roles

### `architect`

Use this role when the work is still being framed, routed, split, reviewed, or accepted.

Typical responsibilities:
- choose the correct lane
- read the required Railyard role, startup, and lifecycle references before review decisions
- inspect open epics
- decide whether new work belongs to an existing epic
- create or revise a ticket
- dispatch or assign Runner work when execution is ready
- review runner output
- provide closure-readiness evidence for epics, but must not close epics by default (this is a Planner responsibility)
- decide next actor and final disposition

Default limits:
- do not silently expand scope
- do not treat a runner result as accepted until review is recorded
- do not stop at `awaiting_review` after dispatching Runner work unless a blocker or explicit opt-in human-gated review mode is recorded
- do not treat a rejected ticket as closed when it should be redispatched to Runner
- do not ask the Human to accept raw Runner output by default; give the Human the Architect-reviewed outcome
- do not authorize a Runner's sandbox, filesystem, network, or destructive-operation escalation unless the Human explicitly approved that action
- do not personally implement rejected Runner fixes unless the Human explicitly changes the current role boundary
- after reject, redispatch Runner automatically when an execution-capable path exists, current authorization allows it, and the same-kind failure limit has not been reached
- stop and report a blocker when redispatch needs Human authorization, no safe Runner path exists, or three same-kind failures have occurred

### `runner`

Use this role when one bounded ticket is already defined.

Typical responsibilities:
- read the required Railyard role and startup references before claiming or editing
- claim one ticket
- execute only that ticket
- write the outbox result
- include lightweight `runner_trace` audit evidence when available as a recommended optional result field: platform name if known, agent profile if known, attempts count, exact commands, blocker category when blocked, and next action when blocked or partial
- mark runner result through the helper

Default limits:
- do not widen scope beyond the ticket
- do not redefine the ticket contract without escalation
- do not bypass helper scripts for workflow writes
- do not record Architect review or final acceptance for your own ticket
- do not close epics or mark lane-level epic completion
- do not report `partial` when work is actually blocked by permission, network, missing secret, missing tool, or unresolved outside action
- do not fake or bypass blocked work with dummy credentials, skipped validation, unrelated tools, raw SQL, or alternate workflow state
- do not turn `runner_trace` into telemetry, token cost tracking, automatic model routing, or failure redispatch discipline
- do not write scratch files, copied databases, or probe state inside `.workflow/`
- do not treat a copied validation database as authoritative workflow state
- do not copy generated ticket, epic, or outbox files into documentation directories unless the ticket explicitly asks for documentation fixtures
- do not work around permission denial; if blocked by a permission boundary, record `runner_result=blocked` using the failure taxonomy in `SKILL.md`

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

## Architect Review Startup Reads

Before starting or recording Architect review, the Architect reads the role and lifecycle protocol:

```text
railyard/SKILL.md
railyard/references/roles.md
railyard/references/startup-sequence.md
railyard/references/lifecycle.md
```

Human prompt text may add stricter project or ticket constraints, but it does not replace these protocol reads. If the files live under a different path, read the equivalent Railyard files and record that path in the review notes or final report.

## Reject And Redispatch

`review_result=reject` routes the ticket back to `ready` for `runner`. Reject is an Architect decision, not the end of the closed loop.

After rejecting a ticket, the Architect should dispatch or spawn a new Runner for the rejected ticket when the platform supports execution-capable subagents and the current session is authorized to spawn them. Dispatching a Runner is Architect work; personally editing the rejected fix is Runner work.

Redispatch is automatic within the Architect closed loop when all of these are true:

- the rejected ticket is `ready` for `runner`
- the platform has a safe execution-capable Runner path
- current authorization allows the dispatch or spawn
- the ticket has not reached three same-kind failures for the intended operation

If the current platform requires explicit Human authorization before spawning subagents and the Human has not granted it, the Architect must report a spawn authorization blocker. The blocker includes:

- the rejected ticket id
- the review reason
- the current ticket state
- the exact spawn-ready Runner prompt or dispatch command needed next

The Architect should not stop silently after reject, and should not claim that Runner redispatch is outside Architect responsibility.

If the third same-kind failure has occurred, the Architect records a blocker instead of redispatching again.

## Platform Agent Type Boundary

Railyard roles are workflow roles. They are not host-platform agent type names.

An Architect may dispatch a Railyard Runner through a documented or discovered platform-native execution agent first. If platform-native selection is missing, ambiguous, or unsafe, the Architect may use a project-defined Railyard fallback profile such as `railyard-runner` when the platform supports custom or prompt-defined agents. A documented implicit default execution path is valid only when the platform marks it execution-capable.

The Architect must not require a platform type literally named `worker`, must not use read-only exploration agents for implementation tickets, and must not use planning-only agents for implementation tickets. If no safe execution-capable dispatch path is known, the Architect records a blocker instead of guessing.

The dispatched prompt must state the workflow role explicitly:

```text
You are acting as the Railyard Runner for this ticket.
```

The dispatched prompt must also list required startup reads before claim or edits. The default required reads are:

```text
railyard/SKILL.md
railyard/references/roles.md
railyard/references/startup-sequence.md
```

Runner result JSON should include `protocol_reads` with the actual role/startup files read. Missing `protocol_reads` is review evidence that the prompt or Runner session may have skipped the role contract, but historical results without it remain valid.

The Runner does not need to know its platform `agent_type`. Platform identity and capability mapping are dispatcher responsibilities.

## Epic Closure Ownership

Epics close at the lane Planner level by default. After all scoped or linked tickets appear complete, the Planner must inspect the finalised ticket results, review outcomes, epic done definition, remaining open tickets, blockers, and dependencies before marking the epic `done`.

Architects provide closure-readiness evidence, but must not close epics merely because all currently scoped tickets appear finalised. Runners may provide closure-readiness evidence as part of a ticket result, but they must not close epics. Planner or Human direction may request closure, but the lane Planner records the lane-level closure through the epic helper after verification.

## Permission Boundaries

Agents can request permission when the operating environment requires it, but only the Human can approve that request. An Architect may not approve an escalation for a spawned Runner as a substitute for Human approval.

When a Runner cannot complete a ticket because a command needs blocked filesystem, network, sandbox, or destructive-operation access, the correct result is `blocked` with the exact command, error, and requested permission. The Runner should not broaden the ticket, write into `.workflow/` scratch space, switch to an unapproved helper path, or mutate raw SQL to get around the denial.

Missing required secrets and missing required tools follow the same rule. The Runner records `blocked` unless the ticket explicitly provides an approved fallback. The Runner must not invent credentials, substitute unrelated tools, skip required checks while claiming success, or use network access that the Human has not approved.

Human-required blockers include category, ticket id, lane, intended operation, commands attempted, exact errors, current ticket state, outbox existence, required Human action, and recommended next action. After reporting that blocker, the agent stops.

## Restricted-Runner Mode

Restricted-runner mode is a platform permission fallback. Use it when the Runner is allowed to edit source files for a ticket but is not allowed to write Control lifecycle state or Control outbox files.

In restricted-runner mode:

- Architect owns Control lifecycle transitions and Control outbox writes.
- Runner reads the required protocol, edits only allowed source files, and runs required validation.
- Runner removes any allowed temporary probe state it created.
- Runner returns exact JSON-compatible result content for Architect to record.
- Runner does not claim, mark result, write Control DB, write Control outbox, record review, close epics, commit, push, or update vendor content.

## Interrupted Runner Recovery

Before drafting or dispatching new Runner work, an Architect should inspect running Runner tickets in the lane. A running ticket with no outbox result JSON may indicate an interrupted Runner session.

The Architect should recover that ticket through:

```powershell
python railyard/scripts/ticket.py --lane system recover-stale --ticket-id SYSTEM-DEMO-001 --actor runner --reason "runner interrupted before outbox"
```

Do not recover when the outbox result JSON exists; use `mark-runner-result` so the ticket moves to Architect review. Do not draft replacement tickets or edit raw SQLite to work around stale running state.

Failed `claim`, `draft`, `next`, or `mark-runner-result` attempts are not alternate recovery paths. If those commands fail during recovery, inspect the ticket with `show`, inspect events with `events`, then use `recover-stale` only when the ticket is running and no outbox result exists.

## Retry Limit

An Architect or Runner must not loop indefinitely on failed helper commands, dispatch attempts, validation commands, or permission-gated operations.

For the same ticket and intended operation, stop after three same-kind failed attempts. Then report a blocker with the attempted commands, exact errors, current ticket state, outbox existence, and recommended next action.

Same-kind failures include repeated helper transition failures, repeated validation failures with the same cause, repeated permission or network denials, repeated missing-secret failures, repeated missing-tool failures, and repeated platform dispatch failures. Do not keep trying new helper commands just because earlier commands failed. If the correct operation is unclear after three attempts, the correct outcome is a blocker, not another mutation attempt.

## MCP-lite Role Boundary

MCP-lite does not create a new authority role. It is an optional tool adapter for the same Human, Planner, Architect, and Runner responsibilities.

Architect-facing MCP tools may inspect workflow state, request the next Runner dispatch payload, and perform review transitions through helper-backed logic. Runner-facing MCP tools may claim a ticket and mark a runner result through helper-backed logic.

No role should treat MCP-lite as permission to bypass the lifecycle contract. The MCP surface must not expose raw SQL, force reset, admin mutation, arbitrary source editing, direct ticket Markdown rewrite, broad `sync-docs` or `sync-mailbox` replacement, or replacement of the helper lifecycle contract.

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
