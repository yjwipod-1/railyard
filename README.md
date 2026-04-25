# Railyard

Agentic workflow with deterministic guardrails.

Railyard is a portable operating scaffold for long-running AI-agent projects. It separates planning, lane ownership, execution, review, and task state so that human-led agent work can survive across sessions, projects, and role boundaries.

This repository is a reference implementation extracted from real project use. It is not an agent runtime, a hosted service, or a Python package. It supplies the workflow structure, SQLite schema, templates, and helper scripts that an agent project can vendor into its own workspace.

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

The result is agentic reasoning inside deterministic structure. Agents can be creative within their scope. The workflow prevents that creativity from turning into uncontrolled project drift.

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
- Controls when tickets become ready.
- Reviews Runner output against the ticket contract.

**Runner**

- Executes one ticket at a time.
- Sees only scoped ticket context and relevant references.
- Writes a result file and records a runner result.
- Does not manage cross-lane dependencies.

## Review Chain

Railyard is a closed control loop, not a one-way upward chain:

```text
Human + Planner direction
  -> Architect scoping
      -> Runner execution
          -> Architect review
              -> Human + Planner decision
```

No layer is skipped. Direction moves downward through planning and scoping; results move back upward through review and decision.

## Task Management

Railyard uses a SQLite-backed epic and ticket system. Mailbox files hold task and result bodies, but the database is the control-plane truth.

The schema contains four tables:

| Table | Purpose |
| --- | --- |
| `domain_epic` | Domain lane epics |
| `domain_ticket` | Domain lane tickets |
| `system_epic` | System lane epics |
| `system_ticket` | System lane tickets |

### Epics

An epic represents unresolved lane-level work. It records planning context, dependency information, priority, status, and done definition.

Valid epic statuses:

```text
queued | in_progress | partial | blocked | done | superseded
```

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

Architect review result values:

```text
accept | accept_with_changes | reject | redesign
```

## Cross-Lane Dependencies

System and Domain lanes can run in parallel, but dependencies between them are inevitable. Railyard handles them with explicit ticket readiness rather than lane-wide blocking.

The Planner declares dependencies during epic planning. Architects monitor the dependency state within their lane. Runners only see tickets that are ready to execute.

The key design choice is: declare dependencies early, enforce them late. This keeps constraints visible to the Planner and Architects while keeping Runner execution narrow and clean.

## Handoff Protocol

Agents do not share a global context. Each role receives a scoped handoff:

- **Runner handoff**: role definition, ticket specification, acceptance checks, relevant references.
- **Architect handoff**: lane-level epic state, ticket statuses, review queue, dependency status.
- **Planner handoff**: both lanes' epic-level status, global constraints, Human decisions, architecture rules.

Context size increases as responsibility increases. No agent receives everything.

## Repository Contents

```text
.
|-- agents/
|   `-- openai.yaml              # Agent metadata
|-- assets/
|   `-- skeleton/                # Project seed copied into target workspaces
|-- references/                  # Detailed workflow contracts
|-- scripts/                     # SQLite-backed helper commands
|-- .gitignore                   # Excludes local and generated files
|-- SKILL.md                     # Agent-facing operating instructions
`-- README.md                    # GitHub project homepage
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

Requirements:

- Python 3.10 or newer
- A project directory where workflow state should be created

From a target project, vendor or clone this repository into a subdirectory such as `railyard/`, then initialize the workflow:

```powershell
python railyard/scripts/init_workflow.py --project-root .
```

This creates:

- `.workflow/workflow.db`
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

Claim and complete a Runner ticket:

```powershell
python scripts/ticket.py --lane domain next --actor runner
python scripts/ticket.py --lane domain claim --ticket-id DOMAIN-001 --actor runner --claimed-by codex
python scripts/ticket.py --lane domain mark-runner-result --ticket-id DOMAIN-001 --runner-result done
```

Review a completed ticket:

```powershell
python scripts/ticket.py --lane domain next --actor architect
python scripts/ticket.py --lane domain claim --ticket-id DOMAIN-001 --actor architect --claimed-by codex
python scripts/ticket.py --lane domain mark-review-result --ticket-id DOMAIN-001 --review-result accept
```

Resolve the correct control surface before acting:

```powershell
python scripts/resolve_control_surface.py --lane domain --role architect --epic-id DOMAIN-E001
python scripts/resolve_control_surface.py --lane system --role runner --ticket-id SYSTEM-001
```

Bootstrap epics from a JSON queue:

```powershell
python scripts/bootstrap_epics.py --lane domain --input queue.json
```

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
- **Separate control from execution.** Global awareness belongs to the Planner and Architects, not every Runner.
- **State lives in the database.** SQLite is the source of truth for task state; conversations are ephemeral.
- **Declare dependencies early, enforce them late.** Cross-lane dependencies are planned at the epic level and enforced at ticket readiness.
- **Runners should not see blocked work.** If a ticket is not ready, it should not be offered to a Runner.
- **Review closes the loop.** Work starts from Human and Planner direction, moves through Architect scoping and Runner execution, then returns through Architect review to Human and Planner decision.
- **Handoffs are protocol.** Each role receives a defined context shape.
- **Roles are defined, not hardcoded.** The core roles are Human, Planner, Architect, and Runner; projects may extend the model.

## Scope and Limitations

- This is a reference implementation, not a maintained library.
- It assumes Human ownership of the control loop. Fully autonomous operation is not a design goal.
- It does not include LLM API calls, agent spawning code, or hosted orchestration.
- The SQLite schema is intentionally simple and intended to be adapted.
- There is no automated access control beyond helper-script behavior and the workflow contract.

## References

Read these files for the detailed operating contract:

- `references/model.md`
- `references/startup-sequence.md`
- `references/roles.md`
- `references/routing.md`
- `references/lifecycle.md`
- `references/sql-contract.md`
- `references/epic-format.md`
- `references/ticket-format.md`
- `references/result-format.md`
- `references/helper-commands.md`

## Maintenance Checklist

Before publishing or tagging a new version:

- Keep generated workflow databases out of version control.
- Run the helper commands from a clean checkout to verify the examples still match the scripts.
- Run the E2E smoke check described in `references/startup-sequence.md`.

## License

Apache License 2.0. See `LICENSE`.
