---
name: railyard
description: "Use when a project follows a structured operating model with: two work lanes (Domain, System), unresolved work tracked as Epic, bounded execution tracked as Ticket, role-based behavior (architect, runner), helper-backed SQLite workflow tables, inbox and outbox files as task and result bodies."
---

# Railyard Workflow

Use this skill when a project follows a structured operating model with:
- two work lanes: `Domain` and `System`
- unresolved work tracked as `Epic`
- bounded execution tracked as `Ticket`
- role-based behavior such as `architect` and `runner`
- helper-backed SQLite workflow tables
- inbox and outbox files as task and result bodies

## Core Rules

- Treat `Epic` as unresolved planning and dependency truth.
- Treat `Ticket` as bounded execution and review truth.
- Resolve the correct lane before acting.
- Resolve the current role before acting.
- Prefer the official helper scripts over direct SQL.
- Treat mailbox files as body surfaces, not as workflow truth, unless the project explicitly says otherwise.
- Keep review explicit. Runner completion does not equal final acceptance.
- Prompt text can add constraints, but it does not replace required Railyard role, lifecycle, and startup protocol reads.
- The Planner or Architect that drafts or publishes a ticket must explicitly decide whether independent Validator evidence is required before Architect acceptance and record that decision in the ticket metadata.
- Historical tickets with neither `validator_required` nor `validator_gate_reason` remain valid and usable. Their missing metadata is a legacy unknown state, not an inferred `validator_required=false` decision.
- Tickets involving data transform, ingest, migration, source-to-derived artifacts, generated artifacts with measurable constraints, high-risk implementation, or derived authoritative data require Validator gate consideration.
- When a ticket requires Validator evidence, Architect acceptance is prohibited until a verifiable independent Validator report reference record resolves to a `pass` report under the ticket's declared failure behavior.
- Epic closure is a lane-level Planner responsibility. The Planner reviews finalised scoped or linked tickets, accepted review outcomes, the epic done definition, remaining open work, blockers, and dependencies before closure.
- Architects provide closure-readiness evidence but must not close epics by default; Runners may only provide evidence that an epic is ready for Planner closure.
- When dispatching execution, prefer `scripts/architect.py dispatch-next-runner` so the Architect receives a spawn-ready Runner prompt.
- Default Architect dispatch is closed-loop: The Architect that dispatches Runner work must review the Runner result and record a review result unless an explicit opt-in human-gated exception is declared.
- Architect review must read the Railyard role/startup/lifecycle references before recording review. A reject routes the ticket back to Runner; it does not end the closed loop when Runner redispatch is authorized.
- Architect may dispatch or spawn a Runner to fix rejected work. Architect must not personally implement the rejected fix unless the Human explicitly changes the role boundary.
- After `review_result=reject`, Architect redispatches Runner automatically when an execution-capable dispatch path is available, current authorization allows it, and the same-kind failure limit has not been reached.
- If redispatch needs Human spawn authorization, no safe Runner path exists, or three same-kind failures have occurred, stop and report a blocker with the rejected ticket id, reason, current state, and exact next dispatch prompt or command.
- Treat `awaiting_review` as an intermediate handoff state, not an Architect completion state.
- Treat human-gated review as opt-in. It must be declared in the ticket, handoff, or project protocol.
- Treat `partial` as honest, reviewable, in-scope work that is incomplete. Treat `blocked` as a stop condition requiring outside action before progress can continue.
- Do not write scratch files, copied databases, or probe state inside `.workflow/`.
- Do not treat a copied validation database as authoritative workflow state.
- Do not copy generated ticket, epic, or outbox files into documentation directories unless the ticket explicitly asks for documentation fixtures.
- Do not place validation scratch state, copied workflow databases, or probe temp files inside `.workflow/`.
- Do not authorize a child agent's permission escalation on behalf of the Human. If a Runner hits a sandbox or permission boundary outside its ticket contract, record a blocker with the exact command and error.
- Do not fake or bypass permission, network, missing-secret, or missing-tool blockers. Use `blocked` unless the ticket explicitly supplies an approved fallback.
- When Human action is required, report a blocker with category, ticket id, lane, intended operation, attempted commands, exact errors, current ticket state, outbox existence, required Human action, and recommended next action.
- If the current platform requires explicit Human authorization before spawning subagents and no authorization was given, report a spawn authorization blocker with the spawn-ready Runner prompt instead of stopping silently.
- Before drafting or dispatching Runner work, inspect running tickets. If a `running` Runner ticket has no outbox result and the Runner was interrupted, use `ticket.py recover-stale` with a reason instead of drafting around it.
- Stop after three failed attempts for the same ticket and intended operation. Record a blocker with commands, errors, current state, outbox existence, and recommended next action instead of continuing to try alternate helper commands.
- Count same-kind failures toward the three-attempt limit, including repeated helper failures, validation failures with the same cause, permission or network denials, missing-secret failures, missing-tool failures, and platform dispatch failures.
- Use restricted-runner mode only as a platform permission fallback: Architect owns Control lifecycle and Control outbox writes; Runner edits only allowed source files and returns exact JSON-compatible result content.
- Treat MCP-lite as an optional tool adapter over helper-backed operations. SQLite remains canonical workflow state, and helper functions remain lifecycle authority.
- Do not expose or rely on MCP tools for raw SQL, force reset, admin mutation, arbitrary source editing, direct ticket Markdown rewrite, broad `sync-docs` or `sync-mailbox` replacement, or replacement of the helper lifecycle contract.
- Treat Runner, Worker, Architect, and Reviewer as Railyard workflow roles, not portable platform `agent_type` names.
- When dispatching execution, use the current platform's documented execution-capable agent or a project-defined Railyard Runner profile. If no safe execution-capable dispatch path is known, fail fast instead of guessing.
- Match platform agents by capability, not by name. Runner dispatch requires read, write, execute, scoped file edit, and result JSON capabilities.
- Do not use read-only or planning platform agents for Runner implementation tickets.
- Do not treat `.codex/`, `.claude/`, `.cursor/`, `.windsurf/`, or other platform-local configuration as Railyard lifecycle authority.
- Runner dispatch prompts must require startup reads before claim or edits. New Runner result JSON should include non-empty `protocol_reads` evidence; historical results without it remain valid.

## Execution Profile

Railyard agents should indicate their confidence level and provide supporting evidence in new results:

- **Confidence**: `high`, `medium`, or `low`.
- **Evidence**: A collection of file paths, command outputs, or logs that justify the stated confidence.

Runner results should also include a lightweight `runner_trace` audit object as recommended optional evidence. Missing `runner_trace` remains valid for backward compatibility; malformed `runner_trace` is rejected when present. This trace is for comparison and review, not for telemetry or heavy observability. It records the platform name when available, agent profile when available, attempts count, exact command list, blocker category when blocked, and next action when blocked or partial. It must not include token cost statistics, automatic model routing decisions, or failure redispatch policy.

## Runner Result Boundaries

- `done`: the ticket scope is complete and validation evidence is available.
- `partial`: a reviewable subset is complete inside the ticket scope, but the full ticket is not complete. Include remaining work and a concrete `next_action`.
- `blocked`: progress cannot continue without outside action. Use this for permission denial, network denial, missing secrets, missing required tools, unresolved dependencies, unsupported dispatch, or required Human authorization.
- `invalid`: the Runner result cannot be trusted or does not satisfy the result contract.

Blocked work stops. Do not replace missing credentials with dummy values, skip required network validation while reporting success, substitute unrelated tools, broaden file scope, write alternate workflow state, or mutate SQLite directly to make the ticket appear complete.

## Failure Taxonomy (Blocker Categories)

When an agent cannot complete a task, it must not simply fail or retry indefinitely. Instead, it must report a **blocker** using one of the following categories:

- `permission_denied`: Command blocked by OS or sandbox.
- `command_failed`: Command returned an error or non-zero exit code.
- `sandbox_boundary`: Attempted access outside the assigned ticket scope.
- `authorization_required`: Requires human intervention (e.g., explicit permission to spawn a subagent).
- `environment_issue`: Missing tools, dependencies, or an incorrect environment.
- `unresolved_dependency`: Blocked by an external or cross-lane dependency.

This taxonomy ensures that blockers are actionable and that the Architect or Human can quickly identify the root cause.

Human-required blockers must include:

- `category`
- `ticket_id`
- `lane`
- `intended_operation`
- `commands_attempted`
- `exact_errors`
- `current_ticket_state`
- `outbox_exists`
- `required_human_action`
- `recommended_next_action`

## v0.7 Validation Contract Foundation

Railyard v0.7 adds a generic, development-time-first validation contract foundation. It defines how a Validation Contract is applied to artifacts and produces a Validation Report, without embedding project-specific business rules and without runtime orchestration.

The foundation defines three artifacts:

- **Validation Contract**: a declarative specification with `contract_id`, `version`, `description`, `applies_to`, and `rules`.
- **Validation Report**: structured output with `contract_id`, `contract_version`, and per-artifact `results` including `overall_verdict` and `findings`.
- **Validator**: a read-only component that applies contracts and produces reports; it does not perform automatic repair, retry, remediation, or runtime orchestration.

### Validation Contract Ownership

The Validation Contract follows defined ownership: Human defines unacceptable risk, Planner records contract intent at the epic level, Architect produces the executable contract at the ticket level, Runner implements against the contract, Validator checks against the contract. Contract intent lives in the Epic; the executable contract lives in the Ticket or a ticket-scoped artifact. No role silently redefines a contract produced by another role. When a contract is missing or insufficient, the workflow must not silently pass; it escalates as `inconclusive`, `human_review_required`, or `blocked`. See `references/validation-contract.md` for the full protocol.

The repository includes two development-time reference scripts:

- `scripts/validate_artifacts.py` validates artifact shape (tickets, epics, result files, queue examples, validation contracts, and validation reports) using built-in structural checks. It is not independent Validator role evidence and cannot satisfy a ticket's required Validator gate.
- `scripts/validator.py` runs a minimal executable Validator for source-to-derived field-mapping checks and emits the Validator Protocol report shape.

`scripts/validator.py` supports source and derived JSON artifacts, a field mapping contract, required field mappings, `identity`, `multiply_by_2`, `parse_integer`, `parse_number_preserve_sign`, `missing_mapping_policy`, and `warnings_as_errors`. It is read-only with respect to input artifacts and workflow state. It does not implement automatic repair, runtime orchestration, model routing, lifecycle writes, or business-specific rules.

Run it with:

```powershell
python scripts\validator.py --input examples\source-derived-mapping-review\validator-input.json
python scripts\validator.py --input examples\source-derived-mapping-review\validator-input.json --output report.json
```

### Validation Report verdicts and findings

Overall verdict values: `pass`, `fail`, `blocked`, `inconclusive`, `human_review_required`.
Finding severity values: `error`, `warn`, `info`.
Finding status values: `pass`, `fail`, `not_applicable`, `blocked`, `inconclusive`.

Internal consistency rules:
- `overall_verdict=pass` means no finding with `severity=error` AND `status=fail`, no `status=blocked`, and no `status=inconclusive`.
- `overall_verdict=pass` allows findings with `severity=warn` AND `status=fail`.
- `overall_verdict=fail` means at least one `severity=error` AND `status=fail` finding.
- `overall_verdict=blocked` means at least one `status=blocked` finding.
- `overall_verdict=inconclusive` means at least one `status=inconclusive` finding.
- `overall_verdict=human_review_required` means the findings, missing evidence, or notes identify an issue that requires manual review before a verdict can be assigned.

Future external or runtime-adjacent validation is acknowledged as an extension, not implemented in this foundation ticket; later queued validation work may extend the same contract model.

## Non-Goals

- Railyard is not an automated error recovery system.
- It is not a replacement for human judgment.

## Working Sequence

1. Resolve lane: `Domain` or `System`.
2. Resolve role: `architect` or `runner`.
3. Resolve object type: `Epic` or `Ticket`.
4. Use the official helper script for that lane and object.
5. Read the specific inbox or outbox file only after workflow state is known.
6. Before dispatching new Runner work, list running Runner tickets and recover interrupted no-outbox tickets with `recover-stale`.
7. If the same intended operation has three same-kind failures for the same ticket, stop and report a blocker.
8. Architect review reads `SKILL.md`, `references/roles.md`, `references/startup-sequence.md`, and `references/lifecycle.md` before recording review.
9. For Runner execution, let the Architect dispatch the next ticket and spawn an execution-capable subagent with the returned prompt when the environment supports subagents and authorization allows it.
10. Map the returned Runner prompt to the current platform through `references/platform-dispatch.md`; do not require a literal `worker` agent type.
11. Runner reads the required Railyard role/startup references before claim or edits, then records those paths in `protocol_reads` when available.
12. Runner records the runner outcome through the helper.
13. Architect inspects the Runner result and validation, then records the review outcome through the helper.
14. If review rejects a ticket, continue the closed loop by dispatching a Runner again when authorized and under the three-attempt limit; otherwise report a blocker with the exact spawn-ready prompt or dispatch command.
15. Report Architect completion only after the ticket is finalised, routed back to Runner with redispatch blocked, routed back to Architect, or blocked with a clear next action.

## MCP-lite Boundary

The optional v0.3 MCP-lite server exists for workflow integration through stdio. Use it only for narrow workflow operations that mirror existing helper behavior:

- inspect tickets, epics, ticket events, and schema version
- dispatch the next Runner ticket and receive a spawn-ready Runner prompt
- claim tickets, recover interrupted running tickets, start review, mark runner result, and mark review result
- validate result payloads and ticket state

Lane-specific tools must receive an explicit `lane` value. Probes and smoke checks must operate on a copied database and keep temporary files outside `.workflow/` unless a ticket explicitly authorizes a live workflow transition.

## Recommended Entry Commands

```powershell
python railyard/scripts/epic.py --lane domain list-open
python railyard/scripts/architect.py --lane domain --runner-name domain-runner-1 dispatch-next-runner
python railyard/scripts/ticket.py --lane domain next --actor runner
python railyard/scripts/ticket.py --lane system show --ticket-id SYSTEM-DEMO-001
```

## Read Next

- Abstract workflow model: `references/model.md`
- Startup sequence and E2E smoke checks: `references/startup-sequence.md`
- Role behavior: `references/roles.md`
- Routing rules: `references/routing.md`
- Platform dispatch rules: `references/platform-dispatch.md`
- Lifecycle contract: `references/lifecycle.md`
- SQL contract: `references/sql-contract.md`
- Helper command examples: `references/helper-commands.md`
- Epic document format: `references/epic-format.md`
- Ticket document format: `references/ticket-format.md`
- Result file format: `references/result-format.md`
- Validator protocol: `references/validator-protocol.md`
