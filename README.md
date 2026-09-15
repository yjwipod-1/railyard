# Railyard

Agentic workflow with deterministic guardrails.

[Chinese quick start](README.zh-CN.md) | [Changelog](CHANGELOG.md) | [Operating model](references/model.md)

Railyard is a portable workflow scaffold for long-running AI-agent projects. It separates planning, lane ownership, execution, independent validation, review, and durable task state so work can continue across sessions without relying on chat history.

Railyard is a reference implementation, not a hosted agent runtime, managed service, or Python package. It provides:

- a role-based operating protocol for Human, Planner, Architect, Runner, and Validator work;
- SQLite-backed epic, ticket, claim, result, and review state;
- helper scripts, schemas, templates, and optional MCP-lite workflow access;
- deterministic local runtime components for state, evidence, gates, action policy, and validator aggregation;
- governed contracts, conformance fixtures, smoke scenarios, and validation commands.

## Quick Start

Railyard requires Python 3.10 or later. The workflow helpers and runtime components use the Python standard library. The validation route has two direct dependencies listed in `requirements-test.txt`.

### Install

Clone Railyard into your project as a `railyard/` subdirectory:

```bash
git clone https://github.com/yjwipod-1/railyard.git railyard
```

Initialize the workflow surface from your project root:

```shell
python railyard/scripts/init_workflow.py --project-root .
```

Initialization creates the local workflow database, authority record, default agent profiles, mailbox directories, and document templates.

```text
railyard/.workflow/workflow.db      Local SQLite workflow state
railyard/.railyard-workflow.json    Resolved workflow authority record
.github/agents/                      Default agent profiles
docs/domain/                         Domain-lane epics, inbox, and outbox
docs/system/                         System-lane epics, inbox, and outbox
docs/templates/                      Project document templates
```

The database and authority record are local state. They are ignored by Git and must not be published with the Railyard source.

### Start a Planner

Use an existing planning conversation or open a fresh session:

```text
Use this session as the Planner for my project.
Read railyard/SKILL.md and railyard/references/roles.md.
Convert our current project direction into Railyard epics and tickets.
Then give me the smallest Architect startup prompt for the next ticket or epic.
```

### Start an Architect

The Planner normally supplies the smallest startup prompt. For a manual start:

```text
Read railyard/SKILL.md, railyard/references/roles.md,
railyard/references/startup-sequence.md, and railyard/references/lifecycle.md.
role=architect
Work on <epic_id or ticket_id>.
Dispatch the Runner if your platform supports subagents.
If not, return the exact Runner startup prompt.
```

### Start a Runner

Use a fresh Runner session for one ready ticket:

```text
Read railyard/SKILL.md.
role=runner
ticket_id=<ticket_id>
Stay inside the ticket scope, run the required validation,
and return the Runner result.
```

### Start a Validator

Validator is an independent, read-only role. The Architect or Planner dispatches it only when the governing ticket or closure contract requires it.

```text
Read railyard/SKILL.md and railyard/references/validator-protocol.md.
role=validator
Apply the validation contract in <contract_path> to <artifact_paths>.
Return a Validation Report JSON. Do not modify artifacts or lifecycle state.
```

For the complete startup and handoff rules, read [references/startup-sequence.md](references/startup-sequence.md).

## Why Railyard

Long-running agent work fails when task state lives only in conversation history, every agent receives the whole project, or execution and review share the same authority. Railyard addresses those failure modes directly:

- **Durable state, disposable sessions:** workflow state survives in SQLite while conversations can start fresh at role boundaries.
- **Bounded execution:** lanes, tickets, and explicit references keep Runners inside a reviewable scope.
- **Independent validation:** Validator evidence is separate from Runner self-checks and Architect lifecycle decisions.
- **Platform-neutral operation:** the protocol does not depend on one model, agent product, or hosted orchestrator.
- **Deterministic control:** helpers, schemas, and contracts constrain lifecycle and runtime behavior around agent reasoning.

## Workflow Model

Railyard keeps conversations disposable and workflow state durable.

```text
Human
  |
  v
Planner ---------> Validator
  |                   ^
  v                   |
Architect ------------+
  |
  v
Runner

Results return: Runner -> Architect -> Planner -> Human
```

| Role | Primary responsibility | Lifecycle authority | Artifact access |
|---|---|---|---|
| Human | Direction and final decisions | Final project authority | Delegates work |
| Planner | Epics and cross-lane coordination | Epic closure | Planning artifacts |
| Architect | Ticket scope, dispatch, and review | Ticket review | Workflow-scoped artifacts |
| Runner | Execute one ready ticket | None | Ticket-scoped edits |
| Validator | Independent verification | None | Read-only evidence |

### Lanes

Railyard separates two concerns:

- **System lane**: tooling, storage, schemas, integrations, automation, and platform mechanics.
- **Domain lane**: product logic, analysis, content generation, validation rules, and delivery semantics.

Each lane has its own Architect and Runner queue. The Planner coordinates dependencies between lanes without giving every execution session the full project context.

### Durable State

The SQLite database is the lifecycle source of truth. It records epics, tickets, dependencies, claims, Runner results, reviews, and event history. Sessions discover it through `.railyard-workflow.json` and pass the resolved path explicitly to helper commands.

Project mailbox documents complement the database:

```text
docs/domain/epics/       Domain epic definitions
docs/domain/inbox/       Domain ticket definitions
docs/domain/outbox/      Domain Runner results
docs/system/epics/       System epic definitions
docs/system/inbox/       System ticket definitions
docs/system/outbox/      System Runner results
```

These project artifacts belong to the consuming project. They are not part of the reusable Railyard dependency source.

### Workflow State and Runtime State

The workflow protocol governs development work. The runtime components provide a separate deterministic state and evidence domain for executing and evaluating runs.

| State domain | Authority | Contains |
|---|---|---|
| Development workflow | Lifecycle helpers and the workflow database | Epics, tickets, claims, review state, and next actor |
| Runtime execution | Runtime components and the runtime event journal | Runs, stages, events, gate decisions, retries, and checkpoints |

These domains are independent and mutually non-authoritative. Runtime components do not use the workflow database as runtime state, and lifecycle helpers do not write the runtime journal. Cross-domain relationships use typed `ArtifactRef` values rather than shared mutable state.

## Runtime v0.8

The local v0.8 runtime is a set of deterministic, importable Python components. It does not provide hosted orchestration or a background service.

| Component | Responsibility | Canonical reference |
|---|---|---|
| Runtime state and evidence | Append-only events, reduction, replay, projection, visibility, snapshots, and export | [Architecture](references/runtime-architecture.md), [State](references/runtime-state-contract.md), [Visibility](references/runtime-artifact-visibility-contract.md), [Export](references/runtime-evidence-export-contract.md) |
| Runtime adapter | Provider-neutral request, capability, delegation, and error boundary | [Runtime Adapter Contract](references/runtime-adapter-contract.md) |
| Gate and action policy | Structured gate evaluation and bounded action selection without action execution | [Gate Decision Contract](references/runtime-gate-decision-contract.md), [Action Policy Contract](references/runtime-action-policy-contract.md) |
| Validator Mesh | Independent report dispatch, aggregation, freshness, confidence, conflict handling, and gate publication | [Validator Mesh Contract](references/runtime-validator-mesh-contract.md) |
| Smoke runner | Public integration scenarios across state, validation, gates, policy, and evidence | [Smoke Quick Start](examples/runtime_v080_smoke/README.md), [Smoke Contract](references/runtime-v080-smoke-contract.md) |

The implementation is under `scripts/runtime_*.py`. Contracts, schemas, and conformance fixtures define its public boundaries independently of the implementation.

## Validate v0.8 Locally

Run the supported validation route from the repository root. Supply a smoke workspace outside the source checkout.

PowerShell:

```powershell
$smokeRoot = Join-Path $env:TEMP "Railyard v0.8 smoke"
python -m pip install -r requirements-test.txt
python -m compileall -q scripts
python scripts/validate_artifacts.py --project-root .
python scripts/test_runtime_v080_ci.py
python scripts/runtime_v080_regression.py
python scripts/runtime_v080_smoke.py --tmp-dir $smokeRoot --all run
```

POSIX shell:

```bash
smoke_root="${TMPDIR:-/tmp}/railyard v0.8 smoke"
python -m pip install -r requirements-test.txt
python -m compileall -q scripts
python scripts/validate_artifacts.py --project-root .
python scripts/test_runtime_v080_ci.py
python scripts/runtime_v080_regression.py
python scripts/runtime_v080_smoke.py --tmp-dir "$smoke_root" --all run
```

The repository includes `.github/workflows/railyard-validate.yml` for the same public route on Windows and Linux with Python 3.10 through 3.14. The workflow configuration is part of the v0.8 candidate; hosted results are established only after an authorized branch push executes the workflow.

The smoke catalog contains 20 scenarios: 11 normal, tamper, recovery, and visibility paths plus 9 expected typed non-pass Validator Mesh outcomes. A conforming run reports `total=20`, `passed=20`, `failed=0`; an expected typed non-pass verdict is scenario data, not a smoke failure.

## Governance

Governance documents are classified by kind and authority. The machine-readable inventory is the canonical discovery surface.

- [Governance Document Taxonomy](references/governance-document-taxonomy.md)
- [Governance Document Inventory (JSON)](references/governance-document-inventory.json)
- [Governance Document Inventory (Markdown)](references/governance-document-inventory.md)
- [Governance Read Routing](references/governance-read-routing.json)

Resolve the canonical reads for a role:

```powershell
python scripts/governance_read_router.py --role architect
```

The resolver returns an ordered `normative_reads` list or a blocked result when governance configuration is invalid. This README is a non-normative Guide; Protocol, Policy, Contract, Schema, and Registry artifacts listed in the inventory take precedence.

## Optional MCP-lite Surface

Railyard includes an optional stdio MCP-lite adapter over the helper-backed workflow contract. It exposes bounded read, dispatch, lifecycle, and validation operations without replacing SQLite or helper authority.

Install the optional dependencies only when MCP-lite is needed:

```powershell
python -m pip install -r requirements-mcp.txt
python scripts/probe_railyard_mcp_server.py --project-root .
```

The MCP-lite server is not a raw database console, source editor, release manager, or hosted orchestration service.

## Repository Layout

```text
SKILL.md                  Agent-facing operating entry point
scripts/                  Workflow helpers, runtime components, and tests
references/               Normative protocols, policies, contracts, and guides
assets/schemas/           JSON Schemas
assets/skeleton/          Files copied into consuming projects
examples/                 Conformance fixtures and smoke catalogs
.github/workflows/        Public validation workflow
CHANGELOG.md              Versioned release history
```

## Documentation

Use the governance router for role-specific reads. These documents are useful starting points:

- [Operating Model](references/model.md)
- [Roles](references/roles.md)
- [Lifecycle](references/lifecycle.md)
- [Startup Sequence](references/startup-sequence.md)
- [Routing](references/routing.md)
- [Platform Dispatch](references/platform-dispatch.md)
- [Helper Commands](references/helper-commands.md)
- [Epic Format](references/epic-format.md)
- [Ticket Format](references/ticket-format.md)
- [Result Format](references/result-format.md)
- [Validation Contract](references/validation-contract.md)
- [Validator Protocol](references/validator-protocol.md)
- [Knowledge Contract](references/knowledge-contract.md)
- [Changelog](CHANGELOG.md)

## Scope and Limitations

Railyard intentionally does not include:

- LLM API calls or automatic model selection;
- built-in agent spawning or hosted orchestration;
- a scheduler or background service;
- proprietary provider integration;
- Knowledge extraction or storage;
- a vector database or RAG implementation;
- automatic commit, push, tag, release, or deployment;
- a general permission system beyond helper behavior and the workflow contract.

The Human remains accountable for project direction, cross-lane decisions, publication, and external side effects. Agents reason within explicit scopes; deterministic helpers and contracts constrain lifecycle and runtime behavior.

## License

Apache License 2.0. See [LICENSE](LICENSE).
