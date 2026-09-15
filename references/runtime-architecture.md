---
name: runtime-architecture
description: Canonical runtime architecture contract -- identity, relationships, state boundaries, and ownership for run, stage, runtime artifact, gate, adapter, event, retry, resume, and intervention
type: contract
version: 0.8.0
governing_contract:
  artifact_id: knowledge-contract
  artifact_kind: contract
  artifact_version: 0.8.0
  locator: references/knowledge-contract.md
risk_level: high
validator_required: true
---

# Runtime Architecture Contract

This contract defines the canonical identity, relationships, state boundaries, and ownership of runtime concepts. It is a contract for future runtime producers and consumers; it does not implement runtime state storage, event infrastructure, adapter frameworks, gate engines, Validator coordination, or runtime orchestration.

## 1. Scope and Separation

### 1.1 Development Workflow State vs Runtime State

Railyard maintains two strictly separated state domains:

| Domain | Authority | State examples | Managed by |
|---|---|---|---|
| **Development workflow state** | `references/lifecycle.md` | ticket status, epic status, claim state, review state, next_actor | Lifecycle helpers, workflow database |
| **Runtime state** | This contract | run state, stage progress, gate decisions, event log, retry count, resume checkpoint | Runtime components (future) |

These domains are **independent** and **mutually non-authoritative**. Runtime state does not depend on workflow state for existence, and workflow state does not depend on runtime state for transitions. The domains are connected only through typed `ArtifactRef` bridges.

Runtime components MUST NOT write to the workflow database or bypass lifecycle helpers. Lifecycle helpers MUST NOT write runtime state.

### 1.2 Run Independence

A Run represents one bounded execution attempt. A Run MAY be triggered by:

- A ticket claim (through the workflow lifecycle)
- A CI pipeline invocation
- An external pipeline or automation
- A local script execution
- An API or programmatic call

When a Run is triggered by a ticket, the ticket serves as optional provenance recorded in `run_provenance`. A Run triggered without a ticket MUST still produce typed `ArtifactRef` provenance linking to its triggering source. Ticket/Epic identity is never a required existence condition for a Run.

### 1.3 Contract Hierarchy

This contract is a consumer of the frozen Knowledge Contract (`references/knowledge-contract.md` v0.8.0). It preserves all typed fields, `ArtifactRef` structures, provenance, evidence, relationships, visibility, and lifecycle event streams as required by Knowledge Contract Sec.9.1 and Sec.9.2. This contract does not modify, reinterpret, or override the Knowledge Contract.

### 1.4 Non-Goals

This contract does **not** implement or prescribe implementation of:

- Runtime state storage, persistence engine, or database schema
- Event sidecar, event bus, message broker, or streaming infrastructure
- Adapter framework, plugin system, provider registry, or dynamic dispatch
- Gate engine, rule evaluator, decision executor, or policy enforcement
- Validator mesh, distributed validation, or multi-node coordination
- Runtime orchestration, workflow engine, or state machine executor
- Proprietary service integration or commercial-only correctness paths

---

## 2. Canonical Runtime Entities

### 2.1 Run

A `Run` is the canonical unit of runtime execution. It represents one bounded execution attempt with typed provenance.

| Field | Type | Required | Contract |
|---|---|---|---|
| `run_id` | string | Yes | Stable, non-empty, globally unique identifier. |
| `run_provenance` | object | Yes | Typed provenance for this run (see below). |
| `trigger` | string | Yes | `ticket`, `ci_pipeline`, `external_pipeline`, `local_script`, or `api`. |
| `executor_identity` | string | Yes | Identity of the executor. Required for every Run regardless of trigger type. |
| `run_ordinal` | integer | Yes | Monotonically increasing ordinal for the same provenance scope. |
| `status` | string | Yes | One of `pending`, `active`, `completed`, `failed`, `blocked`, `interrupted`. |
| `created_at` | string | Yes | ISO 8601 timestamp of run creation. |
| `started_at` | string | No | ISO 8601 timestamp when execution began. |
| `completed_at` | string | No | ISO 8601 timestamp when execution ended (any terminal status). |
| `stage_graph` | StageGraph | Yes | Contract-declared stage graph for this run. |
| `events` | array of RuntimeEvent | Yes | Append-only runtime event log for this run. |
| `runner_trace` | object | No | Lightweight audit record as defined in `references/result-format.md`. |

**Run provenance** (`run_provenance`):

| Field | Type | Required | Contract |
|---|---|---|---|
| `origin_artifact` | ArtifactRef | Yes | The artifact that triggered this run. MUST be a portable ArtifactRef (ticket ID, pipeline config locator, script artifact, or request artifact), not a machine-local path or raw API endpoint. `artifact_kind` MUST be `ticket`, `pipeline_config`, `script`, or `request_artifact`. |
| `origin_epic` | ArtifactRef | No | Authorizing Epic when applicable. `artifact_kind` MUST be `epic`. |
| `governing_contracts` | array of ArtifactRef | Yes | Governing contracts for this run. |
| `additional_sources` | array of ArtifactRef | No | Other source artifacts. |

**Lifecycle rule:** Status transitions are append-only. A run's status history is preserved in its `events` array. Terminal statuses are `completed`, `failed`, `blocked`.

**Ownership:** The triggering source owns the run's authorization. The executor owns the run's execution. The Architect reviews when the trigger is a ticket; other triggers may assign review to different roles.

### 2.2 Stage and StageGraph

A `StageGraph` is a contract-declared directed graph of stages. It replaces a hardcoded linear pipeline with an explicit contract.

| Field | Type | Required | Contract |
|---|---|---|---|
| `graph_id` | string | Yes | Stable identifier for this stage graph contract. |
| `stages` | array of Stage | Yes | All stages in the graph. |
| `edges` | array of StageEdge | Yes | Directed edges between stages. |
| `entry_stages` | array of string | Yes | `stage_id` values that are valid entry points. |
| `terminal_stages` | array of string | Yes | `stage_id` values whose completion terminates the run. |

A `StageEdge` is:

| Field | Type | Required | Contract |
|---|---|---|---|
| `from` | string | Yes | Source `stage_id`. |
| `to` | string | Yes | Target `stage_id`. |
| `condition` | string | No | `always`, `on_pass`, `on_fail`, `on_skip`. Default: `always`. |

A `Stage` is a named, bounded phase within a run.

| Field | Type | Required | Contract |
|---|---|---|---|
| `stage_id` | string | Yes | Stable, non-empty identifier unique within the graph. |
| `name` | string | Yes | Human-readable stage name. |
| `required` | boolean | Yes | If `true`, this stage MUST NOT be silently skipped. |
| `status` | string | Yes | One of `pending`, `active`, `completed`, `failed`, `skipped`. |
| `started_at` | string | No | ISO 8601 timestamp. |
| `completed_at` | string | No | ISO 8601 timestamp. |
| `gates` | array of Gate | No | Quality gates evaluated during this stage. |
| `artifacts_produced` | array of ArtifactRef | No | Runtime artifacts produced during this stage. |

**Required stage rule (no skip):** A stage with `required: true` MUST NOT be skipped under any circumstances. When a required stage cannot be completed:
- The run enters `blocked` status and triggers more-evidence collection.
- If the blocking condition is a contract gap, the Architect may authorize `contract redesign` as a lifecycle action, which creates a new run with a revised StageGraph. The original run is terminated as `failed`.
- The Human may provide missing evidence, approve contract redesign, or authorize run termination.
- Under no condition may a required stage be skipped, treated as completed, or have its failure recast as pass.

A stage with `required: false` (optional stage) MAY be skipped through explicit Human intervention recorded as a `run.intervention` event with `intervention_type=skip_stage` and a non-empty `reason`.

**Ownership:** The StageGraph is declared in the run contract before execution begins. The StageGraph is authorized by the governing runtime contract or the trigger policy declared in the run's `run_provenance.governing_contracts`. The run executor MUST follow the declared graph; it MUST NOT dynamically rewrite the graph during execution. A formal signature schema for StageGraph authorization is not defined in this contract and remains a future extension.

### 2.3 Runtime Artifact

A `RuntimeArtifact` is any artifact produced or consumed during runtime execution. It is always referenced via `ArtifactRef` (Knowledge Contract Sec.1.1).

Every RuntimeArtifact MUST carry explicit `visibility`. The visibility value is derived from the most restrictive of:

1. The visibility declared by each source artifact that contributed to this artifact
2. The project policy for the artifact's scope
3. Any explicit visibility constraint in the governing contract

Visibility MUST be recorded at artifact creation time and MUST NOT be silently downgraded, broadened, or reinterpreted by downstream consumers. Per Knowledge Contract Sec.3 and Sec.6, accepted visibility is frozen. A runtime artifact that aggregates content from sources with different visibilities MUST use the most restrictive visibility among its sources.

Canonical runtime artifact kinds (extending Knowledge Contract Sec.1.1):

| Kind | Description | Producer |
|---|---|---|
| `runner_result` | Runner outbox result JSON | Runner |
| `validation_report` | Validator output JSON | Validator |
| `gate_decision` | Result of a gate evaluation | Gate consumer |
| `runtime_event_log` | Append-only runtime event stream | Runtime event infrastructure |
| `stage_output` | Intermediate output from a single stage | Stage executor |
| `resume_checkpoint` | Saved state for resumption | Runtime infrastructure |

**Provenance:** Every RuntimeArtifact MUST record:
- `origin_run`: `run_id` of the producing run
- `origin_stage`: `stage_id` of the producing stage (if applicable)
- `produced_by`: executor identity
- `source_artifacts`: array of `ArtifactRef` to contributing source artifacts
- `visibility`: the resolved visibility value

### 2.4 Gate and Gate Decision

A `Gate` is a quality checkpoint within a stage. It defines a contract that must be evaluated. The evaluation produces a `GateDecision`.

**Gate** (what must be checked):

| Field | Type | Required | Contract |
|---|---|---|---|
| `gate_id` | string | Yes | Stable identifier unique within the stage. |
| `gate_type` | string | Yes | `validator`, `artifact_shape`, `diff_review`, `custom`. |
| `required` | boolean | Yes | If `true`, this gate MUST NOT be skipped or treated as pass without evidence. |
| `contract_ref` | ArtifactRef | Conditional | Validation contract to apply. Required for `validator` gate type. |
| `failure_behavior` | string | Yes | `halt_stage`, `halt_run`, `warn`, `require_intervention`. |

**GateDecision** (the result of evaluation):

| Field | Type | Required | Contract |
|---|---|---|---|
| `decision_id` | string | Yes | Stable, globally unique identifier. |
| `gate_id` | string | Yes | The gate that was evaluated. |
| `outcome` | string | Yes | `pass`, `fail`, `blocked`, `inconclusive`, `human_review_required`. Independent of execution mode. |
| `execution_mode` | string | Yes | `full`, `degraded_transport`, `degraded_storage`. Describes how evaluation was executed. |
| `evidence` | array of ArtifactRef | Yes | Evidence supporting this decision. Always required. |
| `evaluated_at` | string | Yes | ISO 8601 timestamp. |
| `evaluated_by` | string | Yes | Identity of the evaluating component or role. |
| `degradation_note` | string | Conditional | Required when `execution_mode` is not `full`. Describes what was degraded and what was preserved. |

**Outcome and execution mode independence:** `outcome` and `execution_mode` are independent dimensions. Degradation of transport or storage (`execution_mode=degraded_transport` or `degraded_storage`) MUST NOT change the `outcome`. A gate that would produce `outcome=fail` under `execution_mode=full` MUST produce `outcome=fail` under any degraded mode. Degradation may affect how evidence is collected and transported; it MUST NOT alter the correctness verdict.

**Required gate rule (non-degradable correctness):** A gate with `required: true` MUST NOT be skipped, overridden, or treated as pass without evidence. When a required gate cannot be evaluated with full correctness:

- Transport or storage MAY be degraded (`execution_mode=degraded_transport` or `degraded_storage`). Degradation affects how evidence is collected and transported; it MUST NOT alter the `outcome`.
- The correctness contract MUST NOT be degraded. If correctness evidence cannot be obtained, the gate MUST produce `outcome=blocked` or `outcome=inconclusive`.
- `outcome=blocked` triggers more-evidence collection. `outcome=inconclusive` triggers Human review.
- A required gate MUST NOT produce `outcome=pass` under any degradation path without evidence.

When a required gate is `blocked` or `inconclusive`, resolution paths are:
- **More evidence**: The executor or Architect provides missing evidence and the gate is re-evaluated.
- **Contract redesign**: The Architect determines the gate contract is insufficient. The run is terminated as `failed`, the contract is revised, and a new run is created with the revised StageGraph.
- **Terminate run**: The Human or Architect terminates the run as `failed` or `blocked`.

Under no condition may a Human, Architect, or any role rewrite a required gate's `outcome` from `fail`/`blocked`/`inconclusive` to `pass`. Human may only provide evidence, approve contract redesign, or terminate.

**Optional gate override:** A gate with `required: false` (advisory/non-required gate) MAY be overridden through `override_gate` intervention ONLY when the governing contract explicitly declares `allow_gate_override: true` for that `gate_id`. Override creates a new `GateDecision` that references the original decision and records the override reason. Override MUST NOT modify the original GateDecision or any referenced Validation Report.

**Runner inline Validator prohibition:** A Runner MUST NOT self-evaluate a `validator` gate. The Validator role is independent and read-only (Knowledge Contract Sec.7, `references/roles.md`). Runner validation scripts are development-time checks, not independent Validator evidence. When a `validator` gate cannot dispatch an independent Validator, the gate produces `outcome=blocked` and the run requires more-evidence.

**Bare execution prohibition:** There is no "bare execution without gates" mode. Every run has a declared StageGraph. If a stage declares required gates, those gates MUST be evaluated with evidence. The absence of a gate engine does not authorize skipping required gates; the run cannot proceed (`outcome=blocked`) until the gate engine is available.

### 2.5 Adapter and Provider Interface

An `Adapter` is a typed bridge between a runtime concept and an external system, platform, or provider. It implements a declared `ProviderInterface`.

**ProviderInterface** (what an adapter must satisfy):

| Field | Type | Required | Contract |
|---|---|---|---|
| `interface_id` | string | Yes | Stable identifier for this interface contract. |
| `interface_version` | string | Yes | Version of the interface contract. |
| `capabilities` | array of string | Yes | Declared capabilities (e.g., `read`, `write`, `execute`, `validate`, `store`, `publish`). |
| `input_schema` | object | Yes | Schema for adapter input. |
| `output_schema` | object | Yes | Schema for adapter output. |
| `error_schema` | object | Yes | Schema for adapter errors. |

**Adapter** (a concrete implementation):

| Field | Type | Required | Contract |
|---|---|---|---|
| `adapter_id` | string | Yes | Stable identifier. |
| `implements` | string | Yes | `interface_id` that this adapter satisfies. |
| `provider` | string | Yes | Provider identifier (e.g., `local_filesystem`, `sqlite`, `postgres`, `custom`). |
| `capability_degradation` | object | Yes | Declared degradation behavior per capability. |

**Capability degradation contract:** Every adapter MUST declare, per capability, whether it supports:
- `full`: full capability with correctness guarantees.
- `degraded_transport`: transport/dispatch/storage mechanism is reduced but correctness is preserved.
- `degraded_scope`: reduced scope of the capability but correctness is preserved within that scope.
- `unavailable`: capability is not available. Any gate depending on this capability for correctness MUST return `blocked`.

The architecture is **provider-neutral** and **storage-neutral**. This contract does not mandate Python, SQLite, any specific database, any specific transport, or any specific execution environment. Conformance is measured by the declared ProviderInterface and the evidence a provider produces, not by implementation language or storage engine.

**Non-goal:** This contract defines adapter identity, interface contract, and degradation contract. It does not implement adapter discovery, lifecycle, configuration, dynamic loading, or provider registry.

### 2.6 Runtime Event

A `RuntimeEvent` is an append-only occurrence within a run's lifecycle. It is distinct from Knowledge lifecycle events (Knowledge Contract Sec.5) but follows the same structural principles.

| Field | Type | Required | Contract |
|---|---|---|---|
| `event_id` | string | Yes | Globally unique event identity. |
| `event_type` | string | Yes | Runtime event type (see taxonomy below). |
| `schema_version` | string | Yes | Event schema version. |
| `occurred_at` | string | Yes | ISO 8601 timestamp. |
| `event_order` | integer | Yes | Positive, monotonically increasing within the run. |
| `run_id` | string | Yes | Owning run identity. |
| `stage_id` | string | No | Stage in which the event occurred. |
| `actor_role` | string | Yes | `runner`, `architect`, `validator`, `planner`, or `human`. |
| `trigger_artifact` | ArtifactRef | Yes | Artifact that triggered this event. |
| `prior_state` | object | Yes | Runtime state projection before the event. |
| `next_state` | object | Yes | Runtime state projection after the event. |
| `reason` | string | Yes | Non-empty factual reason. |
| `recommended_action` | string | Yes | `none`, `retry`, `resume`, `intervention_required`, `more_evidence`, or `escalate`. |

**Runtime event type taxonomy:**

| event_type | Trigger | Consumer action |
|---|---|---|
| `run.created` | Trigger source initiates run | Initialize run state |
| `run.started` | Executor begins execution | Record start timestamp |
| `run.stage.started` | Stage begins | Initialize stage state |
| `run.stage.completed` | Stage finishes successfully | Record stage completion |
| `run.stage.failed` | Stage encounters unrecoverable error | Evaluate retry/resume |
| `run.stage.skipped` | Optional stage skipped by intervention | Record skip reason and authorizing event |
| `run.gate.evaluated` | Gate check produces GateDecision | Record decision reference |
| `run.gate.blocked` | Required gate cannot be evaluated | Trigger more-evidence or contract redesign |
| `run.gate.overridden` | Optional gate overridden (contract allows) | Record new GateDecision, preserve original |
| `run.retry.initiated` | Retry decision recorded | Increment retry count, create new run |
| `run.resumed` | Interrupted run resumed | Restore from checkpoint |
| `run.redesign` | Contract redesign authorized | Terminate current run, trigger new run |
| `run.intervention` | External action applied | Record intervention details |
| `run.terminated` | Run terminated by authority | Terminal state |
| `run.completed` | All terminal stages finished successfully | Terminal state |
| `run.failed` | Unrecoverable failure | Terminal state |
| `run.blocked` | External dependency blocks progress | Terminal state pending resolution |
| `run.interrupted` | Executor session lost before completion | Awaiting resume |

**Replay rule:** Runtime events are append-only. Replaying the ordered event set for a run MUST produce the same state projection. Events are never updated or deleted.

### 2.7 Retry

`Retry` is the deterministic re-execution of a failed run. It is a bounded, role-authorized action.

| Field | Type | Required | Contract |
|---|---|---|---|
| `retry_decision` | object | Yes | Who authorized, when, and why. |
| `retry_decision.authorized_by` | string | Yes | `architect`, `human`, or `system` (for deterministic auto-retry). |
| `retry_decision.reason` | string | Yes | Factual reason for retry. |
| `retry_decision.authorized_at` | string | Yes | ISO 8601 timestamp. |
| `max_retries` | integer | Yes | Maximum retry count for the same provenance scope (default: 3). |
| `current_retry_count` | integer | Yes | Retries attempted so far (0 for initial run). |
| `retry_strategy` | string | Yes | `full` (restart from beginning) or `resume` (continue from last checkpoint). |
| `parent_run_id` | string | Yes | The run that is being retried. |
| `failure_category` | string | Yes | Failure taxonomy from `references/lifecycle.md`. |

**Bounds:** Retry MUST respect the same-kind failure limit of 3 (from `references/lifecycle.md` Retry Stop Rule). After the third same-kind failure, retry is blocked and requires Human intervention.

**Auto-retry (deterministic only):** A system may auto-retry only when `retry_strategy=full`, `failure_category=command_failed`, the error is transient and deterministic, and the current retry count is below `max_retries`. All other retries require Architect or Human authorization.

**Non-goal:** This contract defines retry identity, bounds, and authorization. It does not implement retry scheduling, exponential backoff, circuit breaking, or dead-letter queues.

### 2.8 Resume

`Resume` is the continuation of an interrupted run from its last known checkpoint.

| Field | Type | Required | Contract |
|---|---|---|---|
| `resume_run_id` | string | Yes | The new run that continues the interrupted one. |
| `interrupted_run_id` | string | Yes | The run that was interrupted. |
| `checkpoint` | object | Yes | Last known good state. |
| `checkpoint.stage_id` | string | Yes | Last completed stage. |
| `checkpoint.event_order` | integer | Yes | Last event order in the interrupted run. |
| `checkpoint.artifacts` | array of ArtifactRef | Yes | Artifacts successfully produced up to the checkpoint. |
| `recovery_action` | string | Yes | `replay_from_checkpoint` or `restart_stage`. |
| `authorized_by` | string | Yes | `architect` or `human`. |

**Resume vs Retry:** Retry restarts a failed run; Resume continues an interrupted run. A run is `interrupted` when the executor session was lost, the environment terminated, or an external signal stopped execution, but the work completed so far is valid.

**Checkpoint requirement:** A run MUST record a checkpoint at every stage boundary. Without a checkpoint, resume is not possible and the run must be retried from the beginning (`retry_strategy=full`).

**Non-goal:** This contract defines resume identity and checkpoint contract. It does not implement checkpoint serialization, state snapshot, or process migration.

### 2.9 Intervention

`Intervention` is an external action that modifies a running or blocked run's state without destroying its provenance.

| Field | Type | Required | Contract |
|---|---|---|---|
| `intervention_id` | string | Yes | Stable, globally unique identifier. |
| `run_id` | string | Yes | The run being intervened upon. |
| `intervention_type` | string | Yes | `skip_stage`, `override_gate`, `force_retry`, `force_terminal`, `provide_evidence`. |
| `authorized_by` | string | Yes | `architect` or `human`. `system` is not authorized to intervene. |
| `reason` | string | Yes | Non-empty factual reason. |
| `occurred_at` | string | Yes | ISO 8601 timestamp. |
| `prior_state` | object | Yes | Runtime state before intervention. |
| `next_state` | object | Yes | Runtime state after intervention. |
| `evidence` | array of ArtifactRef | No | Evidence supporting the intervention. |

**Rules:**

- Intervention is always recorded as a `run.intervention` event.
- Intervention MUST NOT destroy or rewrite prior runtime events. It appends a new event.
- Intervention MUST NOT change the `run_id` or run provenance of the run.

**Skip stage:** `skip_stage` is permitted ONLY for a stage with `required: false` (optional stage). It requires Human authorization and records the skip reason as a `run.stage.skipped` event. A required stage (`required: true`) MUST NOT be skipped by any intervention; the only resolution paths are blocked/more-evidence, contract redesign (new run), or termination.

**Override gate:** `override_gate` is permitted ONLY for a gate with `required: false` (advisory/non-required gate) AND only when the governing contract explicitly declares `allow_gate_override: true` for that `gate_id`. Override creates a new `GateDecision` that references the original decision and records the override reason as an audit event. Override MUST NOT modify the original GateDecision or any referenced Validation Report. A required gate (`required: true`) MUST NOT be overridden by any intervention; its `outcome` is final.

**Required gate override prohibition:** A required `validator` gate's `outcome` of `fail`, `blocked`, or `inconclusive` MUST NOT be rewritten to `pass` by any role, including Human. The Validation Report that a required validator gate references is immutable evidence and keeps its original `overall_verdict`. Intervention MUST NOT produce a substitute pass decision for a required gate.

**Provide evidence:** `provide_evidence` supplies missing evidence without overriding gate logic. The gate is re-evaluated with the new evidence. This is the only intervention that may change a gate's `outcome`, and only because the underlying evidence has changed, not because the verdict was overridden.

**Force retry / terminate:** `force_retry` and `force_terminal` are available for any run regardless of stage or gate requirement. They require Architect or Human authorization.

**Non-goal:** This contract defines intervention identity and rules. It does not implement an intervention API, UI, or automated intervention decision logic.

---

## 3. Validator, Gate, and Decision Separation

### 3.1 Three Distinct Concerns

The runtime architecture separates three concerns that are often conflated:

| Concern | Produces | Owned by | Modifies state? | Decides lifecycle? |
|---|---|---|---|---|
| **Validator** | Validation Report | Validator role (read-only) | No | No |
| **Gate consumer** | Gate Decision | Gate engine | No | No |
| **Lifecycle authority** | Review result / closure | Architect / Planner | Yes | Yes |

### 3.2 Validator

The Validator applies a Validation Contract to artifacts and produces a Validation Report. Per `references/validator-protocol.md`:

- Read-only: inspects artifacts, produces reports.
- Does NOT modify artifacts, create tickets, close epics, or write workflow state.
- Does NOT perform remediation, auto-fix, retry, or automated repair.
- Does NOT decide acceptance or closure.

A Validation Report is immutable evidence. Once produced, it MUST NOT be modified by any role, including Human intervention. For a required `validator` gate, the Validation Report's `overall_verdict` is final; intervention MUST NOT produce a substitute pass decision. The report's `overall_verdict` of `fail`, `blocked`, `inconclusive`, or `human_review_required` cannot be rewritten to `pass` by any means.

### 3.3 Gate Consumer

The Gate consumer (gate engine) receives a Validation Report (for `validator` gate type) or other evidence, and produces a Gate Decision. The Gate Decision:

- Evaluates the gate contract against the available evidence.
- Produces an `outcome` of `pass`, `fail`, `blocked`, `inconclusive`, or `human_review_required` based on the evidence and contract.
- Records an `execution_mode` of `full`, `degraded_transport`, or `degraded_storage` describing how evaluation was executed.
- `execution_mode` degradation MUST NOT change the `outcome`.
- Does NOT modify the Validation Report.
- Does NOT decide lifecycle; it informs the lifecycle authority.

### 3.4 Lifecycle Authority

The Architect (per-ticket) or Planner (per-epic) consumes Gate Decisions as evidence and makes lifecycle decisions: accept, reject, redesign, close, block. The lifecycle authority is NEVER delegated to a Validator or Gate consumer.

### 3.5 Runner and Validator Boundary

A Runner MUST NOT produce independent Validator evidence for its own work. Runner validation scripts, smoke tests, and artifact shape checks are development-time evidence, not independent Validator reports. Per `references/roles.md`, the Validator is a separate role with read-only boundaries. The Runner is prohibited from Knowledge materialization, classification, extraction, or curation (Knowledge Contract Sec.7).

---

## 4. Relationships

### 4.1 Entity Relationship Diagram

```
Trigger (ticket, CI, pipeline, script, API)
  |
  +---> Run (1:N, ordered by run_ordinal)
          |
          +---> StageGraph (1:1, contract-declared)
          |       |
          |       +---> Stage (1:N, graph nodes)
          |       |       |
          |       |       +---> Gate (0:N)
          |       |       |       |
          |       |       |       +---> GateDecision (1:1 per evaluation)
          |       |       |
          |       |       +---> RuntimeArtifact (0:N, via ArtifactRef)
          |       |
          |       +---> StageEdge (directed edges between stages)
          |
          +---> RuntimeEvent (0:N, append-only)
          +---> Retry (0:N, each creates a new Run)
          +---> Resume (0:1, creates a new Run from checkpoint)
          +---> Intervention (0:N, append-only)
```

### 4.2 Cross-Domain References

All cross-domain references (runtime -> workflow, runtime -> knowledge) MUST use `ArtifactRef` per Knowledge Contract Sec.1.1. Scalar identifiers, machine-local paths, or opaque strings are prohibited. References between runtime entities within the same run MAY use internal identifiers (`run_id`, `stage_id`, `gate_id`) since these are scoped and resolvable within the runtime domain.

### 4.3 Relationship Rules

| Source | Relationship | Target | Rule |
|---|---|---|---|
| Run | `triggered_by` | Trigger source | Every run has exactly one trigger source. |
| Run | `governed_by` | Contract (ArtifactRef) | Every run declares governing contracts. |
| Run | `contains` | StageGraph | Every run has exactly one stage graph. |
| Stage | `evaluates` | Gate | A stage may evaluate zero or more gates. |
| Gate | `produces` | GateDecision | Each evaluation produces one decision. |
| GateDecision | `references` | ValidationReport | For `validator` gates, the decision references the report. |
| Stage | `produces` | RuntimeArtifact | A stage may produce artifacts. |
| Retry | `creates` | Run | Each retry creates exactly one new run. |
| Resume | `continues` | Run | Each resume creates exactly one new run. |
| Intervention | `modifies` | Run | Multiple interventions may apply to one run. |
| Intervention | `overrides` | Gate | Gate override creates new GateDecision, does not modify original. |
| RuntimeArtifact | `derived_from` | Source artifacts | Every artifact records its source provenance. |

---

## 5. State Boundaries and Ownership

### 5.1 Role Ownership Matrix

| Action | Runner | Architect | Validator | Gate Consumer | Human |
|---|---|---|---|---|---|
| Create run | Executes | Dispatches | -- | -- | -- |
| Execute stage | Executes | -- | -- | -- | -- |
| Produce Validation Report | -- | -- | Produces | -- | -- |
| Produce Gate Decision | -- | -- | -- | Produces | -- |
| Record runtime event | Produces | Produces | Produces | Produces | -- |
| Authorize retry | -- | Authorizes | -- | -- | Authorizes |
| Authorize resume | -- | Authorizes | -- | -- | Authorizes |
| Provide evidence | Provides | Provides | -- | -- | Provides |
| Approve contract redesign | -- | Authorizes | -- | -- | Authorizes |
| Terminate run | -- | Authorizes | -- | -- | Authorizes |
| Override optional gate (contract allows) | -- | Authorizes | -- | -- | Authorizes |
| Skip optional stage | -- | -- | -- | -- | Authorizes |
| Override required gate | Prohibited | Prohibited | Prohibited | Prohibited | Prohibited |
| Skip required stage | Prohibited | Prohibited | Prohibited | Prohibited | Prohibited |
| Produce runtime artifact | Produces | -- | -- | -- | -- |
| Materialize Knowledge | Prohibited | Prohibited | Prohibited | Prohibited | -- |
| Decide lifecycle | -- | Decides (ticket) | -- | -- | Escalates |

### 5.2 State Write Boundaries

| State domain | Writable by |
|---|---|
| Ticket workflow state | Lifecycle helpers only |
| Epic workflow state | Lifecycle helpers only |
| Run state | Runtime infrastructure only |
| Stage state | Runtime infrastructure only |
| Gate Decision | Gate consumer only |
| Validation Report | Validator only (immutable after production) |
| Runtime event log | Append-only, any authorized producer |
| Knowledge entries | Knowledge Curator only (Knowledge Contract Sec.7) |

### 5.3 Transition Authority

| Transition | Authorized by | Event type |
|---|---|---|
| `pending` -> `active` | Executor (claim/start) | `run.started` |
| Stage `pending` -> `active` | Executor (stage entry) | `run.stage.started` |
| Stage `active` -> `completed` | Executor (stage done) | `run.stage.completed` |
| Stage `active` -> `failed` | Runtime (error) | `run.stage.failed` |
| Stage `pending` -> `skipped` | Human (intervention, optional stage only) | `run.stage.skipped` |
| `active` -> `completed` | Runtime (terminal stages done) | `run.completed` |
| `active` -> `failed` | Runtime (unrecoverable) | `run.failed` |
| `active` -> `blocked` | Executor or gate | `run.blocked` |
| `active` -> `interrupted` | Runtime (session loss) | `run.interrupted` |
| `interrupted` -> (new run) | Architect/Human (resume) | `run.resumed` |
| `failed` -> (new run) | Architect/Human (retry) | `run.retry.initiated` |

---

## 6. Knowledge Contract Compliance

### 6.1 Preserved Typed Fields

Per Knowledge Contract Sec.9.1, runtime components MUST preserve:

| Contract source | Runtime preservation |
|---|---|
| `entry_id`, `entry_type`, `level`, `visibility`, `version`, `immutable` | Preserved as typed scalar fields |
| `provenance` (all sub-fields) | Preserved as typed `ArtifactRef` objects |
| `evidence` | Preserved as `array<ArtifactRef>`, never strings |
| `evidence_notes` | Preserved as `array<string>`, separate from evidence |
| `relationships` | Preserved with discriminator and typed target branch |
| Lifecycle events | Preserved with all Sec.5 fields, ordering, and causation |
| Lifecycle projections | Derived from ordered events; never written back to accepted entries |

### 6.2 ArtifactRef Compliance

All cross-domain references MUST use `ArtifactRef` with required `artifact_id` and `artifact_kind`. Optional `artifact_version`, `locator`, and `digest` fields are preserved when present. Per Knowledge Contract Sec.1.1, resolvers match identity first and reject ambiguous references.

### 6.3 Provenance

Runtime artifacts reference Knowledge entry provenance through typed `ArtifactRef` chains. Runtime artifacts do not create new Knowledge provenance; they reference existing provenance from their source artifacts and governing contracts. When a runtime artifact records `run_provenance`, it uses the same typed structure (origin artifact, governing contracts, additional sources) as Knowledge provenance without claiming to be a Knowledge producer.

### 6.4 Visibility

Per Knowledge Contract Sec.3, Sec.6, and Sec.9.3, visibility is frozen for every accepted entry. RuntimeArtifact visibility is resolved at creation time from source artifacts and project policy using the most restrictive applicable value. Runtime components MUST NOT silently omit, broaden, downgrade, or reinterpret visibility. Visibility is carried as an explicit typed field on every RuntimeArtifact, not inferred from a ticket.

### 6.5 Invalidation Hooks

Per Knowledge Contract Sec.9.1, runtime components MUST use `trigger_artifact: ArtifactRef`, `affected_entry_ids`, and `propagation_chain` from Knowledge lifecycle events for invalidation routing. When a Knowledge entry is invalidated:

1. The runtime identifies all runs that consumed the invalidated entry via `affected_entry_ids`.
2. It traces `propagation_chain` to find indirectly affected entries.
3. It marks affected runtime artifacts with an invalidation notice referencing the lifecycle event.
4. It records a `run.intervention` event if a Gate Decision depended on the invalidated entry.

Invalidation is a signal for review, not an automatic state change.

### 6.6 Functionality Relationships

Per Knowledge Contract Sec.4, runtime components preserve relationship discriminators (`target_kind: knowledge_entry` or `target_kind: runtime_artifact`) and their respective target branches. The `implemented_by`, `verified_by`, and `introduced_by` relationships link Knowledge entries to runtime artifacts.

---

## 7. Execution Boundaries

### 7.1 Provider and Storage Neutrality

This architecture contract is provider-neutral and storage-neutral. It does not mandate:

- A specific programming language (Python, Go, Rust, etc.)
- A specific database or storage engine (SQLite, PostgreSQL, filesystem, etc.)
- A specific transport or communication protocol
- A specific execution environment or operating system

Conformance is measured by evidence: a provider MUST satisfy the declared `ProviderInterface` and produce the typed artifacts this contract requires. Implementation language, storage engine, and transport are provider choices.

### 7.2 OSS Correctness

Correctness of the architecture MUST be achievable with open-source tooling only. No proprietary service, commercial API, or paid tooling is required for any correctness path defined in this contract. Optional proprietary providers MAY exist behind an Adapter with a declared degradation path, but they MUST NOT be the only path to a correct Gate Decision.

### 7.3 Deterministic Degradation

When a runtime component is unavailable, the system degrades deterministically. Degradation levels:

| Level | What degrades | What is preserved | Trigger |
|---|---|---|---|
| **Full** | Nothing | All capabilities | All components available |
| **Transport-degraded** | Transport, dispatch, storage mechanism | Correctness contract, evidence quality | Adapter capability marked `degraded_transport` |
| **Scope-degraded** | Reduced capability scope | Correctness within reduced scope | Adapter capability marked `degraded_scope` |
| **Blocked** | Execution cannot proceed | No false correctness claims | Required capability is `unavailable` |

A run MUST NOT silently transition from `Blocked` to `completed`. Resolution requires more-evidence, Human intervention, or capability restoration.

### 7.4 Exportable Evidence

Runtime evidence MUST be exportable and independently verifiable:

| Evidence type | Format requirement | Verifiable by |
|---|---|---|
| `runner_result` | Typed JSON (per `references/result-format.md`) | Architect, Validator |
| `validation_report` | Typed JSON (per `references/validator-protocol.md` Sec.3) | Architect, Planner, Gate consumer |
| `gate_decision` | Typed JSON (per Sec.2.4) | Architect, Planner |
| `runtime_event_log` | Typed JSON array (per Sec.2.6) | Replay tool, Auditor |
| `runner_trace` | Typed JSON (per `references/result-format.md`) | Architect |

All evidence formats are self-describing (typed fields, not opaque blobs). Evidence MUST be readable with standard tools. No proprietary format or binary blob is acceptable as the sole evidence format.

---

## 8. Consumer Crosswalk

The following crosswalk defines the contract surface that each downstream component MUST satisfy. These component names are public and do not expose internal Epic or Ticket identifiers.

### 8.1 Runtime State and Event Sidecar

**Concern:** Runtime state persistence and append-only event recording and replay.

**Contract surface:**
- Run state lifecycle (Sec.2.1)
- Stage and StageGraph state (Sec.2.2)
- RuntimeEvent schema and replay (Sec.2.6)
- State write boundaries (Sec.5.2)
- Transition authority (Sec.5.3)

**Must preserve:**
- `ArtifactRef` as typed objects, never scalar or opaque references (Sec.6.2)
- Visibility as explicit typed field on RuntimeArtifacts (Sec.6.4)
- Provenance references from source artifacts (Sec.6.3)
- Append-only event ordering with strict monotonic `event_order`
- Full `prior_state` and `next_state` for replay auditability

**Must not:**
- Modify, write to, or bypass the workflow database (Sec.1.1, Sec.5.2)
- Materialize, classify, extract, or curate Knowledge (Knowledge Contract Sec.7)
- Update or delete recorded events (append-only)
- Require proprietary storage or commercial services (Sec.7.1, Sec.7.2)

### 8.2 Adapter and Provider Interface

**Concern:** Typed bridges between runtime concepts and external systems with declared degradation behavior.

**Contract surface:**
- ProviderInterface definition (Sec.2.5)
- Adapter identity and capability declaration (Sec.2.5)
- Capability degradation contract per capability (Sec.2.5)
- Deterministic degradation levels (Sec.7.3)

**Must preserve:**
- Every adapter MUST declare a ProviderInterface it implements
- Every capability MUST declare its degradation behavior (`full`, `degraded_transport`, `degraded_scope`, `unavailable`)
- `unavailable` on a correctness-critical capability MUST cause dependent gates to return `blocked`
- Provider-neutral: no mandatory language, database, or transport

**Must not:**
- Hard-depend on any proprietary service for correctness (Sec.7.2)
- Provide a `pass` path that skips required evidence
- Implement dynamic plugin loading, hot-reload, or service discovery

### 8.3 Gate Decision

**Concern:** Gate evaluation, GateDecision production, and failure behavior routing.

**Contract surface:**
- Gate identity and required flag (Sec.2.4)
- GateDecision schema (Sec.2.4)
- Required gate non-degradable correctness rule (Sec.2.4)
- Validator/Gate/Lifecycle separation (Sec.3)
- Runner inline Validator prohibition (Sec.2.4, Sec.3.5)

**Must preserve:**
- `validator` gate type consumes Validator Protocol via `contract_ref`
- Gate Decisions are evidence, not lifecycle decisions
- Required gate MUST NOT silently skip or pass without evidence
- Gate override creates new GateDecision, never modifies Validator Report

**Must not:**
- Modify artifacts, create tickets, close epics, or write workflow state
- Implement runtime orchestration (deciding what to do next)
- Require proprietary rule engines or commercial policy evaluators
- Allow Runner self-evaluation of `validator` gates

### 8.4 Validator Mesh and Final Publish Gate

**Concern:** Multi-Validator coordination, parallel validation dispatch, aggregated reporting, and final publish gate.

**Contract surface:**
- Validator as read-only report producer (Sec.3.2)
- Gate consumer as decision producer (Sec.3.3)
- Validation Report immutability (Sec.3.2)
- Truth hierarchy from Validator Protocol Sec.5
- Confidence and Human escalation matrix (`references/validator-protocol.md` Sec.11)

**Must preserve:**
- Each Validator is independent and read-only
- Aggregated verdict follows severity/status independence
- Candidate output must never be the truth source
- Validation Reports are immutable; Gate Decisions are separate artifacts
- A failed or inconclusive mesh result does not authorize automatic pass

**Must not:**
- Implement distributed consensus, leader election, or multi-node coordination
- Allow one Validator's output to serve as truth source for another without independent evidence
- Require proprietary orchestration, service mesh, or commercial coordination services
- Modify any Validation Report after production

### 8.5 OSS Examples, Smoke Tests, Documentation, Release Readiness

**Concern:** Public examples, end-to-end smoke tests, documentation, and release readiness evidence that exercise the runtime architecture without proprietary dependencies.

**Contract surface:**
- Run independence: examples demonstrate ticket-triggered AND non-ticket-triggered runs (Sec.1.2)
- StageGraph: examples demonstrate graph-based flow with required stages (Sec.2.2)
- Required gate: smoke tests exercise blocked/more-evidence path when gate is unavailable (Sec.2.4)
- Visibility: examples demonstrate explicit visibility on all artifacts (Sec.2.3, Sec.6.4)
- Adapter degradation: smoke tests exercise each degradation level (Sec.7.3)
- Exportable evidence: examples produce all 5 evidence types in typed JSON (Sec.7.4)

**Must preserve:**
- All examples run with OSS tooling only (Sec.7.2)
- No internal Epic/Ticket IDs in public documentation
- Public hygiene: ASCII-only text, no secrets, no machine-local paths

**Must not:**
- Require proprietary services, paid APIs, or commercial tooling
- Use internal project identifiers in public-facing content
- Ship incomplete or gated examples that require non-public setup

---

## 9. Public Hygiene

### 9.1 Visibility Contract

This contract is `visibility: public`. All examples, schemas, and cross-references use public-safe values. No secrets, credentials, tokens, machine-local paths, proprietary configuration, or internal Epic/Ticket identifiers appear in this document.

### 9.2 OSS Boundary

All concepts, schemas, and contracts defined here are implementable with open-source tooling. No concept requires a commercial license, proprietary service, or paid API for correctness. The architecture is provider-neutral and storage-neutral (Sec.7.1).

### 9.3 Related References

| Reference | Relationship |
|---|---|
| `references/knowledge-contract.md` | Governing contract (frozen v0.8.0); this contract is a downstream consumer |
| `references/lifecycle.md` | Development workflow lifecycle; runtime state is independent |
| `references/roles.md` | Role ownership boundaries; runtime respects all role constraints |
| `references/validator-protocol.md` | Validator protocol consumed by `validator` gate type |
| `references/validation-contract.md` | Validation contract foundation |
| `references/result-format.md` | Runner result format; primary runtime artifact schema |
| `references/platform-dispatch.md` | Platform dispatch mapping |

---

## 10. Version and Governance

| Field | Value |
|---|---|
| Contract version | 0.8.0 |
| Governing contract | `references/knowledge-contract.md` v0.8.0 (frozen) |
| Risk level | `high` |
| Validator required | `true` |
| Visibility | `public` |
