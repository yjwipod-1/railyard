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

Epic closure is a lane-level Planner responsibility. Architects provide closure-readiness evidence, but must not close epics merely because all currently scoped tickets appear finalised. A Runner may complete tickets that satisfy an epic, but a Runner must not close the epic or mark its lane-level status.

Before recording an epic as `done`, the lane Planner must inspect:

- every scoped or linked ticket for the epic
- each ticket's final status, runner result, and review result
- the epic done definition
- remaining open tickets in the same epic scope
- unresolved blockers
- declared epic dependencies and external dependencies

An epic is ready for closure only when the relevant tickets are `finalised`, accepted by Architect review, and no blockers or open scoped work remain. If the epic has a single `linked_ticket_id`, that ticket must be finalised and accepted. If the epic spans multiple tickets, every ticket in the scope must be finalised and accepted or explicitly superseded by an accepted replacement.

Planner or Human direction may request or authorize closure, but the lane Planner records the closure through the epic helper after verifying the evidence. Closing an epic is separate from accepting any individual ticket.

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
5. runner writes outbox result, including `protocol_reads` evidence when available, and marks runner result
6. ticket becomes `awaiting_review` for `architect`
7. `architect` starts review and ticket becomes `in_review`
8. `architect` reviews and records review result
9. ticket becomes `finalised`, `ready`, or `drafted` depending on review outcome

## Runner Result Boundaries

`done` means the Runner completed the ticket scope and provided validation evidence.

`partial` means the Runner completed a useful, reviewable subset of the ticket inside the allowed scope, but the full ticket is not complete. A partial result must state what is done, what remains, validation status, and a concrete `next_action`. Partial is not a way to hide missing validation or outside dependencies.

`blocked` means the Runner cannot continue without outside action from a Human, Planner, Architect, platform, dependency owner, environment, secret provider, permission boundary, network boundary, or tool installation. Blocked work stops. The Runner must not fake success, broaden scope, use dummy secrets, skip required network checks while reporting success, substitute unrelated tools, write alternate workflow state, or mutate SQLite directly.

`invalid` means the result cannot be trusted or does not satisfy the result contract.

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

Prompt text can add constraints, but it does not replace Railyard protocol reads. Before Architect review decisions, the Architect reads the role, startup, and lifecycle references from the Railyard files used by the project.

`review_result=reject` routes the ticket back to `ready` for `runner`. A rejected ticket remains in the closed-loop workflow. If Runner redispatch is authorized on the current platform, an execution-capable Runner path is available, and the same-kind failure limit has not been reached, the Architect dispatches or spawns a Runner for the rejected ticket instead of stopping. If platform rules require explicit Human authorization before spawning and none was granted, no safe Runner path exists, or the failure limit has been reached, the Architect reports a blocker with the rejected ticket id, rejection reason, current state, outbox existence, and exact spawn-ready prompt or dispatch command.

## Failure Taxonomy (Blocker Categories)

When a Runner cannot complete a ticket, it must not simply fail or retry indefinitely. Instead, it must record `runner_result=blocked` and report a blocker using one of the following categories:

- `permission_denied`: Command blocked by OS or sandbox.
- `command_failed`: Command returned an error or non-zero exit code.
- `sandbox_boundary`: Attempted access outside the assigned ticket scope.
- `authorization_required`: Requires human intervention (e.g., explicit permission to spawn a subagent).
- `environment_issue`: Missing tools, dependencies, or an incorrect environment.
- `unresolved_dependency`: Blocked by an external or cross-lane dependency.

This taxonomy ensures that blockers are actionable and that the Architect or Human can quickly identify the root cause.

Permission denial, network denial, missing secrets, and missing required tools are blockers unless the ticket explicitly provides an approved fallback. They must not be faked, bypassed, silently skipped, or replaced with unrelated tools.

Human-required blockers must include:

- ticket id
- lane
- blocker category
- intended operation
- commands attempted
- exact errors
- current ticket state
- whether the outbox result exists
- required Human action
- recommended next action

After recording a human-required blocker, the agent stops. It does not continue by trying alternate lifecycle helpers, raw SQL, unapproved credentials, unapproved network access, or broader filesystem access.

## Permission And Scratch-State Boundary

Permission escalation is a Human boundary, not an Architect-to-Runner delegation. An Architect may dispatch a Runner, but it must not approve a child agent's sandbox, filesystem, network, or destructive-operation escalation unless the Human has explicitly granted that approval for the requested action.

If a Runner hits a permission boundary that is outside the ticket contract, the Runner must record `runner_result=blocked` with the exact command, error, and needed approval. It must not bypass the boundary by writing to a different workflow state store or by mutating state outside the helper contract.

Railyard uses one authoritative workflow database per project:

- **Authoritative workflow DB**: `.workflow/workflow.db` is the single source of truth for tickets, epics, claims, reviews, and lifecycle transitions.
- **Disposable validation DBs**: Smoke tests and MCP-lite probes may copy the workflow database to a temporary directory and run against that copy.

**Agents must never:**
- Write scratch files, copied databases, or probe state inside `.workflow/`.
- Treat a copied validation database as authoritative workflow state.
- Copy generated ticket, epic, or outbox files into documentation directories unless the ticket explicitly asks for documentation fixtures.

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
python railyard/scripts/ticket.py --lane system recover-stale --ticket-id SYSTEM-DEMO-001 --actor runner --reason "runner interrupted before outbox"
```

`recover-stale` only supports interrupted Runner tickets in `running` state. It resets the ticket to `status=ready`, `next_actor=runner`, clears claim and result fields, and records a `recover-stale-running` workflow event with the recovery reason and previous claim metadata.

`sync-mailbox --reset-lifecycle --ticket-id <ID>` can still reset lifecycle fields from inbox and outbox files, but it is a broad mailbox sync operation. Prefer `recover-stale` for interrupted running Runner tickets because it validates the current state and refuses recovery when an outbox result already exists.

## Retry Stop Rule

Agents must stop retry loops before they become unattended drift.

For the same ticket and the same intended lifecycle operation, an Architect or Runner may make at most three same-kind failed attempts. After the third failure, the agent must stop trying alternate commands and record or report a blocker.

The blocker must include:

- ticket id
- lane
- intended operation
- commands attempted
- exact errors
- current ticket state
- whether the outbox result exists
- recommended next action

The retry limit applies to recovery, claim, dispatch, result marking, review transitions, validation, and permission-gated commands. Repeating the same command with cosmetic changes counts as another attempt. Trying a different helper command for the same intended state change also counts as another attempt. Same-kind failures include repeated helper transition failures, repeated validation failures with the same cause, repeated permission or network denials, repeated missing-secret failures, repeated missing-tool failures, and repeated platform dispatch failures.

The retry limit does not authorize bypassing helper scripts, direct SQLite edits, broad reset commands, cross-lane mutation, automatic model routing, telemetry, token cost tracking, or a heavy observability system.

## Restricted-Runner Mode

Restricted-runner mode is a platform permission fallback for environments where a Runner can edit source files but must not write Control workflow state or Control outbox files. The Architect owns the Control lifecycle, including claim, result marking, review, and outbox writes. The Runner edits only allowed source files, runs validation, removes any allowed temporary probe state it created, and returns exact JSON-compatible result content for the Architect to record.

## Validator Boundary

The Validation Report is optional review evidence. It does not replace Architect review.

- The Validator is read-only: it inspects artifacts and produces reports.
- The Validator does not modify artifacts, create tickets, close epics, or execute lifecycle transitions.
- Architect review decisions remain unchanged.
- Planner epic/release closure decisions remain unchanged.

### Validator dispatch failure boundary

Validator evidence must come from a Validator role execution or an explicitly authorized role-collapsed check.

If Validator dispatch is unavailable, blocked, or produces no retrievable output, the Architect or Planner must:

1. Report the dispatch failure.
2. Emit the exact spawn-ready Validator prompt and payload.
3. Stop the Validator step until a Human or external Validator session returns a report.

The Architect or Planner must not create temporary validation scripts, ad hoc validators, direct shell validators, or replacement tooling to simulate an independent Validator. They must not label self-run checks as a Validator report. Explicit Human-authorized role collapse is the only exception, and the output must be labeled as role-collapsed evidence.

### Validator report effect on Architect review

The Architect uses the Validation Report as structured evidence when deciding the review result. The `overall_verdict` from the Validation Report maps to the Architect's review decision as follows:

| Validation Report `overall_verdict` | Architect review action |
|---|---|
| `pass` | Accept as supporting evidence; Architect still reviews scope/diff independently. |
| `fail` | Reject ticket (`review_result=reject`) or redispatch Runner with focused remediation prompt. |
| `blocked` | Do not accept; collect missing evidence, permission, or artifact. |
| `inconclusive` | For high-risk tickets, do not accept; provide missing contract/evidence or escalate. |
| `human_review_required` | Stop and request Human decision before recording review result. |
| `warn` + `fail` (no `warnings_as_errors`) | Record as non-blocking warning. Architect decides whether it affects acceptance based on risk and ticket acceptance criteria. |
| `warn` + `fail` (`warnings_as_errors` = true) | Treat as error-level finding; affects overall verdict. |
| `error` + `fail` | Cannot accept; must reject or redispatch. |

**Source-to-derived rule:** The candidate output must never be the truth source for expected values. Every derived field must have an independent source mapping or declared transformation. For high-risk tasks, missing mapping policy should be `fail` or `human_review_required`, not silent pass. See `references/validator-protocol.md` Sections 5, 6, and 7 for truth hierarchy, severity/status independence, and source-to-derived reconciliation.

**Vague acceptance criteria:** The Architect must not pass vague natural-language acceptance criteria directly to the Validator. If acceptance criteria are vague (e.g., "validate that implementation correctly transforms source artifact" without mapping or evidence expectations), the Architect must translate them into concrete validation input or mark validation as `inconclusive` / `human_review_required`.

### Validator report effect on Planner closure

The Planner uses the Validation Report as structured evidence when deciding epic closure or release readiness. The `overall_verdict` from the Validation Report maps to the Planner's decision as follows:

| Validator `overall_verdict` | Planner action |
|---|---|
| `pass` | May close epic or proceed release after Planner judgment |
| `fail` | Open follow-up ticket or block closure |
| `blocked` | Collect missing evidence before deciding |
| `inconclusive` | Request more evidence; do not close high-risk epic |
| `human_review_required` | Stop; await Human decision |

**The Validator report is Planner evidence only, NOT closure authority.**
The Validator report does NOT close epic, record lifecycle, or replace Planner judgment.
The Planner still closes epic through the epic helper.
The Validator report informs but does not dictate the decision.

**Scope exclusions:** The Planner-side Validator does NOT handle Runner-side Validator self-check, runtime orchestration, automatic repair or remediation, model routing or automatic dispatch, or business-specific rules.

## MCP-lite Lifecycle Boundary

The optional MCP-lite server is a stdio adapter over helper-backed lifecycle behavior. It does not change the workflow state model.

Canonical state remains in SQLite. Lifecycle transitions remain owned by the helper functions. MCP write tools are limited to the same bounded transitions that a role may perform through helpers:

- `claim_ticket`
- `recover_stale_ticket`
- `start_review`
- `mark_runner_result`
- `mark_review_result`

MCP read and inspection tools may return tickets, epics, ticket events, schema version, validation results, and Architect dispatch payloads. Dispatch returns a spawn-ready Runner payload; it does not execute the Runner and does not mark acceptance.

The MCP-lite lifecycle surface must not expose raw SQL, force reset, admin mutation, arbitrary source editing, direct ticket Markdown rewrite, broad `sync-docs` or `sync-mailbox` replacement, or replacement of the helper lifecycle contract.

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
