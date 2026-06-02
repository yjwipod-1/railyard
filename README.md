# Railyard

Agentic workflow with deterministic guardrails.

Railyard is a portable operating scaffold for long-running AI-agent projects. It separates planning, lane ownership, execution, review, and task state so that human-led agent work can survive across sessions, projects, and role boundaries.

This repository is a reference implementation extracted from real project use. It is not an agent runtime, a hosted service, or a Python package. It supplies the workflow structure, SQLite schema, templates, and helper scripts that an agent project can copy into its own workspace.

## Why Railyard

The name reflects the core idea: work moves through defined tracks, switching points, gates, and review stations. Agents do not freely wander through the whole project. They move through structured lanes.

A railyard is not a single train on a single track. It is a system where multiple trains move in parallel, each on its own track, coordinated by switches and signals rather than by giving every train the full map.

That is the design philosophy here. Runners operate within scoped tickets. Architects manage lane-level readiness and review. The Planner coordinates cross-lane decisions. The Human decides where the work should go.

## Why This Exists

Most multi-agent setups give every agent the whole project context and hope for the best. That works for short tasks. It breaks in long-running workflows where multiple agents need to operate in parallel across different concerns without losing coherence.

Railyard separates two problems that are usually tangled together:

- **System work**: tooling, storage, schemas, integrations, automation, platform mechanics.
- **Domain work**: product logic, analysis, content generation, validation rules, delivery semantics.

Each lane has its own Architect and Runners. A shared Planner handles cross-lane decisions. Task state lives in SQLite, not in agent memory.

## Problems This Solves

**Context window explosion.** Everything lives in one session. The agent loses track of what matters because it carries the entire project history in a single conversation. Railyard gives each role a scoped context. Runners see one ticket. Architects see lane-level work. The Planner sees the cross-lane view.

**Context pollution between concerns.** System implementation details leak into domain reasoning, or domain logic contaminates system decisions. Railyard separates System and Domain into independent lanes. Cross-lane influence happens through explicit dependency declarations, not shared context.

**No persistent state across sessions.** The agent forgets everything when the session ends. Railyard keeps epics, tickets, statuses, claims, results, and reviews in SQLite so any session can resume from durable state.

**No quality gate before output reaches the Human.** Work is first shaped by the Human and Planner, scoped by Architects, executed by Runners, then returned through Architect review before the Human and Planner make the next decision.

**Disposable execution sessions.** Runners receive a role definition, a ticket specification, and relevant references. They do not depend on prior conversation history, so a fresh session can execute a ticket and produce clean output.

**Token cost accumulation.** Planners and Architects can restart sessions at natural boundaries because the workflow state lives in the database and file surfaces, not in a long-running chat.

## Human-in-the-Loop by Design

Railyard assumes a Human at the top of every decision chain. This is not a guardrail added after the fact. It is a structural commitment.

The Human does not review every ticket or approve every Runner output. The Human operates at the highest level of abstraction:

- Sets project direction with the Planner.
- Reviews Planner-level summaries, not raw ticket output.
- Makes final calls on architecture decisions, scope changes, and cross-lane tradeoffs.
- Can intervene at any level, but does not need to watch every level.

Every other role exists to reduce the surface area the Human needs to monitor, not to remove the Human from the loop.

## Deterministic Guardrails

Railyard is an agentic framework, but not every part of it is agentic.

Agents handle reasoning, generation, and review. Those activities depend on model capability, prompt quality, and context. The guardrails around them are deterministic:

- **Task state transitions** follow fixed status values.
- **Visibility rules** keep Runners focused on ready tickets.
- **Lane boundaries** keep System and Domain concerns separate.
- **Cross-lane dependencies** are declared explicitly and enforced at ticket readiness.
- **Review flow** moves upward from Runner to Architect to Planner to Human.

The result is agentic reasoning inside deterministic structure. Agents can be creative within their scope. The workflow prevents that creativity from turning into unmanaged project drift.

## v0.3 MCP-lite Tool Surface

Railyard v0.3 adds an optional MCP-lite stdio tool surface. It is a thin wrapper over the existing helper-backed workflow contract, not a replacement workflow engine.

SQLite remains the canonical workflow state. Helper functions remain the lifecycle authority. MCP tools expose the same bounded operations that a Planner, Architect, or Runner would otherwise reach through the helper scripts:

- read and inspect tickets, epics, ticket events, and schema version
- dispatch the next Runner ticket and return the same spawn-ready payload as the Architect helper
- perform narrow lifecycle writes for ticket claim, interrupted-runner recovery, start-review, mark-runner-result, and mark-review-result
- validate result payloads and expected ticket state

The exposed tools are intentionally small and explicit:

- Read: `resolve_lane`, `get_ticket`, `list_tickets`, `next_ticket`, `get_epic`, `list_open_epics`, `next_open_epic`
- Inspect: `list_ticket_events`, `get_workflow_schema_version`
- Dispatch: `dispatch_next_runner`
- Lifecycle writes: `claim_ticket`, `recover_stale_ticket`, `start_review`, `mark_runner_result`, `mark_review_result`
- Validation: `validate_result_payload`, `validate_ticket_state`

## v0.6 Execution Profile & Failure Taxonomy

Railyard v0.6 introduces enhanced execution observability through execution profiles and a standardized failure taxonomy.

### Execution Profile Hints

Railyard agents should indicate their confidence level (`high`, `medium`, or `low`) and provide supporting evidence in new results. Profile hints (e.g., `fast`, `strong`, `local`) are advisory routing hints for dispatch adapters and are not automatic model routing. The framework does not implement automatic model selection based on profile hints.

The dispatch contract includes `profile_hints` as metadata:
- `execution_profile`: the default execution profile (e.g., `default`).
- `preferred_execution_profile`: the dispatcher's preferred execution profile when multiple options exist.
- `allowed_execution_profiles`: an optional list of profiles the dispatcher considers acceptable.
- `advisory`: always `True`, confirming that profile hints are advisory only.

`preferred_execution_profile` and `allowed_execution_profiles` enter the dispatch contract as metadata only. The dispatcher may use them for capability matching, but must not treat them as hard constraints on model choice. When no dispatch adapter consumes profile_hints, they fall back to normal Railyard Runner behavior. Profile hints never trigger automatic model routing.

### Result Confidence and Evidence

Runner results should include a structured `confidence` field and an `evidence` array documenting the file paths, command outputs, or logs that justify the stated confidence level. Missing confidence or evidence remains valid for historical results; malformed fields are rejected when present.

Runner results should also include a lightweight `runner_trace` object as recommended optional audit evidence so Architects and Planners can compare outputs across platforms consistently. It records `platform_name` when available, `agent_profile` when available, `attempts`, the exact ordered `commands` list, `blocker_category` when blocked, and `next_action` when blocked or partial. Missing `runner_trace` remains valid for backward compatibility; malformed `runner_trace` is rejected when present. This is audit evidence only; Railyard does not turn it into telemetry, token cost statistics, automatic model routing, or failure redispatch discipline.

### Failure Taxonomy

When a Runner cannot complete a task, it reports a blocker using a defined failure taxonomy: `permission_denied`, `command_failed`, `sandbox_boundary`, `authorization_required`, `environment_issue`, or `unresolved_dependency`. This ensures blockers are actionable.

## v0.6.1 Failure Handling & Redispatch Discipline

Railyard v0.6.1 clarifies how agents stop cleanly when work cannot proceed and how rejected work returns to Runner execution.

### Blocked vs Partial

`partial` means the Runner completed a useful, reviewable subset of the ticket inside the ticket scope. The result must describe what was completed, what remains, validation status, and the concrete next action. Partial is appropriate when the remaining work can be reviewed or rescoped without pretending the full ticket is complete.

`blocked` means progress cannot continue without a Human, Planner, Architect, platform, environment, secret, dependency, permission, network, or tooling action outside the Runner's authority. A blocked Runner stops instead of faking results, bypassing permission checks, broadening scope, writing alternate workflow state, or substituting unapproved tools.

Permission denial, network denial, missing secrets, and missing required tools are blockers unless the ticket explicitly provides an approved fallback. They must not be hidden as successful validation, approximated with dummy credentials, replaced with unrelated tools, or worked around through raw database writes or scratch workflow state.

Human-required blockers use a standard shape in the result notes or blocker detail:

```json
{
  "category": "authorization_required",
  "ticket_id": "TICKET-EXAMPLE-001",
  "lane": "system",
  "intended_operation": "install required dependency",
  "commands_attempted": ["python -m pip install -r requirements-mcp.txt"],
  "exact_errors": ["network access denied by sandbox"],
  "current_ticket_state": "running",
  "outbox_exists": false,
  "required_human_action": "Approve network access or provide an offline dependency bundle.",
  "recommended_next_action": "Redispatch Runner after the dependency is available."
}
```

### Reject Redispatch

`review_result=reject` routes the ticket back to `ready` for `runner`. When the platform has an execution-capable Runner path and the current session is authorized to use it, Architect redispatches the rejected ticket automatically as part of the same closed loop. Architect redispatch is not Architect implementation work.

Architect stops and records a blocker instead of redispatching when subagent spawn needs explicit Human authorization, the platform has no safe execution-capable Runner path, or the rejected ticket has reached the same-kind failure limit.

### Same-Kind Failure Limit

For the same ticket and same intended operation, agents may make at most three failed attempts of the same kind. Same-kind failures include repeated helper transition failures, repeated validation failures with the same cause, repeated permission or network denials, repeated missing-secret failures, repeated missing-tool failures, and repeated platform dispatch failures. After the third failed attempt, the workflow stops as blocked with the attempted commands, exact errors, current state, outbox existence, and recommended next action.

The three-attempt rule does not authorize direct SQLite edits, broad lifecycle resets, unapproved network or filesystem access, automatic model routing, telemetry, token cost tracking, or a heavy observability system.

### Restricted-Runner Mode

Restricted-runner mode is a platform permission fallback for environments where the Runner can edit source files but cannot safely write Control workflow state or outbox files. In this mode, the Architect owns Control lifecycle transitions and Control outbox writes. The Runner edits only allowed source files, runs validation, cleans any allowed scratch state it created, and returns exact JSON-compatible result content for the Architect to record.

### MCP-lite Validation Coverage

The v0.3 MCP-lite tool surface and probe now validate v0.6 result fields including `confidence`, `evidence`, and `protocol_reads` when they are present. A copied workflow database is used for probe validation to preserve the live database.

### Blocked Result Example

A blocked result example is provided in `examples/blocked-result-example/` demonstrating the failure taxonomy in practice.

Note: v0.6 does not implement automatic model routing. Profile hints are advisory only and fall back to normal Runner behavior when dispatch adapters are not available.

Lane-specific tools require an explicit `lane` argument. Write tools preserve the same lifecycle guardrails as the helper functions and should be run against copied workflow databases for probes or smoke tests unless the task explicitly calls for a live workflow transition.

The MCP-lite surface intentionally does not expose:

- raw SQL execution
- force reset or admin mutation
- arbitrary source-file editing
- direct ticket Markdown rewrite
- full replacement of `sync-docs` or `sync-mailbox`
- replacement of the helper lifecycle contract

## v0.7 Validation Contract Foundation

Railyard v0.7 introduces a generic, development-time-first validation contract foundation. It defines how a declared Validation Contract is applied to an artifact and produces a structured Validation Report, without embedding project-specific business rules and without adding runtime orchestration.

### Design Goals

- Generic: no encoding of a specific business domain, data schema, or content policy.
- Development-time first: the primary use case is validating Railyard artifacts (tickets, epics, results, queues) during authoring or CI.
- Read-only Validator: validation reports findings only. Architect and Planner retain all lifecycle decisions.
- Extension boundary: future external or runtime-adjacent validation is explicitly acknowledged as an extension, not implemented in this foundation ticket; later queued validation work may extend the same contract model.

### Core Concepts

The foundation defines three artifacts:

- **Validation Contract**: a declarative specification that describes what checks must be satisfied by a target artifact. It includes `contract_id`, `version`, `description`, `applies_to`, and `rules` with `rule_id`, `description`, `severity`, and `check` objects.
- **Validation Report**: the structured output produced when a Validation Contract is applied to one or more artifacts. It includes `contract_id`, `contract_version`, and `results` with per-artifact `overall_verdict` (`pass`, `fail`, `blocked`, `inconclusive`, `human_review_required`) and `findings`.
- **Validator**: the component that applies a Validation Contract to artifacts and produces a Validation Report. The Validator is read-only by default; it does not perform automatic repair, retry, remediation, or runtime orchestration.

The first application of the validation contract in v0.7 is development-time validation of Railyard artifacts. The repository ships two reference scripts:

- `scripts/validate_artifacts.py` validates Railyard artifact shape (tickets, epics, result files, queue examples, validation contracts, and validation reports) using built-in structural checks.
- `scripts/validator.py` is a minimal executable Validator for source-to-derived field-mapping checks. It reads a Validator input JSON, loads only the referenced artifacts, applies the declared field mapping contract, and emits a Validation Report JSON.

The executable Validator currently covers source artifact, derived artifact, field mapping contract, required field mappings, `identity`, `multiply_by_2`, `parse_integer`, `parse_number_preserve_sign`, `missing_mapping_policy`, and `warnings_as_errors`. It does not implement runtime orchestration, workflow lifecycle writes, automatic repair, model routing, or business-specific rules.

### Extension Boundary

The v0.7 foundation deliberately excludes business-specific rules, semantic validation across unrelated artifacts, runtime governance, automatic repair, and external artifact ingestion beyond what is strictly necessary for development-time validation. These capabilities may be implemented as new contracts and validator implementations in later work.

### Validator Protocol

The Validator protocol (input slots, output JSON shape, verdict semantics, truth hierarchy, severity/status independence, source-to-derived reconciliation patterns, and missing mapping policy) is defined in `references/validator-protocol.md`. Architect, Planner, and CI workflows can use that document as the dispatch contract for Validator invocations.

Run the development-time reference implementation with:

```powershell
python scripts\validator.py --input examples\source-derived-mapping-review\validator-input.json
python scripts\validator.py --input examples\source-derived-mapping-review\validator-input.json --output report.json
```

Planner closure and release-readiness examples are accepted as input shape examples. The minimal script reports those as `human_review_required` until a dedicated Planner-side readiness contract is implemented.

### Relationship to Runner Result

The Validation Report is distinct from the Runner result format defined in `references/result-format.md`:

- The Runner result (`runner_status: done/partial/blocked/invalid`) expresses whether a ticket's work is complete from a Runner's perspective.
- The Validation Report (`overall_verdict: pass/fail/blocked/inconclusive/human_review_required`) expresses whether one or more artifacts satisfy one or more structural or semantic checks.
- A Runner result may include a Validation Report as structured evidence. The `validation` array in the Runner result can reference validation command outputs.

## v0.7 Validator Role

The Validator is a read-only verification role that applies a Validation Contract to artifacts and produces a Validation Report. It is dispatched by two roles at different stages of the workflow: the Architect dispatches the Validator during ticket review, and the Planner dispatches the Validator before epic or release closure.

The Validator report is evidence, not lifecycle authority. It does not modify artifacts, record lifecycle transitions, close epics, or replace Architect review and Planner judgment. The Architect decides accept or reject based on the report plus scope and diff review. The Planner decides epic closure based on the report plus the full ticket state table. The Validator informs; it does not decide.

The minimum dispatch payload includes: source artifacts, candidate implementation, and relevant documentation; a validation contract or done definition derived from ticket acceptance criteria; an evidence pack with raw source values, headers, schemas, and command outputs; a risk level (low, medium, or high); a read-only command allowance listing commands the Validator is authorized to execute; and an output schema requirement mandating a single JSON object conforming to the Validation Report shape.

| Role | Nature | Can decide lifecycle? | Can modify? | Output |
|---|---|---|---|---|
| Runner | execution | no | yes, scoped source/work artifacts | Runner Result |
| Validator | read-only verification | no | no | Validation Report |
| Architect | lane owner / reviewer | yes, ticket review within lane | yes, workflow-scoped | Review Result / Runner dispatch |
| Planner | cross-lane coordinator | yes, epic closure and direction | yes, planning-level | Epic closure / direction |

### Validator Examples

Reference examples for Validator dispatch and review:

- [Architect Validator Review](examples/architect-validator-review/) - Architect dispatches the Validator during ticket review, receives a Validation Report, and maps the verdict to an accept/reject decision.
- [Source-Derived Mapping Review](examples/source-derived-mapping-review/) - Validator applies a field-mapping contract to source-to-derived artifacts and reports reconciliation findings.
- [Planner Validator Closure](examples/planner-validator-closure/) - Planner invokes the Validator before epic closure; the report serves as evidence while the Planner retains closure authority.
- [Planner Validator Release Review](examples/planner-validator-release-review/) - Planner uses the Validator for release readiness, scanning public artifacts and cross-ticket consistency.

## Architecture

```text
Human
  -> Planner
      -> System Architect
          -> System Runner(s)
      -> Domain Architect
          -> Domain Runner(s)
```

### Roles

**Human**

- Sets direction and constraints.
- Makes final decisions on scope, tradeoffs, and acceptance.
- Reviews distilled Planner summaries rather than every raw result.

**Planner**

- Collaborates with the Human on approach and feasibility.
- Breaks work into epics with clear scope boundaries.
- Declares cross-lane dependencies at planning time.
- Reviews Architect-level output before presenting summaries to the Human.

**Architect**

- Owns work within one lane.
- Breaks epics into tickets with acceptance checks.
- Decides when tickets become ready.
- Reviews Runner output against the ticket contract.

**Runner**

- Executes one ticket at a time.
- Sees only scoped ticket context and relevant references.
- Reads the required Railyard role and startup protocol before claiming or editing.
- Writes a result file and records a runner result.
- Does not manage cross-lane dependencies.

## Review Chain

Railyard is a closed workflow loop, not a one-way upward chain:

```text
Human + Planner direction
  -> Architect scoping
      -> Runner execution
          -> Architect review
              -> Human + Planner decision
```

No layer is skipped. Direction moves downward through planning and scoping; results move back upward through review and decision.

The default Architect workflow is closed-loop. If an Architect scopes or dispatches Runner work, that Architect workflow remains responsible until it reviews the Runner result and records a review outcome. `awaiting_review` is only an intermediate handoff state. It is not a completed Architect outcome unless the ticket explicitly declares an opt-in human-gated review mode or a blocker prevents review.

Human review happens above the Architect level by default. The Human reviews Architect-level summaries and decisions, not raw Runner completion as final acceptance. Human-gated review of raw Runner output is opt-in and must be declared explicitly.

Architect review requires the Railyard role, startup, and lifecycle protocol reads. A prompt can add stricter ticket rules, but it does not replace the protocol. If review rejects a ticket, the ticket returns to `ready` for Runner and the closed loop continues through Runner redispatch when the platform and current authorization allow it. Architect dispatching or spawning a Runner is allowed; personally implementing the rejected fix is not Architect work.

## Task Management

Railyard uses a SQLite-backed epic and ticket system. Mailbox files hold task and result bodies, but the database is the workflow truth.

The schema contains four lane tables plus two workflow support tables:

| Table | Purpose |
| --- | --- |
| `domain_epic` | Domain lane epics |
| `domain_ticket` | Domain lane tickets |
| `system_epic` | System lane epics |
| `system_ticket` | System lane tickets |
| `workflow_event` | Ticket lifecycle event log |
| `schema_version` | Local schema component version |

### Epics

An epic represents unresolved lane-level work. It records planning context, dependency information, priority, status, and done definition.

Valid epic statuses:

```text
queued | in_progress | partial | blocked | done | superseded
```

Epic closure is a lane-level Planner responsibility. Before marking an epic `done`, the Planner reviews completed tickets (done definition, scope coverage, cross-ticket consistency, blockers, dependencies, and follow-up needs). Architects provide closure-readiness evidence but must not close epics by default. Runners do not close epics; they may only provide evidence that an epic is ready for Planner closure. Planner or Human direction may request closure, but the lane Planner records it through the epic helper.

### Tickets

A ticket is one bounded unit of Runner work. It points to an inbox Markdown file and, after execution, to an outbox JSON result file.

Typical ticket flow:

```text
drafted -> ready -> running -> awaiting_review -> in_review -> finalised
```

`superseded` is also supported as a terminal state.

Runner result values:

```text
done | partial | blocked | invalid
```

`partial` is for reviewable in-scope work that is incomplete but honestly reported. `blocked` is for work that cannot continue without outside action, such as permission, network, missing secret, missing required tool, or unresolved dependency intervention.

Architect review result values:

```text
accept | accept_with_changes | reject | redesign
```

Review outcomes are lifecycle decisions:

```text
accept | accept_with_changes -> finalised
reject -> ready for Runner
redesign -> drafted for Architect
```

`awaiting_review` is not terminal. It means Runner execution has handed work back to the Architect. A ticket is accepted only after Architect review records `accept` or `accept_with_changes`.

Permission escalation remains a Human boundary. An Architect can dispatch Runner work, but it does not approve a spawned Runner's sandbox, filesystem, network, or destructive-operation escalation unless the Human explicitly approved that action.

Some platforms also require explicit Human authorization before subagent spawn. In that case, the Architect reports a spawn authorization blocker with the exact spawn-ready Runner prompt or dispatch command instead of stopping silently or treating the workflow as complete.

After reject, Architect redispatches Runner automatically when an execution-capable dispatch path is available, the session is authorized to use it, and the same-kind failure limit has not been reached. Otherwise the Architect records a blocker with the rejected ticket id, reason, current state, and exact next dispatch prompt or command.

## Workflow State Boundary

Railyard uses one authoritative workflow database per project:

- **Authoritative workflow DB**: `.workflow/workflow.db` is the single source of truth for tickets, epics, claims, reviews, and lifecycle transitions.
- **Disposable validation DBs**: Smoke tests and MCP-lite probes may copy the workflow database to a temporary directory and run against that copy.

**Agents must never:**
- Write scratch files, copied databases, or probe state inside `.workflow/`.
- Treat a copied validation database as authoritative workflow state.
- Copy generated ticket, epic, or outbox files into documentation directories unless the ticket explicitly asks for documentation fixtures.

## Cross-Lane Dependencies

System and Domain lanes can run in parallel, but dependencies between them are inevitable. Railyard handles them with explicit ticket readiness rather than lane-wide blocking.

The Planner declares dependencies during epic planning. Architects monitor the dependency state within their lane. Runners only see tickets that are ready to execute.

The key design choice is: declare dependencies early, enforce them late. This keeps constraints visible to the Planner and Architects while keeping Runner execution narrow and clean.

## Handoff Protocol

Agents do not share a global context. Each role receives a scoped handoff:

- **Runner handoff**: role definition, ticket specification, acceptance checks, relevant references.
- **Architect dispatch**: lane-level ticket selection plus a spawn-ready Runner prompt from `scripts/architect.py`.
- **Architect handoff**: lane-level epic state, ticket statuses, review queue, dependency status.
- **Planner handoff**: both lanes' epic-level status, global constraints, Human decisions, architecture rules.

Context size increases as responsibility increases. No agent receives everything.

## Platform Dispatch Compatibility

Railyard Runner is a workflow role, not a portable platform `agent_type`.

Agent platforms name and expose their execution surfaces differently. Claude Code, Gemini CLI, GitHub Copilot, VS Code, Windsurf, Cursor, JetBrains, and Codex-like environments do not share one standard subagent type list. Railyard therefore treats platform-specific agent names as adapter details.

Dispatchers should select a safe execution-capable platform surface in this order:

1. documented or discovered platform-native execution agent, such as `general-purpose`, `generalist`, `Agent`, `Code`, or the current platform's explicit implementation agent
2. Railyard fallback profile, such as `railyard-runner`, when platform-native selection is missing, ambiguous, or unsafe and the platform supports custom or prompt-defined agents
3. documented implicit default execution path when the platform clearly marks it execution-capable
4. fail fast with a clear unsupported-dispatch error

Selection is capability-based, not name-based. A Runner requires read, write, execute, scoped file edit, and result JSON capabilities. Read-only and planning agents must not be used for implementation tickets. A dispatched Runner prompt must always state the Railyard workflow role, lane, ticket scope, helper authority, required startup reads, validation commands, result JSON contract, and blocker reporting requirements.

Runner startup reads are part of the dispatch contract, not optional background reading. The default dispatch payload includes:

```text
railyard/SKILL.md
railyard/references/roles.md
railyard/references/startup-sequence.md
```

Runner result JSON should include non-empty `protocol_reads` evidence for new work. If a prompt, adapter, or custom handoff omits role protocol reads, the omission becomes visible through dispatch validation and review evidence, but historical results without `protocol_reads` remain valid.

Initialized projects include VS Code / GitHub Copilot-compatible default profiles in `.github/agents/`:

- `railyard-architect`
- `railyard-runner`
- `railyard-explorer`
- `railyard-reviewer`

See `references/platform-dispatch.md` for the platform mapping, capability profile contract, and shared-workspace rules.

## Repository Contents

```text
.
|-- agents/
|   `-- openai.yaml              # Agent metadata
|-- assets/
|   `-- skeleton/                # Project seed copied into target workspaces
|       `-- .github/agents/      # Default platform agent profiles
|-- examples/
|   `-- mcp-lite-smoke/          # Minimal MCP-lite lifecycle example
|-- references/                  # Detailed workflow contracts
|-- scripts/                     # SQLite-backed helper commands
|-- .gitignore                   # Excludes local and generated files
|-- SKILL.md                     # Agent-facing operating instructions
`-- README.md                    # GitHub project homepage
```

Optional MCP-lite files:

```text
requirements-mcp.txt             # Optional MCP server dependency
scripts/railyard_mcp_server.py   # Thin FastMCP stdio wrapper
scripts/probe_railyard_mcp_server.py
                                  # Temp-DB probe for MCP-lite smoke validation
```

The skeleton creates this project-local workflow surface:

```text
docs/
|-- domain/
|   |-- epics/
|   |-- inbox/
|   `-- outbox/
|-- system/
|   |-- epics/
|   |-- inbox/
|   `-- outbox/
`-- templates/
```

## Quick Start

Railyard has no third-party runtime dependency. It uses Python and the standard-library SQLite module.

The optional MCP-lite server requires the dependency listed in `requirements-mcp.txt`.

Requirements:

- Python 3.10 or newer
- A project directory where workflow state should be created

From a target project, copy or clone this repository into a subdirectory such as `railyard/`, then initialize the workflow:

```powershell
python railyard/scripts/init_workflow.py --project-root .
```

This creates:

- `.workflow/workflow.db`
- `.github/agents/`
- `docs/domain/epics/`
- `docs/domain/inbox/`
- `docs/domain/outbox/`
- `docs/system/epics/`
- `docs/system/inbox/`
- `docs/system/outbox/`
- `docs/templates/`

If you are running commands from the Railyard repository root itself, use:

```powershell
python scripts/init_workflow.py --project-root .
```

For the full role-based startup sequence, including first epics, ticket execution, review, and E2E smoke checks, read `references/startup-sequence.md`.

## Common Commands

Initialize or verify the schema:

```powershell
python scripts/workflow_schema.py ensure --db .workflow/workflow.db
python scripts/workflow_schema.py tables --db .workflow/workflow.db
```

Sync epic Markdown files into SQLite:

```powershell
python scripts/epic.py --lane domain --project-root . sync-docs
python scripts/epic.py --lane system --project-root . sync-docs
```

List or inspect unresolved epics:

```powershell
python scripts/epic.py --lane domain list-open
python scripts/epic.py --lane system next-open
python scripts/epic.py --lane domain show --epic-id DOMAIN-E001
```

Sync ticket inbox files into SQLite:

```powershell
python scripts/ticket.py --lane domain --project-root . sync-mailbox
python scripts/ticket.py --lane system --project-root . sync-mailbox
```

Draft a ticket from the helper:

```powershell
python scripts/ticket.py --lane domain draft --epic-id DOMAIN-E001 --title "Define scope" --task "Write docs/scope.md."
```

Dispatch the next Runner ticket from the Architect helper:

```powershell
python scripts/architect.py --lane domain --runner-name domain-runner-1 dispatch-next-runner
```

Claim and complete a Runner ticket:

```powershell
python scripts/ticket.py --lane domain next --actor runner
python scripts/ticket.py --lane domain claim --ticket-id DOMAIN-001 --actor runner --claimed-by runner-1
python scripts/ticket.py --lane domain mark-runner-result --ticket-id DOMAIN-001 --runner-result done --outbox-path docs/domain/outbox/DOMAIN-001.result.json
```

Review a completed ticket:

```powershell
python scripts/ticket.py --lane domain next --actor architect
python scripts/ticket.py --lane domain start-review --ticket-id DOMAIN-001 --claimed-by architect-1
python scripts/ticket.py --lane domain mark-review-result --ticket-id DOMAIN-001 --review-result accept
```

Recover an interrupted Runner ticket that is stuck in `running` before an outbox result was written:

```powershell
python scripts/ticket.py --lane system list --status running --next-actor runner
python scripts/ticket.py --lane system recover-stale --ticket-id SYSTEM-DEMO-001 --actor runner --reason "runner interrupted before outbox"
```

If the outbox result JSON already exists, use `mark-runner-result` instead.

If the same ticket and intended operation fails three times during recovery, dispatch, claim, result marking, review, validation, or permission-gated work, stop and record a blocker instead of trying more commands.

Route a workflow target to its helper command and mailbox paths:

```powershell
python scripts/route_workflow_target.py --lane domain --role architect --epic-id DOMAIN-E001
python scripts/route_workflow_target.py --lane system --role runner --ticket-id SYSTEM-DEMO-001
```

Bootstrap epics from a JSON queue:

```powershell
python scripts/bootstrap_epics.py --lane domain --input queue.json
```

Run the optional MCP-lite server:

```powershell
python scripts/railyard_mcp_server.py --db .workflow/workflow.db --project-root .
```

Probe the MCP-lite surface against a temporary database copy:

```powershell
python scripts/probe_railyard_mcp_server.py --db .workflow/workflow.db --project-root .
```

## Examples

The `examples/` directory contains small, public-safe walkthroughs:

- `examples/mcp-lite-smoke/` shows a disposable MCP-lite workflow that reads and dispatches a ready ticket, claims it as a Runner, validates and records the result, then completes Architect review.

## Validation And Release Discipline

Railyard includes deterministic validation and release hygiene surfaces for maintainers:

- `scripts/validate_artifacts.py` validates workflow artifact shapes and example queue files.
- `.github/workflows/railyard-validate.yml` runs compile, artifact validation, and MCP-lite smoke checks in GitHub Actions.
- `CHANGELOG.md` records versioned release notes for public-facing changes.

When a release adds or changes user-visible capabilities, update both `CHANGELOG.md` and this README. The changelog explains what changed in that release; the README explains what Railyard can do now.

## File Formats

Epic documents live in:

```text
docs/domain/epics/
docs/system/epics/
```

Ticket inbox documents live in:

```text
docs/domain/inbox/
docs/system/inbox/
```

Runner result files live in:

```text
docs/domain/outbox/
docs/system/outbox/
```

Template files are included under `assets/skeleton/docs/templates/` and are copied into target projects during initialization.

## Design Principles

- **The Human stays in the loop.** Automation reduces what the Human must watch; it does not remove Human judgment.
- **Agentic reasoning, deterministic structure.** Agents reason inside fixed workflow boundaries.
- **Separate planning from execution.** Global awareness belongs to the Planner and Architects, not every Runner.
- **State lives in the database.** SQLite is the source of truth for task state; conversations are ephemeral.
- **Declare dependencies early, enforce them late.** Cross-lane dependencies are planned at the epic level and enforced at ticket readiness.
- **Runners should not see blocked work.** If a ticket is not ready, it should not be offered to a Runner.
- **Review closes the loop.** Work starts from Human and Planner direction, moves through Architect scoping and Runner execution, then returns through Architect review to Human and Planner decision.
- **Handoffs are protocol.** Each role receives a defined context shape.
- **Roles are defined, not hardcoded.** The core roles are Human, Planner, Architect, and Runner; projects may extend the model.

## Scope and Limitations

- This is a reference implementation, not a maintained library.
- It assumes Human ownership of final workflow accountability. Fully autonomous operation is not a design goal.
- It does not include LLM API calls, agent spawning code, or hosted orchestration.
- The SQLite schema is intentionally simple and intended to be adapted.
- There is no automated permission system beyond helper-script behavior and the workflow contract.
- The optional MCP-lite server is a thin adapter over helper-backed workflow operations. It is not a raw database console, release manager, source editor, or replacement for the helper lifecycle contract.

## References

Read these files for the detailed operating contract:

- `references/model.md`
- `references/startup-sequence.md`
- `references/roles.md`
- `references/routing.md`
- `references/platform-dispatch.md`
- `references/lifecycle.md`
- `references/sql-contract.md`
- `references/epic-format.md`
- `references/ticket-format.md`
- `references/result-format.md`
- `references/helper-commands.md`

## Maintenance Checklist

Before publishing or tagging a new version:

- Keep generated workflow databases out of Git.
- Run the helper commands from a clean checkout to verify the examples still match the scripts.
- Run the E2E smoke check described in `references/startup-sequence.md`.
- Update `CHANGELOG.md` with versioned release details.
- Update `README.md` when user-visible capabilities, examples, validation surfaces, or workflow boundaries change.

## License

Apache License 2.0. See `LICENSE`.
