---
name: runtime-adapter-contract
description: Canonical Runtime Adapter Contract -- external request/response boundary for provider bridges to the runtime state sidecar
type: contract
version: 1.0.0
governing_contract:
  artifact_id: runtime-architecture
  artifact_kind: contract
  artifact_version: 0.8.0
  locator: references/runtime-architecture.md
risk_level: high
validator_required: true
---

# Runtime Adapter Contract

This contract defines the canonical external request/response boundary for provider bridges to the Railyard runtime state sidecar. It freezes the public types, operations, error taxonomy, capability states, and authority boundaries before any schema or implementation work. Adapters implement this contract; they do not redefine runtime semantics, gate decisions, or workflow authority.

## 1. Public Discriminated Types

### 1.1 ProviderInterface

A `ProviderInterface` declares what an adapter must satisfy.

| Field | Type | Required | Contract |
|---|---|---|---|
| `interface_id` | string | Yes | Stable identifier for this interface contract. |
| `interface_version` | string | Yes | Semantic version of the interface contract. |
| `capabilities` | array of string | Yes | Declared capabilities: `create_run`, `append_event`, `read_event`, `read_events`, `get_run`, `get_stage`, `evidence_snapshot`, `export_evidence`. |
| `input_schema_ref` | string | Yes | Reference to the request schema defined by this contract. |
| `output_schema_ref` | string | Yes | Reference to the response schema defined by this contract. |
| `error_schema_ref` | string | Yes | Reference to the error schema defined by this contract. |

### 1.2 AdapterDescriptor

An `AdapterDescriptor` identifies a concrete adapter implementation.

| Field | Type | Required | Contract |
|---|---|---|---|
| `adapter_id` | string | Yes | Stable, globally unique identifier. |
| `implements` | string | Yes | `interface_id` that this adapter satisfies (must resolve to a registered ProviderInterface). |
| `provider` | string | Yes | Provider identifier (e.g., `local_sqlite`, `postgres`, `custom`). |
| `protocol_version` | string | Yes | Adapter-supported protocol version (must match a version declared by the contract). |
| `capability_declaration` | array of CapabilityDeclaration | Yes | Exactly one CapabilityDeclaration per declared capability (all eight operations). Duplicate, missing or unknown capabilities are invalid. No implicit all-full default is permitted. |

### 1.3 CapabilityDeclaration

| Field | Type | Required | Contract |
|---|---|---|---|
| `capability` | string | Yes | One of the declared capabilities in the ProviderInterface. |
| `state` | string | Yes | One of `full`, `degraded_transport`, `degraded_scope`, `unavailable`. |
| `degradation_note` | string | Conditional | Required when `state` is not `full`. Describes what is degraded and what is preserved. |
| `scope_constraints` | object | Conditional | Required when `state=degraded_scope`; forbidden for `full`, `degraded_transport`, and `unavailable`. Contains `param_allowlist` mapping `payload.params` field names to non-empty arrays of allowed JSON scalar values. A request is within scope only when every constrained param exists and matches one allowed value using type-sensitive equality. Missing or mismatching constrained params produce `CAPABILITY_DEGRADED_SCOPE` at precedence 6 without delegation. No coercion, wildcard, regex, time-dependent, runtime-state, filesystem/network/environment lookup, or executable predicate rules are permitted. |

### 1.4 AdapterRequest

The canonical request envelope that every adapter must accept. A valid request is a single JSON object.

| Field | Type | Required | Contract |
|---|---|---|---|
| `protocol_version` | string | Yes | Exact contract version string (e.g., `"1.0.0"`). |
| `request_id` | string | Yes | Client-generated, globally unique, non-empty identifier. Echoed in the response. |
| `operation` | string | Yes | One of the declared operations (Section 2). |
| `payload` | OperationPayload | Yes | Typed, operation-specific payload (Section 2). |
| `context` | object | No | Portable trigger `ArtifactRef` and explicit correlation identifiers only. |

**`context` sub-fields (when present):**

| Field | Type | Required | Contract |
|---|---|---|---|
| `trigger_artifact` | object | No | Portable `ArtifactRef` with `artifact_id` and `artifact_kind`. Must not contain database paths, signer keys, credentials, or environment-derived values. |
| `correlation_id` | string | No | Client-defined correlation identifier for tracing. Must not embed provider identity, clock value, or machine-local state. |
| `executor_identity` | string | No | Identity of the request executor. |

**Forbidden fields in the canonical request:** `db_path`, `signer_key`, `signer_secret`, `credential`, `endpoint`, `host`, `port`, `connection_string`, `env`, `environment`, `provider_config`, `storage_path`, or any other provider-specific or environment-derived value.

### 1.5 OperationPayload

| Field | Type | Required | Contract |
|---|---|---|---|
| `operation` | string | Yes | Echo of the outer `operation` field for self-contained payload validation. |
| `params` | object | Yes | Operation-specific parameters. Shape defined per operation in Section 2. |

### 1.6 AdapterResponse

| Field | Type | Required | Contract |
|---|---|---|---|
| `protocol_version` | string | Yes | Contract version used to process this response. |
| `request_id` | string or null | Yes | Echo of the client `request_id`. Non-empty string unless the response is `status=error` with `error.code=MALFORMED_ENVELOPE`, in which case `null` is permitted when the input field was missing, empty, wrong-type, or unavailable because the request was not a valid object. Empty-string sentinels are forbidden. |
| `status` | string | Yes | `success` or `error`. |
| `operation` | string or null | Yes | Echo of the requested operation. Non-empty string unless the response is `status=error` with `error.code=MALFORMED_ENVELOPE`, in which case `null` is permitted when the input field was missing, empty, wrong-type, or unavailable because the request was not a valid object. Empty-string sentinels are forbidden. |
| `data` | object, array, or null | Conditional | Required when `status=success`; forbidden when `status=error`. Operations `create_run`, `append_event`, `get_run`, `get_stage`, `evidence_snapshot`, and `export_evidence` produce an `object`. Operation `read_events` produces an `array`. Operation `read_event` produces an `object` or `null` (when not found). |
| `error` | AdapterError | Conditional | Required when `status=error`. |
| `capability_state` | string | Yes | Capability state during processing: `full`, `degraded_transport`, `degraded_scope`, `unavailable`. |

`processed_at` (ISO 8601 timestamp) is transport-level metadata. The canonical adapter response does not include a timestamp field; timestamps are generated by the transport layer or the caller, not by the adapter itself.

### 1.7 AdapterError

| Field | Type | Required | Contract |
|---|---|---|---|
| `code` | string | Yes | One of the adapter error codes (Section 3). |
| `detail` | object | Yes | Structured error detail including the `code` and `detail` from any delegated `RuntimeStateSidecarError`. |
| `request_id` | string or null | Yes | Echo of the client `request_id`. Non-empty string unless `code=MALFORMED_ENVELOPE`, in which case `null` is permitted per the same rules as the top-level AdapterResponse. |
| `operation` | string or null | Yes | Operation that produced the error. Non-empty string unless `code=MALFORMED_ENVELOPE`, in which case `null` is permitted. Top-level and nested `request_id`/`operation` must match exactly. |

---

## 2. Frozen Operations

Every operation maps to exactly one accepted `RuntimeStateSidecar` method. The payload and result shape are defined by reference to the sidecar contract (`references/runtime-state-contract.md`), not by copying runtime schemas. The adapter performs envelope validation and transport translation only; the sidecar owns runtime semantics.

### 2.1 create_run

| Field | Value |
|---|---|
| **Operation** | `create_run` |
| **Sidecar method** | `RuntimeStateSidecar.create_run(request)` |
| **Payload** | `params` is an object with a non-empty string `run_id`. The adapter validates only that `params` is an object with a non-empty string `run_id` and forwards the complete object unchanged to the sidecar. The adapter must not translate, synthesize, or default run_provenance, trigger, executor_identity, run_ordinal, stage_graph, or any other field. |
| **Result** | `data` is an object containing the full sidecar `create_run` response. |

### 2.2 append_event

| Field | Value |
|---|---|
| **Operation** | `append_event` |
| **Sidecar method** | `RuntimeStateSidecar.append_event(request)` |
| **Payload** | `params` is an object with a non-empty string `run_id`. The adapter validates only that `params` is an object with a non-empty string `run_id` and forwards the complete object unchanged to the sidecar. The adapter must not translate, synthesize, or default event fields, digests, IDs, timestamps, heads, provenance, or visibility. |
| **Result** | `data` is an object containing the full sidecar `append_event` response. |

### 2.3 read_event

| Field | Value |
|---|---|
| **Operation** | `read_event` |
| **Sidecar method** | `RuntimeStateSidecar.read_event(run_id, event_order)` |
| **Payload** | `params` contains `run_id` (string) and `event_order` (positive integer). Strict validation: missing, wrong-type, or extra params produce `INVALID_PAYLOAD`. |
| **Result** | `data` is an object containing the runtime event, or `null` if not found. |

### 2.4 read_events

| Field | Value |
|---|---|
| **Operation** | `read_events` |
| **Sidecar method** | `RuntimeStateSidecar.read_events(run_id)` |
| **Payload** | `params` contains `run_id` (string). Strict validation: missing, wrong-type, or extra params produce `INVALID_PAYLOAD`. |
| **Result** | `data` is an array containing the ordered array of runtime event objects (gap-free verified). |

### 2.5 get_run

| Field | Value |
|---|---|
| **Operation** | `get_run` |
| **Sidecar method** | `RuntimeStateSidecar.get_run(run_id)` |
| **Payload** | `params` contains `run_id` (string). Strict validation: missing, wrong-type, or extra params produce `INVALID_PAYLOAD`. |
| **Result** | `data` is an object containing the run state projection. |

### 2.6 get_stage

| Field | Value |
|---|---|
| **Operation** | `get_stage` |
| **Sidecar method** | `RuntimeStateSidecar.get_stage(run_id, stage_id)` |
| **Payload** | `params` contains `run_id` (string) and `stage_id` (string). Strict validation: missing, wrong-type, or extra params produce `INVALID_PAYLOAD`. |
| **Result** | `data` is an object containing the stage state projection. |

### 2.7 evidence_snapshot

| Field | Value |
|---|---|
| **Operation** | `evidence_snapshot` |
| **Sidecar method** | `RuntimeStateSidecar.evidence_snapshot(run_id)` |
| **Payload** | `params` contains `run_id` (string). Strict validation: missing, wrong-type, or extra params produce `INVALID_PAYLOAD`. |
| **Result** | `data` is an object containing the structured evidence snapshot. |

### 2.8 export_evidence

| Field | Value |
|---|---|
| **Operation** | `export_evidence` |
| **Sidecar method** | `RuntimeStateSidecar.export_evidence(run_id, export_id, exported_at)` |
| **Payload** | `params` contains `run_id` (string), `export_id` (string), `exported_at` (ISO 8601 string). All three fields are required. Strict validation: missing, wrong-type, or extra params produce `INVALID_PAYLOAD`. |
| **Result** | `data` is an object containing the export manifest. |

---

## 3. Adapter Error Taxonomy

The adapter error taxonomy is total and deterministic. Error precedence is evaluated in order; the first matching condition produces the error.

| Precedence | Code | Condition | Adapter Action |
|---|---|---|---|
| 1 | `MALFORMED_ENVELOPE` | Request is not valid JSON or top-level required fields are missing or of wrong type. | Return error; do not delegate. Echo available input values as non-empty strings for `request_id` and `operation`. Use JSON `null` when the corresponding input field is missing, empty, wrong-type, or unavailable because the request is not an object. Empty-string sentinels are forbidden. `protocol_version` is the adapter-supported version `"1.0.0"`. |
| 2 | `UNSUPPORTED_VERSION` | `protocol_version` does not match a supported contract version. | Return error; do not delegate. |
| 3 | `UNKNOWN_OPERATION` | `operation` is not one of the frozen operations (Section 2). | Return error; do not delegate. |
| 4 | `INVALID_PAYLOAD` | `payload.params` fails operation-specific parameter validation (wrong types, missing required fields). | Return error; do not delegate. |
| 5 | `CAPABILITY_UNAVAILABLE` | The adapter's `capability_declaration` for the requested operation is `unavailable`. | Return error; do not delegate. |
| 6 | `CAPABILITY_DEGRADED_SCOPE` | The adapter's capability state for the requested operation is `degraded_scope` and the request falls outside the reduced scope. | Return error; do not delegate. |
| 7 | `DELEGATED_ERROR` | The delegated sidecar method raised a `RuntimeStateSidecarError`. | Preserve the sidecar `code` and `detail` in the error response. Adapter adds no interpretation. |
| 8 | `INTERNAL_ERROR` | An unexpected error occurred within the adapter that is not one of the above. | Return error; record the exception type and message in `detail.internal`. Do not expose tracebacks. |

**Error detail contract:** Every `AdapterError.detail` object contains at minimum the `code` and `detail` fields echoed from a `DELEGATED_ERROR`, or an `internal` object with `exception_type` and `message` for an `INTERNAL_ERROR`. Tracebacks, stack frames, and machine-local file paths must not appear in error detail.

---

## 4. Capability States

### 4.1 State Definitions

| State | Meaning | Correctness | Adapter Behavior |
|---|---|---|---|
| `full` | All capabilities available with correctness guarantees. | Full correctness. | Normal operation. |
| `degraded_transport` | Transport, dispatch, or storage mechanism is reduced. | Correctness preserved. | Process request; record `capability_state=degraded_transport` in response. |
| `degraded_scope` | Reduced capability scope. Correctness within scope. | Correctness preserved within declared scope. | If request falls within scope, process with `capability_state=degraded_scope`. If outside scope, return `CAPABILITY_DEGRADED_SCOPE` error. |
| `unavailable` | Capability is not available. | Cannot guarantee correctness. | Return `CAPABILITY_UNAVAILABLE` error. Required operations blocked. |

### 4.2 Degradation Rules

1. Degradation must never change correctness. An operation that would succeed under `full` must succeed under `degraded_transport` and within-scope `degraded_scope`.
2. `unavailable` on a correctness-critical operation must cause the adapter to return a `CAPABILITY_UNAVAILABLE` error. No adapter may synthesize a successful runtime or gate outcome.
3. Capability degradation is declared per adapter, per capability. It is not inferred from the request or the runtime state.

---

## 5. Authority Boundaries

### 5.1 Adapter Authority

The adapter is authorized to:
- Validate the request envelope against this contract (Section 1.4, Section 3).
- Translate the validated request into a sidecar method call (Section 2).
- Return the sidecar response or an adapter error (Section 1.6, Section 3).
- Record its own `capability_state` in every response.

The adapter is NOT authorized to:
- Interpret, modify, or validate the semantic content of sidecar responses.
- Write to the workflow database or bypass lifecycle helpers.
- Produce, consume, or evaluate `GateDecision` artifacts.
- Initiate retry, resume, or intervention of any run.
- Dispatch a Validator or produce independent Validator evidence.
- Materialize, classify, extract, or curate Knowledge entries.
- Perform automatic repair or remediation of any error.
- Access provider-specific configuration beyond what is declared in this contract.

### 5.2 Runtime Sidecar Authority

The `RuntimeStateSidecar` (`scripts/runtime_state_sidecar.py`) owns runtime semantics: event sourcing, run/ stage/ event state, evidence snapshot composition, and evidence export. The adapter delegates to the sidecar without reinterpreting its output. The sidecar's error codes and detail are preserved verbatim through `DELEGATED_ERROR`.

### 5.3 Gate and Validator Authority

Gate evaluation and GateDecision production are owned by the Gate consumer (`references/runtime-architecture.md` Section 2.4). The Validator is an independent read-only role (`references/validator-protocol.md`). No adapter may produce, consume, modify, or replace Gate Decisions or Validation Reports.

### 5.4 Workflow Authority

Ticket and epic lifecycle transitions are owned by the Architect and Planner through lifecycle helpers (`references/lifecycle.md`). The adapter has no access to workflow state and must not perform any lifecycle mutation.

---

## 6. Operational Guarantees

### 6.1 Input Non-Mutation

The adapter must not mutate the input request object. The original request is preserved for audit and replay.

### 6.2 Exactly-Once Delegation

Per accepted request, the adapter delegates to the sidecar exactly once. Multiple delegation calls for the same adapter request are prohibited. The adapter must not implement retry logic -- retry decisions belong to the caller or the runtime architecture (`references/runtime-architecture.md` Section 2.7).

### 6.3 No Hidden Retry

The adapter must not silently retry on failure. Every error is returned to the caller. Automatic retry with backoff, circuit breaking, or dead-letter queuing is outside the adapter's scope.

### 6.4 No Implicit Defaults

The adapter must not supply default values for `protocol_version`, `request_id`, `operation`, `run_id`, `event_order`, timestamps, unique identifiers, file paths, or any other field. Missing or invalid top-level AdapterRequest fields (`protocol_version`, `request_id`, `operation`, `payload`) produce a `MALFORMED_ENVELOPE` error (precedence 1), in which `request_id` and `operation` may use JSON `null` per the null-echo rules (Section 3, precedence 1). Missing or invalid fields in `payload.params` (including `run_id`, `export_id`, `exported_at`) produce an `INVALID_PAYLOAD` error (precedence 4).

### 6.5 Provider and Storage Neutrality

Per `references/runtime-architecture.md` Section 7.1, conformance is measured by the declared `ProviderInterface` and the evidence produced. The adapter contract does not mandate Python, SQLite, any specific database, transport protocol, or execution environment.

---

## 7. Versioning

| Field | Value |
|---|---|
| Contract version | 1.0.0 |
| Protocol version string | `"1.0.0"` |
| Version negotiation | Client sends `protocol_version`; adapter matches exactly (no range negotiation). |
| Backward compatibility | Future contract versions define migration rules. Adapters declare the versions they support. |

---

## 8. Consumer Crosswalk

### 8.1 Adapter Implementations

Implementers of this contract must satisfy the `ProviderInterface` and `AdapterDescriptor` types. The adapter performs envelope validation (Section 3) and delegates to the sidecar (Section 2). Every adapter response includes the `capability_state` field reflecting the adapter's declared capability state during processing.

### 8.2 Transport Providers

Transport providers (HTTP, gRPC, stdio, in-process) implement the serialization and dispatch layer. They must preserve all fields defined by `AdapterRequest` and `AdapterResponse` without adding provider-specific fields in the canonical payload. Transport-level metadata (headers, routing keys, connection parameters) is outside the scope of this contract.

### 8.3 Runtime Consumers

Runtime consumers (Gate consumer, Validator coordinator, CLI tools) consume adapter responses as evidence. They rely on the `request_id` correlation, `capability_state` transparency, and deterministic error taxonomy defined by this contract. Consumers must not bypass the adapter and access the sidecar directly.

---

## 9. Public Hygiene

This contract is `visibility: public`. All types, operations, and error codes use public-safe values. No secrets, credentials, tokens, machine-local paths, proprietary configuration, or internal Epic/Ticket identifiers appear in this document.

---

## 10. Governance and References

| Field | Value |
|---|---|
| Governing contract | `references/runtime-architecture.md` v0.8.0 |
| Downstream contract of | Runtime architecture (adapter identity, interface contract, degradation contract) |
| Related contracts | `references/runtime-state-contract.md`, `references/runtime-evidence-export-contract.md`, `references/runtime-artifact-visibility-contract.md` |
| Consumer role | Adapter implementers, transport providers, runtime consumers |
| Non-goals | Adapter discovery, lifecycle, configuration, dynamic loading, provider registry, schema generation, fixtures, tests, CLI, transport code |
