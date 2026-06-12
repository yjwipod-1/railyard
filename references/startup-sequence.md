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
docs/domain/epics/
docs/domain/inbox/
docs/domain/outbox/
docs/system/epics/
docs/system/inbox/
docs/system/outbox/
docs/templates/
```

By default, workflow authority state stays inside the Railyard installation:

```text
railyard/.workflow/workflow.db
railyard/.railyard-workflow.json
```

`.railyard-workflow.json` is the local workflow authority record. It records the
resolved project root, workflow root, and workflow database used by this
Railyard installation. It is generated local state and is not committed.

On a cold start with no authority record and no existing workflow database,
`init_workflow.py` creates `railyard/.workflow/workflow.db` and records it. On
an existing project with no authority record, initialization scans the target
project and Railyard installation for valid `.workflow/workflow.db` candidates.
If any are found, initialization must ask the Human which database is
authoritative and record that selection. It must not silently create a second
workflow database. Non-interactive migrations must pass an explicit
`--db-path`.

Every Planner, Architect, and Runner session must check
`railyard/.railyard-workflow.json` before using workflow helpers. Use the
recorded workflow database path explicitly with helper commands. If the record
is missing, run `init_workflow.py` to perform cold-start initialization or
legacy database discovery before continuing.

The `.github/agents/` directory contains default Railyard agent profiles for platforms that support VS Code / GitHub Copilot-style custom agents. Platforms that do not read this directory can still use the same profile text as prompt material.

## 2. Confirm The Schema

```powershell
python railyard/scripts/workflow_schema.py ensure --db railyard/.workflow/workflow.db
python railyard/scripts/workflow_schema.py tables --db railyard/.workflow/workflow.db
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

## 4. Runner Protocol Requirements

Before claiming or editing a ticket, every Runner must read the following Railyard protocol files to understand their role, the startup sequence, and the lifecycle:

- `railyard/SKILL.md`
- `railyard/references/roles.md`
- `railyard/references/startup-sequence.md`
- `railyard/references/lifecycle.md`

Failure to read these files before claim or edits is a violation of the Railyard contract. New Runner results should record the actual paths in `protocol_reads`; historical results without `protocol_reads` remain valid.

## 5. Create Or Sync Epics

The Planner records contract intent, done definition, closure criteria, and unacceptable failure modes in each Epic. Contract intent at the epic level drives the Architect's executable contract and Validator dispatch at the ticket level. See `references/validation-contract.md` for the full ownership and handoff protocol.

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

## 6. Create Tickets

The Planner or Architect that drafts or publishes a ticket must decide whether
independent Validator evidence is required before Architect acceptance.

Every new ticket records:

- `validator_required`: `true` or `false`
- `validator_gate_reason`: rationale for the decision

Tickets involving data transform, ingest, migration, source-to-derived
artifacts, generated artifacts with measurable constraints, high-risk
implementation, or derived authoritative data require Validator gate
consideration.

When `validator_required: true`, the ticket also records:

- `validator_risk_level`
- `validator_contract_source`
- `validator_expected_artifacts`
- `validator_evidence_pack`
- `validator_failure_behavior`

These fields must give the Architect enough information to dispatch the
Validator before acceptance without inventing missing criteria during review.
See `references/ticket-format.md` for the metadata contract.

Historical tickets with neither `validator_required` nor
`validator_gate_reason` remain readable, syncable, dispatchable, and valid
under artifact-shape validation. This legacy missing state is not
`validator_required: false` and must not be rewritten or recorded as a no-gate
decision.

Ticket Markdown files live in:

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

Tickets may also be drafted directly through the helper. The helper requires an
explicit Validator gate decision:

```powershell
python railyard/scripts/ticket.py --lane domain draft --epic-id DOMAIN-E001 --title "Define scope" --task "Write docs/scope.md." --validator-not-required --validator-gate-reason "Low-risk documentation-only ticket."
```

For a required gate, use `--validator-required` with
`--validator-risk-level`, `--validator-contract-source`,
`--validator-expected-artifact`, `--validator-evidence-item`, and
`--validator-failure-behavior`.

`scripts/validate_artifacts.py` can verify the ticket artifact shape and gate
metadata shape. It cannot satisfy an independent Validator evidence
requirement. Runner verification commands and Architect self-review also cannot
satisfy that requirement.

## 7. Architect Dispatch

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
spawn.profile_hints
spawn.prompt_format
spawn.required_startup_reads
spawn.prompt
```

When the operating environment supports subagents, map this payload to that environment's spawn mechanism and pass `spawn.prompt` as the runner instruction.

Before spawning, apply `references/platform-dispatch.md`. Railyard Runner is a workflow role, not a required platform `agent_type`. `spawn.agent_type` and `spawn.platform_agent_type` may be `null` until the host adapter selects a documented execution-capable platform surface. Use capability matching, not name matching: Runner dispatch requires read, write, execute, scoped file edit, and result JSON capabilities. Use a documented or discovered platform-native execution agent first. If platform-native selection is missing, ambiguous, or unsafe, use the Railyard fallback profile when the platform supports custom or prompt-defined agents. Do not use read-only or planning agents for Runner implementation, and fail fast if no safe execution-capable dispatch path is known.

Architect dispatch is a closed-loop responsibility by default. The Architect that dispatches Runner work must resume after the Runner result, inspect the outbox result and validation evidence, then complete Section 9 review. Dispatch is not complete while the ticket remains in `awaiting_review`.

An Architect may leave a ticket in `awaiting_review` only when a blocker is recorded or when the ticket, handoff, or project protocol explicitly declares opt-in human-gated review.

The Architect may not approve a spawned Runner's sandbox, filesystem, network, or destructive-operation escalation unless the Human has explicitly approved that exact action. Permission denial is a blocker, not an invitation to bypass the workflow helper or write into another workflow state store.

Before drafting or dispatching new Runner work, the Architect should inspect running tickets:

```powershell
python railyard/scripts/ticket.py --lane domain list --status running --next-actor runner
python railyard/scripts/ticket.py --lane system list --status running --next-actor runner
```

If a ticket is still `running` but the Runner session was interrupted before writing its outbox result JSON, recover it before dispatching later tickets in the same lane:

```powershell
python railyard/scripts/ticket.py --lane system recover-stale --ticket-id SYSTEM-DEMO-001 --actor runner --reason "runner interrupted before outbox"
```

Use `--dry-run` first when inspecting an uncertain case. If the outbox result JSON exists, do not recover the ticket; run `mark-runner-result` instead. `sync-mailbox --reset-lifecycle --ticket-id <ID>` remains a lower-level fallback that resets lifecycle fields from inbox and outbox files, but `recover-stale` is the intended recovery path for interrupted running Runner tickets.

Do not use `claim`, `draft`, `next --ticket-id`, or raw SQLite updates to recover stale running tickets. Those commands either require a different lifecycle state or create a different object.

If recovery, dispatch, claim, result marking, review, validation, or permission-gated work fails three times for the same ticket and intended operation, stop and record a blocker. The blocker should include the commands attempted, exact errors, current ticket state, outbox existence, and recommended next action.

Same-kind failures count toward the three-attempt limit even when the exact command changes. This includes repeated helper transition failures, validation failures with the same cause, permission or network denials, missing-secret failures, missing-tool failures, and platform dispatch failures. The retry limit does not permit raw SQLite edits, broad lifecycle resets, unapproved credentials, unapproved network access, automatic model routing, telemetry, token cost tracking, or a heavy observability system.

## 7.5. Architect-to-Validator Dispatch

The ticket drafter records whether Validator evidence is required. Before or
during Architect review, the Architect verifies that decision and dispatches
the Validator when required. The Validator is a read-only quality gate that
inspects artifacts and produces a Validation Report; it does not modify the
system, make lifecycle decisions, or perform remediation. See
`references/validator-protocol.md` for the complete protocol.

### When to dispatch the Validator

The Architect MUST dispatch the Validator when the ticket involves any of the following:

- **Data transform**: any extract, transform, load, or data shape change.
- **Ingest / migration**: ingesting external data, migrating data between stores or schemas.
- **Source-to-derived artifacts**: any generated artifact produced by transforming source artifacts.
- **Generated artifacts with measurable constraints**: output with structural, numeric, or content constraints that can be checked.
- **High-risk implementation tickets**: tickets where incorrect output would cause data loss, inconsistency, or downstream failures.
- **Acceptance depends on semantic or structural correctness**: ticket acceptance requires verifying artifact structure, field mappings, or value transformations beyond simple command success.
- **Derived authoritative data**: implementation creates derived data or content used by later steps.

If the Architect is unsure whether the ticket warrants Validator dispatch, the Architect should dispatch the Validator with `risk_level=medium` and let the Validator report determine whether findings are material.

A ticket with `validator_required: true` must not be accepted based only on
`scripts/validate_artifacts.py`, Runner verification scripts, or Architect
self-review. Those checks remain useful evidence, but they do not execute the
independent Validator role.

### Validator dispatch failure boundary

Validator dispatch is a role boundary. If the platform cannot dispatch a Validator subagent, or if a dispatch appears to start but no Validator output can be retrieved, the Architect must not implement the Validator inside the Architect session.

Required behavior:

1. Treat the Validator dispatch as unavailable or blocked.
2. Emit the exact spawn-ready Validator prompt and payload.
3. Stop the Validator step until a Human or external Validator session returns a report.

Forbidden behavior:

- Do not create temporary validation scripts.
- Do not run ad hoc shell/Python validators as a replacement for Validator dispatch.
- Do not label Architect-generated checks as a Validator report.
- Do not continue to review as if independent Validator evidence exists.

Explicit Human-authorized role collapse is the only exception. If role collapse is authorized, label the output as a role-collapsed check, not an independent Validator report.

### Architect-constructed Validator input

When dispatching the Validator, the Architect MUST construct the following input. Before constructing the input, the Architect verifies that a sufficient executable contract exists at the ticket level. If the contract is missing, incomplete, or too vague, the Architect stops and escalates rather than dispatching the Validator with a fabricated contract.

| Slot | Description |
|---|---|
| `artifacts` | Source artifacts, candidate implementation, candidate output, relevant docs. |
| `validation_contract` | Explicit contract if present; otherwise an Architect-generated generic contract pattern based on ticket acceptance criteria. |
| `acceptance_criteria` | Ticket acceptance criteria translated into concrete, checkable criteria. Vague natural-language acceptance criteria must be translated; if they cannot be translated, the Architect marks validation as `inconclusive` or `human_review_required`. |
| `evidence_pack` | Raw source values, headers, schemas, command outputs, logs. |
| `risk_level` | `low`, `medium`, or `high` based on ticket risk. |
| `allowed_read_only_commands` | Explicit list of read-only commands the Validator is authorized to run. |
| `truth_hierarchy` | Reference `references/validator-protocol.md` Section 5. The candidate output must never be the truth source. |

### Source-to-derived default rule

If the ticket involves extract/transform/ingest/migration/source-to-derived output:

- The Architect MUST include the source-to-derived reconciliation pattern from `references/validator-protocol.md` Section 7.
- Candidate output must never be the truth source.
- Every derived field must have an independent source mapping or a declared transformation.
- Missing mapping policy for high-risk tasks must be `fail` or `human_review_required`, never a silent pass.
- The Architect must not accept high-risk source-to-derived tickets if the Validator returns `inconclusive`, `blocked`, or `human_review_required` without a Human decision.

### Architect dispatch template

The Architect uses the following copyable template to dispatch the Validator. This template is a compact summary; the full protocol is in `references/validator-protocol.md`.

```text
Validator Dispatch Template
============================

artifacts:
  - path: <source artifact path>
    kind: source
  - path: <derived artifact path>
    kind: derived
  - path: <contract/mapping file>
    kind: contract
  - path: <candidate implementation>
    kind: implementation

validation_contract: <contract_id if explicit, otherwise "generated from ticket acceptance_criteria">
generated_contract_pattern: <describe rules derived from acceptance_criteria>

acceptance_criteria: <concrete, checkable criteria derived from ticket>

evidence_pack:
  - <raw source values, headers, schemas>
  - <command outputs, logs>

risk_level: low | medium | high

allowed_read_only_commands:
  - <read-only command 1>
  - <read-only command 2>

truth_hierarchy: See references/validator-protocol.md Section 5.
  Validation contract / field mapping contract > source headers / metadata / schemas / docs > raw source values > candidate implementation > candidate output.
  Candidate output must never be the truth source.

source_to_derived: Include reconciliation rules from references/validator-protocol.md Section 7.
  - Every derived field must map to a source field or declared transformation.
  - Missing mapping policy: <fail | human_review_required | inconclusive>.
  - Candidate output must never be the truth source.

output_contract:
  The Validator MUST return exactly one JSON object conforming to
  references/validator-protocol.md Section 3.
  - Do not return Markdown tables as the primary output.
  - overall_verdict must be lowercase: pass | fail | blocked | inconclusive | human_review_required.
  - confidence must be high | medium | low, not a number.
  - findings entries must include rule_id, severity, status, message, evidence.
  - finding severity must be error | warn | info.
  - finding status must be pass | fail | not_applicable | blocked | inconclusive.
  - missing_evidence, validated_artifacts, and commands_run must be arrays.
  - artifact_summary must be an object.

Reference: For the full Validator protocol, input/output schema, verdict semantics,
severity/status independence, and missing mapping policy, see references/validator-protocol.md.
```

### Validator result -> Architect review decision mapping

After receiving the Validation Report, the Architect maps the `overall_verdict` to a review decision:

| Validator `overall_verdict` | Architect action |
|---|---|
| `pass` | Can accept as evidence, but Architect still reviews scope/diff independently. |
| `fail` | Reject or redispatch Runner with focused remediation prompt. |
| `blocked` | Collect missing evidence / permission / artifact; do not accept. |
| `inconclusive` | For high-risk tickets, do not accept; provide missing contract/evidence or escalate. |
| `human_review_required` | Stop and request Human decision. |
| `warn` + `fail` (no `warnings_as_errors`) | Record as non-blocking warning. Architect decides whether it affects acceptance based on risk and ticket AC. |
| `warn` + `fail` (`warnings_as_errors` = true) | Treat as error-level finding; affects overall verdict. |
| `error` + `fail` | Cannot accept. |

For the canonical handoff tree covering who acts next, acceptance/closure
permissions, remediation, evidence, redesign, blocked handling, and Human
escalation for every verdict, see
`references/validator-verdict-handoff-tree.md`. This table is the
Architect-specific view derived from that tree.

### Vague acceptance criteria handling

The Architect must not pass vague natural-language acceptance criteria directly to the Validator. If AC is vague (e.g., "validate that implementation correctly transforms source artifact into derived artifact" without mapping or evidence expectations), the Architect must:

1. Translate it into concrete validation input (field mappings, expected transformations, evidence requirements).
2. If it cannot be translated into concrete checks, mark validation as `inconclusive` or `human_review_required` and escalate to the Human.

## 7.6. Planner-to-Validator Dispatch

The Planner may invoke the Validator as a read-only quality gate before epic closure or release readiness decisions. The Validator produces a Validation Report that serves as Planner evidence; the Planner retains all closure authority.

### Planner Validator dispatch failure boundary

Validator dispatch is a role boundary for Planner closure and release readiness work. If the platform cannot dispatch a Validator subagent, or if a dispatch appears to start but no Validator output can be retrieved, the Planner must not implement the Validator inside the Planner session.

Required behavior:

1. Treat the Validator dispatch as unavailable or blocked.
2. Emit the exact spawn-ready Validator prompt and payload.
3. Stop the Validator step until a Human or external Validator session returns a report.

Forbidden behavior:

- Do not create temporary validation scripts.
- Do not run ad hoc shell/Python validators as a replacement for Validator dispatch.
- Do not label Planner-generated checks as a Validator report.
- Do not continue to closure as if independent Validator evidence exists.

Explicit Human-authorized role collapse is the only exception. If role collapse is authorized, label the output as a role-collapsed check, not an independent Validator report.

### When to invoke the Validator

**The Planner MUST invoke the Validator when:**

- release readiness decision
- high-risk epic closure
- cross-ticket consistency risk across multiple tickets
- public artifact hygiene risk (README, CHANGELOG, SKILL, examples)
- workflow / role / protocol contract changes (roles.md, lifecycle.md, validator-protocol.md, startup-sequence.md)

**The Planner MAY invoke the Validator when:**

- normal epic closure (when useful for evidence)

**The Planner should NOT invoke the Validator when:**

- typo fix or small docs edit
- isolated low-risk ticket with no cross-ticket impact
- no checkable artifact or evidence exists

### Planner-constructed Validator input

| Slot | Description |
|---|---|
| `epic_scope` | Epic scope definition and done definition |
| `ticket_state_table` | Current state of all scoped tickets (status, runner_result, review_result) |
| `runner_results` | Runner result JSONs from completed tickets |
| `architect_review_results` | Architect review results and review focus notes |
| `changed_files_summary` | Summary of changed files since last review |
| `validation_command_outputs` | Output from `python -m compileall`, `python scripts/validate_artifacts.py`, etc. |
| `public_hygiene_scan` | Evidence from public artifact scan (README, CHANGELOG, SKILL, examples) |
| `unresolved_blockers` | Any unresolved blockers or follow-ups |

### Planner verdict-to-action mapping

| Validator `overall_verdict` | Planner action |
|---|---|
| `pass` | May close epic or proceed release after Planner judgment |
| `fail` | Open follow-up ticket or block closure |
| `blocked` | Collect missing evidence before deciding |
| `inconclusive` | Request more evidence; do not close high-risk epic |
| `human_review_required` | Stop; await Human decision |

For the canonical handoff tree covering who acts next, acceptance/closure
permissions, remediation, evidence, redesign, blocked handling, and Human
escalation for every verdict, see
`references/validator-verdict-handoff-tree.md`. This table is the
Planner-specific view derived from that tree.

### Validator report effect

- The Validator report is **Planner evidence only**, NOT closure authority.
- The Validator report does NOT close epic, record lifecycle, or replace Planner judgment.
- The Planner still closes epic through the epic helper.
- The Validator report informs but does not dictate the decision.

### Scope exclusions

- NO Runner-side Validator self-check role.
- NO runtime orchestration.
- NO automatic repair or remediation.
- NO model routing or automatic dispatch.
- NO business-specific rules or content policy checks.

### Boundary vs Architect-side Validator

| Dimension | Architect-side Validator | Planner-side Validator |
|---|---|---|
| Who dispatches | Architect (per-ticket) | Planner (pre-closure) |
| When | During Architect review | Before epic/release closure |
| Scope | Single ticket artifact | Cross-ticket, epic-level, public hygiene |
| Output use | Review decision (accept/reject) | Closure decision (close/block) |
| Verdict effect | Maps to Architect review result | Maps to Planner closure evidence |

Reference: For the full Validator protocol including input/output schema, verdict computation, truth hierarchy, severity/status independence, and source-to-derived reconciliation, see `references/validator-protocol.md`.

## 8. Runner Execution

Before claiming or editing a ticket, the Runner reads the required Railyard startup references from the dispatch payload:

```text
railyard/SKILL.md
railyard/references/roles.md
railyard/references/startup-sequence.md
```

If a project keeps Railyard under another path, the Runner reads the equivalent Railyard files and records the actual paths in `protocol_reads`. If the Runner cannot locate equivalent role/startup references, it stops and reports a blocker rather than guessing the role contract.

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
docs/system/outbox/SYSTEM-DEMO-001.result.json
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

The result JSON should include a non-empty `protocol_reads` array. A missing or empty `protocol_reads` field means the Runner did not leave structured evidence that it read the role/startup contract, but historical results without `protocol_reads` remain valid.

The result JSON should include a lightweight `runner_trace` object as recommended optional audit evidence. Missing `runner_trace` remains valid for backward compatibility; malformed `runner_trace` is rejected when present. Record `platform_name` and `agent_profile` when known, `attempts` as the number of attempts for this ticket result, and `commands` as the exact ordered command strings executed. For `runner_status=blocked`, set `blocker_category` to one failure taxonomy value. For `runner_status=blocked` or `runner_status=partial`, set `next_action` to the concrete next step. This trace is not a heavy observability system and must not include token cost statistics, automatic model routing, or failure redispatch policy.

Use `partial` only for honest, reviewable, in-scope work that is incomplete. Use `blocked` when progress requires outside action, including permission approval, network access, a missing secret, a missing required tool, a platform dispatch capability, or an unresolved dependency. Do not fake blocked work with dummy credentials, skipped checks, unrelated tools, alternate workflow state, or raw database edits.

A human-required blocker in the result notes or blocker detail includes:

- category
- ticket id
- lane
- intended operation
- commands attempted
- exact errors
- current ticket state
- whether the outbox result exists
- required Human action
- recommended next action

After reporting a human-required blocker, the Runner stops.

In restricted-runner mode, the Runner cannot write Control lifecycle state or Control outbox files. The Runner edits only the allowed source files, runs validation, removes any allowed temporary probe state it created, and returns exact JSON-compatible result content. The Architect writes the Control outbox and performs lifecycle transitions.

## 9. Architect Review

Before starting or recording review, the Architect reads:

```text
railyard/SKILL.md
railyard/references/roles.md
railyard/references/startup-sequence.md
railyard/references/lifecycle.md
```

Prompt text can add ticket-specific or project-specific review rules, but it does not replace these Railyard protocol reads.

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
python railyard/scripts/ticket.py --lane system mark-review-result --ticket-id SYSTEM-001 --review-result accept --validator-report-record evidence/SYSTEM-001.validator-record.json
```

The second form is required for `accept` or `accept_with_changes` when the
ticket declares `validator_required: true`. The referenced report must verify
and have `overall_verdict: pass`. Non-passing Validator verdicts reject
acceptance. `reject` and `redesign` remain allowed without Validator evidence.

Architect review is mandatory in the default protocol. A Runner result of `done` means the Runner claims completion; it does not mean the ticket is accepted. Acceptance exists only after `mark-review-result` records `accept` or `accept_with_changes`.

If the Architect dispatched the Runner, the Architect must not report the overall task as complete until one review result is recorded or a specific blocker is reported. Human-gated review is opt-in and must be explicit before the Architect can leave raw Runner output for Human acceptance.

Accepted tickets move to:

```text
status=finalised
next_actor=none
```

Rejected tickets move back to `ready` for `runner`.

After `review_result=reject`, the Architect remains responsible for the closed loop. If the platform supports execution-capable subagents and the current session is authorized to spawn them, the Architect dispatches or spawns a Runner for the rejected ticket. This is not Architect implementation work; it is Architect dispatch work.

Architect redispatches automatically after reject when the rejected ticket is `ready` for `runner`, an execution-capable Runner path is available, current authorization allows dispatch, and the same-kind failure limit has not been reached.

If the current platform requires explicit Human authorization before subagent spawn and no authorization was given, no safe Runner path exists, or three same-kind failures have occurred, the Architect records a blocker instead of stopping silently. The blocker includes the rejected ticket id, rejection reason, current ticket state, outbox existence, and exact spawn-ready Runner prompt or dispatch command needed next.

Redesign tickets move back to `drafted` for `architect`.

## 10. Planner And Human Summary

Before summarizing completed lane work, the lane Planner reviews completed tickets (done definition, scope coverage, cross-ticket consistency, blockers, dependencies, and follow-up needs) to determine epic closure readiness. The lane Architect provides closure-readiness evidence but must not close the epic by default.

Epic closure requires checking:

- finalised ticket statuses
- accepted review outcomes
- Runner result evidence
- the epic done definition
- remaining open tickets in the epic scope
- blockers and dependencies

Runners do not close epics. Architects provide closure-readiness evidence but must not close epics merely because all currently scoped tickets appear finalised. Planner or Human direction may request closure, but the lane Planner records it through the epic helper.

After Architect review, the Planner summarizes:

- completed tickets
- accepted results
- rejected or blocked work
- cross-lane dependency changes
- recommended next epics or tickets

The Human makes final project-level decisions from this summary.

## 11. Minimal E2E Smoke Check

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

Validation scratch state must stay outside `.workflow/`. Probes that need writable workflow data should copy the database to a separate temporary directory, run against that copy, and verify the live database is unchanged unless the test is intentionally exercising a lifecycle transition through the helper.

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

## 12. Command Rule

When running helper scripts from outside the target project root, pass both `--project-root` and `--db` before the subcommand:

```powershell
python railyard/scripts/ticket.py --lane domain --project-root ../project --db ../project/.workflow/workflow.db next --actor runner
```

## 13. Optional MCP-lite Surface

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

MCP-lite is not a replacement for the helper scripts or the Railyard lifecycle contract. It must not expose raw SQL, force reset, admin mutation, arbitrary source editing, direct ticket Markdown rewrite, or broad `sync-docs` / `sync-mailbox` replacement.

## Workflow State Boundary

Railyard uses one authoritative workflow database per project:

- **Authoritative workflow DB**: `.workflow/workflow.db` is the single source of truth for tickets, epics, claims, reviews, and lifecycle transitions.
- **Disposable validation DBs**: Smoke tests and MCP-lite probes may copy the workflow database to a temporary directory and run against that copy.

**Agents must never:**
- Write scratch files, copied databases, or probe state inside `.workflow/`.
- Treat a copied validation database as authoritative workflow state.
- Copy generated ticket, epic, or outbox files into documentation directories unless the ticket explicitly asks for documentation fixtures.
