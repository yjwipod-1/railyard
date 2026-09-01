"""Executable adversarial conformance suite for the runtime state contract."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import pathlib
import re
import unittest
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "references" / "runtime-state-contract.md"
FIXTURE = ROOT / "examples" / "runtime_state_contract_fixtures" / "conformance.json"
ZERO_DIGEST = "sha256:" + ("0" * 64)
FROZEN_CONTRACT_SHA256 = "016db98f28eb5fc291f6fc608f761d733f9fa6652fb12ea7af78166a0a54c9b1"
FROZEN_FIXTURE_SHA256 = "5fa055cb9353e6172b304de211208f8a6dfc017822fc4ce45f32576e10667e45"
FROZEN_APPEND_RECEIPT = {
    "required": ["event_id", "event_order", "stored_content_digest", "new_stream_head", "signed_receipt"],
    "signed_receipt_required": ["algorithm", "key_id", "signed_payload", "signature"],
    "new_stream_head_required": ["event_order", "content_digest"],
    "signed_payload_required": ["run_id", "event_order", "content_digest"],
    "verification_registry": {"HMAC-SHA256": ["conformance-key-1"]},
}
FROZEN_EVENT_TYPES = {
    "run.created",
    "run.started",
    "run.stage.started",
    "run.stage.completed",
    "run.stage.failed",
    "run.stage.skipped",
    "run.gate.evaluated",
    "run.gate.blocked",
    "run.gate.overridden",
    "run.retry.initiated",
    "run.resumed",
    "run.redesign",
    "run.intervention",
    "run.terminated",
    "run.completed",
    "run.failed",
    "run.blocked",
    "run.interrupted",
}

# Independent test oracle. These values are intentionally not derived from the
# candidate fixture, so coordinated schema/reducer mutations cannot self-validate.
FROZEN_EVENT_SCHEMAS = {
    "run.created": {"required": ["run_provenance", "trigger", "executor_identity", "run_ordinal", "created_at", "stage_graph", "visibility_context"], "conditional": ["lineage"]},
    "run.started": {"required": ["started_at", "executor_identity"]},
    "run.stage.started": {"required": ["stage_id", "started_at", "entry_evidence"]},
    "run.stage.completed": {"required": ["stage_id", "completed_at", "gate_decisions", "artifacts_produced"]},
    "run.stage.failed": {"required": ["stage_id", "failed_at", "error", "failure_category", "failure_is_transient", "failure_is_deterministic", "artifacts_produced_before_failure", "retry_eligible"]},
    "run.stage.skipped": {"required": ["stage_id", "skipped_at", "authorized_by", "reason", "authorizing_intervention_id"]},
    "run.gate.evaluated": {"required": ["stage_id", "gate_id", "decision_id", "outcome", "execution_mode", "evaluated_at", "evaluated_by", "evidence"], "conditional": ["degradation_note", "reevaluates_decision_id"]},
    "run.gate.blocked": {"required": ["stage_id", "gate_id", "decision_id", "outcome", "execution_mode", "evaluated_at", "evaluated_by", "evidence", "blocked_reason", "required_evidence"], "conditional": ["degradation_note", "reevaluates_decision_id"]},
    "run.gate.overridden": {"required": ["stage_id", "gate_id", "new_decision_id", "original_decision_id", "outcome", "execution_mode", "evaluated_at", "evaluated_by", "evidence", "override_reason", "authorized_by", "authorized_at", "authorizing_intervention_id"], "conditional": ["degradation_note"]},
    "run.retry.initiated": {"required": ["new_run_id", "lineage", "retry_strategy", "current_retry_count", "max_retries", "failure_category", "authorized_by", "authorized_at"], "conditional": ["failure_is_transient", "failure_is_deterministic"]},
    "run.resumed": {"required": ["new_run_id", "lineage", "checkpoint_event_order", "recovery_action", "authorized_by", "authorized_at"]},
    "run.redesign": {"required": ["new_run_id", "lineage", "revised_stage_graph", "authorized_by", "authorized_at"]},
    "run.intervention": {"required": ["intervention_id", "intervention_type", "authorized_by", "reason", "evidence"]},
    "run.terminated": {"required": ["terminated_at", "terminated_by", "termination_reason", "from_status", "terminal_status"]},
    "run.completed": {"required": ["completed_at", "terminal_stages_completed", "final_projection_digest", "total_event_count"]},
    "run.failed": {"required": ["failed_at", "failed_stage_id", "error", "failure_category", "failure_is_transient", "failure_is_deterministic", "retry_eligible"]},
    "run.blocked": {"required": ["blocked_at", "blocked_reason", "resolution_paths", "required_evidence"]},
    "run.interrupted": {"required": ["interrupted_at", "last_event_order", "interruption_cause", "checkpoint_available"]},
}

FROZEN_REDUCERS = {
    "run.created": {"allowed_status": None, "next_status": "pending", "reads": ["run_provenance", "trigger", "executor_identity", "run_ordinal", "created_at", "stage_graph", "lineage"], "initializes_visibility": True, "visibility_context_copied": True},
    "run.started": {"allowed_status": "pending", "next_status": "active", "reads": ["started_at", "executor_identity"]},
    "run.stage.started": {"allowed_status": "active", "next_status": "active", "reads": ["stage_id", "started_at", "entry_evidence"]},
    "run.stage.completed": {"allowed_status": "active", "next_status": "active", "reads": ["stage_id", "completed_at", "gate_decisions", "artifacts_produced"], "builds_runtime_artifacts": True, "requires_full_runtime_artifact": True, "no_artifact_normalization": True, "recomputes_resolved_run_visibility": True},
    "run.stage.failed": {"allowed_status": "active", "next_status": "failed", "reads": ["stage_id", "failed_at", "error", "failure_category", "failure_is_transient", "failure_is_deterministic", "artifacts_produced_before_failure", "retry_eligible"], "builds_runtime_artifacts": True, "requires_full_runtime_artifact": True, "no_artifact_normalization": True, "recomputes_resolved_run_visibility": True},
    "run.stage.skipped": {"allowed_status": "active", "next_status": "active", "reads": ["stage_id", "skipped_at", "authorized_by", "reason", "authorizing_intervention_id"]},
    "run.gate.evaluated": {"allowed_status": "active", "next_status": "active", "reads": ["stage_id", "gate_id", "decision_id", "outcome", "execution_mode", "evaluated_at", "evaluated_by", "evidence", "degradation_note", "reevaluates_decision_id"], "reevaluation_policy": "gate_reevaluation"},
    "run.gate.blocked": {"allowed_status": "active", "next_status": "active", "reads": ["stage_id", "gate_id", "decision_id", "outcome", "execution_mode", "evaluated_at", "evaluated_by", "evidence", "blocked_reason", "required_evidence", "degradation_note", "reevaluates_decision_id"], "reevaluation_policy": "gate_reevaluation"},
    "run.gate.overridden": {"allowed_status": "active", "next_status": "active", "reads": ["stage_id", "gate_id", "new_decision_id", "original_decision_id", "outcome", "execution_mode", "evaluated_at", "evaluated_by", "evidence", "override_reason", "authorized_by", "authorized_at", "authorizing_intervention_id", "degradation_note"]},
    "run.retry.initiated": {"allowed_status": "failed", "next_status": "failed", "reads": ["new_run_id", "lineage", "retry_strategy", "current_retry_count", "max_retries", "failure_category", "authorized_by", "authorized_at", "failure_is_transient", "failure_is_deterministic"], "authorization_policy": "auto_retry_policy"},
    "run.resumed": {"allowed_status": "interrupted", "next_status": "interrupted", "reads": ["new_run_id", "lineage", "checkpoint_event_order", "recovery_action", "authorized_by", "authorized_at"]},
    "run.redesign": {"allowed_status": ["failed", "blocked"], "next_status": "failed", "reads": ["new_run_id", "lineage", "revised_stage_graph", "authorized_by", "authorized_at"], "effect_policy": "redesign_policy"},
    "run.intervention": {"allowed_status": ["active", "blocked", "interrupted"], "next_status": "unchanged", "reads": ["intervention_id", "intervention_type", "authorized_by", "reason", "evidence"]},
    "run.terminated": {"allowed_status": ["pending", "active", "interrupted"], "next_status": "payload.terminal_status", "reads": ["terminated_at", "terminated_by", "termination_reason", "from_status", "terminal_status"]},
    "run.completed": {"allowed_status": "active", "next_status": "completed", "reads": ["completed_at", "terminal_stages_completed", "final_projection_digest", "total_event_count"]},
    "run.failed": {"allowed_status": "active", "next_status": "failed", "reads": ["failed_at", "failed_stage_id", "error", "failure_category", "failure_is_transient", "failure_is_deterministic", "retry_eligible"]},
    "run.blocked": {"allowed_status": "active", "next_status": "blocked", "reads": ["blocked_at", "blocked_reason", "resolution_paths", "required_evidence"]},
    "run.interrupted": {"allowed_status": "active", "next_status": "interrupted", "reads": ["interrupted_at", "last_event_order", "interruption_cause", "checkpoint_available"]},
}
FROZEN_STAGE_GRAPH_VALIDATION = {
    "exact_top_level_fields": ["graph_id", "stages", "edges", "entry_stages", "terminal_stages"],
    "requires_unique_stage_ids": True,
    "requires_resolved_edge_entry_terminal_ids": True,
    "requires_acyclic_graph": True,
    "genesis_stage_status": "pending",
    "validator_gate_requires_typed_contract_ref": True,
}
FROZEN_AUTO_RETRY_POLICY = {
    "authorized_by": "system",
    "required_retry_strategy": "full",
    "required_failure_category": "command_failed",
    "required_true_fields": ["failure_is_transient", "failure_is_deterministic"],
    "parent_projection_match_fields": ["failure_category", "failure_is_transient", "failure_is_deterministic"],
    "retry_count_rule": "current_retry_count < max_retries <= 3",
}
FROZEN_REDESIGN_POLICY = {
    "allowed_parent_statuses": ["failed", "blocked"],
    "resulting_parent_status": "failed",
    "termination_time_field": "authorized_at",
    "termination_reason_field": "lineage.lineage_reason",
    "child_stage_graph_field": "revised_stage_graph",
}
FROZEN_GATE_REEVALUATION = {
    "event_types": ["run.gate.evaluated", "run.gate.blocked"],
    "prior_decision_reference_field": "reevaluates_decision_id",
    "reference_must_equal_current_decision": True,
    "requires_new_decision_id": True,
    "requires_new_evidence": True,
    "new_evidence_rule": "at least one typed ArtifactRef absent from the referenced decision",
    "preserves_immutable_history": True,
    "updates_current_decision_pointer": True,
    "required_pass_requires_non_empty_evidence": True,
}
FROZEN_LINEAGE_BOUNDARIES = {
    "retry": {"parent_status": "failed", "parent_boundary_event_type": "run.failed"},
    "retry_stage_failed": {"lineage_kind": "retry", "parent_status": "failed", "parent_boundary_event_type": "run.stage.failed"},
    "retry_terminated_failed": {"lineage_kind": "retry", "parent_status": "failed", "parent_boundary_event_type": "run.terminated", "terminal_status": "failed"},
    "resume": {"parent_status": "interrupted", "parent_boundary_event_type": "run.interrupted"},
    "more_evidence": {"parent_status": "blocked", "parent_boundary_event_type": "run.blocked"},
    "more_evidence_terminated_blocked": {"lineage_kind": "more_evidence", "parent_status": "blocked", "parent_boundary_event_type": "run.terminated", "terminal_status": "blocked"},
    "redesign_failed": {"lineage_kind": "redesign", "parent_status": "failed", "parent_boundary_event_type": "run.failed"},
    "redesign_stage_failed": {"lineage_kind": "redesign", "parent_status": "failed", "parent_boundary_event_type": "run.stage.failed"},
    "redesign_terminated_failed": {"lineage_kind": "redesign", "parent_status": "failed", "parent_boundary_event_type": "run.terminated", "terminal_status": "failed"},
    "redesign_blocked": {"lineage_kind": "redesign", "parent_status": "blocked", "parent_boundary_event_type": "run.blocked"},
    "redesign_terminated_blocked": {"lineage_kind": "redesign", "parent_status": "blocked", "parent_boundary_event_type": "run.terminated", "terminal_status": "blocked"},
}


class ConformanceError(ValueError):
    """A candidate violates one named contract rule."""


class StaleHeadError(ConformanceError):
    """Carries the exact frozen stale-head rejection object."""

    def __init__(self, rejection: dict):
        super().__init__("stale_head")
        self.rejection = rejection


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def validate_frozen_surface_bytes(contract_bytes: bytes, fixture_bytes: bytes) -> None:
    if hashlib.sha256(contract_bytes).hexdigest() != FROZEN_CONTRACT_SHA256:
        raise ConformanceError("frozen-contract-hash")
    if hashlib.sha256(fixture_bytes).hexdigest() != FROZEN_FIXTURE_SHA256:
        raise ConformanceError("frozen-fixture-hash")


def canonical_bytes(value) -> bytes:
    """RFC 8785 JCS for this contract's I-JSON integer-only profile."""
    def validate_string(item: str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in item):
            raise ConformanceError("canonical-invalid-unicode-scalar")

    def validate(item):
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, str):
            validate_string(item)
            return
        if isinstance(item, int) and not isinstance(item, bool):
            if not (-(2**53) + 1 <= item <= (2**53) - 1):
                raise ConformanceError("canonical-integer-range")
            return
        if isinstance(item, list):
            for child in item:
                validate(child)
            return
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise ConformanceError("canonical-object-key")
            for key in item:
                validate_string(key)
            for child in item.values():
                validate(child)
            return
        raise ConformanceError("canonical-unsupported-type")

    def utf16_sort_key(key: str) -> bytes:
        return key.encode("utf-16-be")

    def serialize(item) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False)
        if isinstance(item, int):
            return str(item)
        if isinstance(item, list):
            return "[" + ",".join(serialize(child) for child in item) + "]"
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + serialize(item[key])
            for key in sorted(item, key=utf16_sort_key)
        ) + "}"

    validate(value)
    return serialize(value).encode("utf-8")


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def require_string(value, rule="typed-string"):
    if not isinstance(value, str) or not value:
        raise ConformanceError(rule)


def require_bool(value, rule="typed-bool"):
    if type(value) is not bool:
        raise ConformanceError(rule)


def require_integer(value, minimum=0, rule="typed-integer"):
    if type(value) is not int or value < minimum:
        raise ConformanceError(rule)


def require_timestamp(value):
    require_string(value, "typed-timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ConformanceError("typed-timestamp") from error
    if parsed.tzinfo is None:
        raise ConformanceError("typed-timestamp")


def require_digest(value):
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise ConformanceError("typed-digest")


def require_string_array(value, allow_empty=True):
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ConformanceError("typed-string-array")
    for item in value:
        require_string(item, "typed-string-array")


def require_artifact_ref(value):
    if not isinstance(value, dict):
        raise ConformanceError("typed-artifact-ref")
    # RuntimeArtifacts carry artifact_ref sub-object
    if "artifact_ref" in value:
        ref = value["artifact_ref"]
        if not isinstance(ref, dict) or not {"artifact_id", "artifact_kind"}.issubset(ref):
            raise ConformanceError("typed-artifact-ref")
        require_string(ref["artifact_id"], "typed-artifact-ref")
        require_string(ref["artifact_kind"], "typed-artifact-ref")
        for field in ("artifact_version", "locator"):
            if field in ref:
                require_string(ref[field], "typed-artifact-ref")
        if "digest" in ref:
            require_digest(ref["digest"])
        return
    if not {"artifact_id", "artifact_kind"}.issubset(value):
        raise ConformanceError("typed-artifact-ref")
    require_string(value["artifact_id"], "typed-artifact-ref")
    require_string(value["artifact_kind"], "typed-artifact-ref")
    for field in ("artifact_version", "locator"):
        if field in value:
            require_string(value[field], "typed-artifact-ref")
    if "digest" in value:
        require_digest(value["digest"])


def require_artifact_array(value):
    if not isinstance(value, list):
        raise ConformanceError("typed-artifact-array")
    for item in value:
        require_artifact_ref(item)


def require_error(value):
    if not isinstance(value, dict) or not {"code", "message"}.issubset(value) or not set(value).issubset({"code", "message", "stack"}):
        raise ConformanceError("typed-error")
    require_string(value["code"], "typed-error")
    require_string(value["message"], "typed-error")
    if "stack" in value:
        require_string(value["stack"], "typed-error")


def require_lineage_shape(value):
    required = {"parent_run_id", "lineage_kind", "lineage_reason", "parent_status", "parent_boundary_event_id", "parent_boundary_event_type", "parent_boundary_event_order"}
    if not isinstance(value, dict) or set(value) != required:
        raise ConformanceError("typed-lineage")
    for field in required - {"parent_boundary_event_order"}:
        require_string(value[field], "typed-lineage")
    require_integer(value["parent_boundary_event_order"], 1, "typed-lineage")


def require_stage_graph(value):
    required = {"graph_id", "stages", "edges", "entry_stages", "terminal_stages"}
    if not isinstance(value, dict) or set(value) != required:
        raise ConformanceError("typed-stage-graph")
    require_string(value["graph_id"], "typed-stage-graph")
    if not isinstance(value["stages"], list) or not value["stages"]:
        raise ConformanceError("typed-stage-graph")
    stage_ids = []
    for stage in value["stages"]:
        if not isinstance(stage, dict) or not {"stage_id", "name", "required", "status"}.issubset(stage):
            raise ConformanceError("typed-stage-graph")
        if not set(stage).issubset({"stage_id", "name", "required", "status", "gates"}):
            raise ConformanceError("typed-stage-graph")
        require_string(stage["stage_id"], "typed-stage-graph")
        require_string(stage["name"], "typed-stage-graph")
        require_bool(stage["required"], "typed-stage-graph")
        if stage["status"] != "pending":
            raise ConformanceError("typed-stage-graph")
        stage_ids.append(stage["stage_id"])
        gates = stage.get("gates", [])
        if not isinstance(gates, list):
            raise ConformanceError("typed-stage-graph")
        gate_ids = set()
        for gate in gates:
            if not isinstance(gate, dict) or not {"gate_id", "gate_type", "required", "failure_behavior"}.issubset(gate):
                raise ConformanceError("typed-stage-graph")
            if not set(gate).issubset({"gate_id", "gate_type", "required", "failure_behavior", "allow_gate_override", "contract_ref"}):
                raise ConformanceError("typed-stage-graph")
            require_string(gate["gate_id"], "typed-stage-graph")
            require_string(gate["gate_type"], "typed-stage-graph")
            require_bool(gate["required"], "typed-stage-graph")
            if gate["failure_behavior"] not in {"halt_stage", "halt_run", "warn", "require_intervention"}:
                raise ConformanceError("typed-stage-graph")
            if "allow_gate_override" in gate:
                require_bool(gate["allow_gate_override"], "typed-stage-graph")
            if gate["gate_type"] == "validator":
                if "contract_ref" not in gate:
                    raise ConformanceError("validator-gate-contract-ref")
                require_artifact_ref(gate["contract_ref"])
                locator = gate["contract_ref"].get("locator")
                if not locator:
                    raise ConformanceError("validator-gate-contract-ref-unresolved")
                relative_path = locator.split("#", 1)[0]
                resolved_path = (ROOT / relative_path).resolve()
                if ROOT.resolve() not in resolved_path.parents or not resolved_path.is_file():
                    raise ConformanceError("validator-gate-contract-ref-unresolved")
            if gate["gate_id"] in gate_ids:
                raise ConformanceError("typed-stage-graph")
            gate_ids.add(gate["gate_id"])
    if len(stage_ids) != len(set(stage_ids)):
        raise ConformanceError("typed-stage-graph")
    require_string_array(value["entry_stages"], allow_empty=False)
    require_string_array(value["terminal_stages"], allow_empty=False)
    if not set(value["entry_stages"] + value["terminal_stages"]).issubset(stage_ids):
        raise ConformanceError("typed-stage-graph")
    if not isinstance(value["edges"], list):
        raise ConformanceError("typed-stage-graph")
    for edge in value["edges"]:
        if not isinstance(edge, dict) or not {"from", "to"}.issubset(edge) or not set(edge).issubset({"from", "to", "condition"}):
            raise ConformanceError("typed-stage-graph")
        if edge["from"] not in stage_ids or edge["to"] not in stage_ids or edge["from"] == edge["to"]:
            raise ConformanceError("typed-stage-graph")
        if edge.get("condition", "always") not in {"always", "on_pass", "on_fail", "on_skip"}:
            raise ConformanceError("typed-stage-graph")
    adjacency = {stage_id: [] for stage_id in stage_ids}
    for edge in value["edges"]:
        adjacency[edge["from"]].append(edge["to"])
    visiting = set()
    visited = set()

    def visit(stage_id):
        if stage_id in visiting:
            raise ConformanceError("stage-graph-cycle")
        if stage_id in visited:
            return
        visiting.add(stage_id)
        for successor in adjacency[stage_id]:
            visit(successor)
        visiting.remove(stage_id)
        visited.add(stage_id)

    for stage_id in stage_ids:
        visit(stage_id)


def require_provenance(value):
    if not isinstance(value, dict) or not {"origin_artifact", "governing_contracts"}.issubset(value) or not set(value).issubset({"origin_artifact", "origin_epic", "governing_contracts", "additional_sources"}):
        raise ConformanceError("typed-provenance")
    require_artifact_ref(value["origin_artifact"])
    if "origin_epic" in value:
        require_artifact_ref(value["origin_epic"])
    require_artifact_array(value["governing_contracts"])
    if not value["governing_contracts"]:
        raise ConformanceError("typed-provenance")
    require_artifact_array(value.get("additional_sources", []))


def require_visibility_context(value):
    if not isinstance(value, dict):
        raise ConformanceError("typed-visibility-context")
    required = {"trigger_visibility", "policy_contributors", "contract_contributors", "run_visibility_resolution", "resolved_run_visibility"}
    if not required.issubset(value):
        raise ConformanceError("typed-visibility-context")
    if not set(value).issubset(required):
        raise ConformanceError("typed-visibility-context")
    if not isinstance(value["trigger_visibility"], dict):
        raise ConformanceError("typed-visibility-context")
    if not isinstance(value["policy_contributors"], list):
        raise ConformanceError("typed-visibility-context")
    if not isinstance(value["contract_contributors"], list) or not value["contract_contributors"]:
        raise ConformanceError("typed-visibility-context")
    if not isinstance(value["run_visibility_resolution"], dict):
        raise ConformanceError("typed-visibility-context")
    require_string(value["resolved_run_visibility"], "typed-visibility-context")
    if value["resolved_run_visibility"] not in {"public", "project", "restricted"}:
        raise ConformanceError("typed-visibility-context")
    if value["resolved_run_visibility"] != value["run_visibility_resolution"].get("resolved_visibility"):
        raise ConformanceError("typed-visibility-context")


def validate_payload(contract: dict, event_type: str, payload: dict) -> None:
    if event_type not in contract["event_schemas"]:
        raise ConformanceError("event-schema-missing")
    if not isinstance(payload, dict):
        raise ConformanceError("payload-object")
    schema = contract["event_schemas"][event_type]
    required = set(schema["required"])
    conditional = set(schema.get("conditional", []))
    missing = required - set(payload)
    if missing:
        raise ConformanceError("payload-required:" + ",".join(sorted(missing)))
    if not set(payload).issubset(required | conditional):
        raise ConformanceError("payload-extra")

    timestamp_fields = {"created_at", "started_at", "completed_at", "failed_at", "skipped_at", "evaluated_at", "authorized_at", "terminated_at", "blocked_at", "interrupted_at"}
    positive_integer_fields = {"run_ordinal", "current_retry_count", "max_retries", "checkpoint_event_order", "total_event_count", "last_event_order"}
    boolean_fields = {"retry_eligible", "checkpoint_available", "failure_is_transient", "failure_is_deterministic"}
    digest_fields = {"final_projection_digest"}
    artifact_array_fields = {"entry_evidence", "artifacts_produced", "artifacts_produced_before_failure", "evidence", "required_evidence", "restored_evidence"}
    string_array_fields = {"terminal_stages_completed", "resolution_paths"}
    structured_fields = {"run_provenance", "stage_graph", "lineage", "revised_stage_graph", "error", "gate_decisions", "visibility_context"}
    all_fields = required | conditional
    string_fields = all_fields - timestamp_fields - positive_integer_fields - boolean_fields - digest_fields - artifact_array_fields - string_array_fields - structured_fields

    for field in string_fields & set(payload):
        require_string(payload[field], "typed-payload-string")
    for field in timestamp_fields & set(payload):
        require_timestamp(payload[field])
    for field in positive_integer_fields & set(payload):
        require_integer(payload[field], 1, "typed-payload-integer")
    for field in boolean_fields & set(payload):
        require_bool(payload[field], "typed-payload-bool")
    for field in digest_fields & set(payload):
        require_digest(payload[field])
    for field in artifact_array_fields & set(payload):
        require_artifact_array(payload[field])
    for field in string_array_fields & set(payload):
        require_string_array(payload[field])
    if "run_provenance" in payload:
        require_provenance(payload["run_provenance"])
    if "stage_graph" in payload:
        require_stage_graph(payload["stage_graph"])
    if "revised_stage_graph" in payload:
        require_stage_graph(payload["revised_stage_graph"])
    if "lineage" in payload:
        require_lineage_shape(payload["lineage"])
        validate_lineage(contract, payload["lineage"])
    if "error" in payload:
        require_error(payload["error"])
    if "gate_decisions" in payload:
        if not isinstance(payload["gate_decisions"], list):
            raise ConformanceError("typed-gate-decisions")
        for decision in payload["gate_decisions"]:
            if isinstance(decision, str):
                require_string(decision, "typed-gate-decisions")
            elif isinstance(decision, dict) and set(decision) == {"decision_id"}:
                require_string(decision["decision_id"], "typed-gate-decisions")
            else:
                raise ConformanceError("typed-gate-decisions")
    if "visibility_context" in payload:
        require_visibility_context(payload["visibility_context"])

    enum_rules = {
        "trigger": {"ticket", "ci_pipeline", "external_pipeline", "local_script", "api"},
        "execution_mode": {"full", "degraded_transport", "degraded_storage"},
        "outcome": {"pass", "fail", "blocked", "inconclusive", "human_review_required"},
        "retry_strategy": {"full", "resume"},
        "recovery_action": {"replay_from_checkpoint", "restart_stage"},
        "intervention_type": {"skip_stage", "override_gate", "force_retry", "force_terminal", "provide_evidence"},
        "terminal_status": {"failed", "blocked"},
        "from_status": {"pending", "active", "interrupted"},
        "interruption_cause": {"session_lost", "environment_terminated", "external_signal"},
        "failure_category": {"permission_denied", "command_failed", "sandbox_boundary", "authorization_required", "environment_issue", "unresolved_dependency"},
    }
    for field, allowed_values in enum_rules.items():
        if field in payload and payload[field] not in allowed_values:
            raise ConformanceError("payload-enum-" + field)
    if "resolution_paths" in payload and not set(payload["resolution_paths"]).issubset({"more_evidence", "contract_redesign", "human_intervention", "capability_restoration"}):
        raise ConformanceError("payload-enum-resolution-paths")
    if event_type == "run.gate.blocked" and payload["outcome"] != "blocked":
        raise ConformanceError("gate-blocked-outcome")
    if event_type == "run.gate.overridden" and payload["outcome"] != "pass":
        raise ConformanceError("gate-override-outcome")
    if "execution_mode" in payload:
        degraded = payload["execution_mode"] != "full"
        if degraded and "degradation_note" not in payload:
            raise ConformanceError("gate-degradation-note-required")
        if not degraded and "degradation_note" in payload:
            raise ConformanceError("gate-degradation-note-forbidden")
    authority_domains = {
        "run.stage.skipped": {"human"},
        "run.gate.overridden": {"architect", "human"},
        "run.retry.initiated": {"architect", "human", "system"},
        "run.resumed": {"architect", "human"},
        "run.redesign": {"architect", "human"},
        "run.intervention": {"architect", "human"},
    }
    if event_type in authority_domains and payload["authorized_by"] not in authority_domains[event_type]:
        raise ConformanceError("payload-authority")


def validate_contract_shape(contract: dict) -> None:
    schemas = set(contract["event_schemas"])
    reducers = set(contract["reducers"])
    if schemas != FROZEN_EVENT_TYPES:
        raise ConformanceError("frozen-taxonomy-equality")
    if reducers != schemas:
        raise ConformanceError("schema-reducer-equality")
    if contract["event_schemas"] != FROZEN_EVENT_SCHEMAS:
        raise ConformanceError("frozen-payload-schema-crosswalk")
    if contract["reducers"] != FROZEN_REDUCERS:
        raise ConformanceError("frozen-reducer-crosswalk")
    for event_type, reducer in contract["reducers"].items():
        available = set(contract["event_schemas"][event_type]["required"])
        available.update(contract["event_schemas"][event_type].get("conditional", []))
        missing = set(reducer["reads"]) - available
        if missing:
            raise ConformanceError("reducer-payload-sufficiency")
    receipt = contract["append_receipt"]
    if "signed_receipt" not in receipt["required"]:
        raise ConformanceError("signed-receipt-mandatory")
    if set(receipt["signed_receipt_required"]) != {"algorithm", "key_id", "signed_payload", "signature"}:
        raise ConformanceError("signed-receipt-shape")
    if receipt != FROZEN_APPEND_RECEIPT:
        raise ConformanceError("signed-receipt-contract-profile")
    if set(contract["projection_digest_excludes"]) != {"projection_digest", "projection_id", "derived_at"}:
        raise ConformanceError("projection-digest-preimage")
    if contract["stage_graph_validation"] != FROZEN_STAGE_GRAPH_VALIDATION:
        raise ConformanceError("frozen-stage-graph-validation")
    if contract["auto_retry_policy"] != FROZEN_AUTO_RETRY_POLICY:
        raise ConformanceError("frozen-auto-retry-policy")
    if contract["redesign_policy"] != FROZEN_REDESIGN_POLICY:
        raise ConformanceError("frozen-redesign-policy")
    if contract["gate_reevaluation"] != FROZEN_GATE_REEVALUATION:
        raise ConformanceError("frozen-gate-reevaluation-policy")
    if contract["lineage_boundaries"] != FROZEN_LINEAGE_BOUNDARIES:
        raise ConformanceError("frozen-lineage-boundaries")


def completion_digest_preimage(prior: dict, payload: dict) -> dict:
    """Stable replay state after completion, excluding its asserted digest."""
    return {
        "run_id": prior["run_id"],
        "status": "completed",
        "run_provenance": prior["run_provenance"],
        "trigger": prior["trigger"],
        "executor_identity": prior["executor_identity"],
        "run_ordinal": prior["run_ordinal"],
        "stage_graph": prior["stage_graph"],
        "lineage": prior["lineage"],
        "stage_states": prior["stage_states"],
        "artifact_refs": prior["artifact_refs"],
        "completed_at": payload["completed_at"],
        "terminal_stages_completed": payload["terminal_stages_completed"],
        "events_count": payload["total_event_count"],
    }


def compute_completion_digest(prior: dict, payload: dict) -> str:
    return digest(completion_digest_preimage(prior, payload))


_VISIBILITY_ORDER = {"public": 1, "project": 2, "restricted": 3}


def _visibility_strictness(visibility: str) -> int:
    return _VISIBILITY_ORDER.get(visibility, 1)


def _public_runtime_artifact(artifact_id: str, artifact_kind: str = "stage_output", run_id: str = "run-root", stage_id: str = "build", produced_by: str = "runner-conformance", origin_stage: str | None = None) -> dict:
    """Create a complete public RuntimeArtifact for test use."""
    return {
        "artifact_ref": {"artifact_id": artifact_id, "artifact_kind": artifact_kind},
        "origin_run": run_id,
        "origin_stage": origin_stage if origin_stage is not None else stage_id,
        "produced_by": produced_by,
        "source_artifacts": [],
        "visibility": "public",
        "visibility_resolution": {
            "resolution_id": f"res-{artifact_id}",
            "resolved_at": "2026-07-16T00:00:00Z",
            "contributors": [
                {"contributor_id": f"contrib-{artifact_id}", "contributor_kind": "source_artifact",
                 "contributor_ref": {"artifact_id": artifact_id, "artifact_kind": artifact_kind},
                 "asserted_visibility": "public", "authority": "test artifact",
                 "classification_evidence": [{"artifact_id": f"ev-{artifact_id}", "artifact_kind": "evidence"}]}
            ],
            "resolution_rule": "most_restrictive",
            "resolved_visibility": "public",
            "resolution_audit": {"contributor_count": 1, "restricted_count": 0, "project_count": 0, "public_count": 1, "applied_rule": "most_restrictive"},
        },
    }


def _require_runtime_artifact(value: dict, run_id: str, stage_id: str | None) -> dict:
    """Require a complete RuntimeArtifact. Reject ArtifactRef-only inputs and missing fields."""
    if not isinstance(value, dict):
        raise ConformanceError("visibility-not-runtime-artifact")

    required = ["artifact_ref", "origin_run", "produced_by", "source_artifacts", "visibility", "visibility_resolution"]
    missing = [f for f in required if f not in value]
    if missing:
        raise ConformanceError("visibility-missing-fields:" + ",".join(sorted(missing)))

    if "artifact_kind" not in value.get("artifact_ref", {}) or "artifact_id" not in value.get("artifact_ref", {}):
        raise ConformanceError("visibility-incomplete-artifact-ref")

    if value["origin_run"] != run_id:
        raise ConformanceError("visibility-origin-run-mismatch")
    if stage_id is not None and value.get("origin_stage") != stage_id:
        raise ConformanceError("visibility-origin-stage-mismatch")
    return copy.deepcopy(value)


def _compute_resolved_run_visibility(visibility_context: dict, runtime_artifacts: list) -> str:
    """Compute most-restrictive resolved_run_visibility. Reject missing visibility."""
    current = visibility_context.get("resolved_run_visibility")
    if current not in ("public", "project", "restricted"):
        raise ConformanceError("visibility-invalid-context-visibility")
    current_strictness = _visibility_strictness(current)
    for artifact in runtime_artifacts:
        artifact_visibility = artifact.get("visibility")
        if artifact_visibility not in ("public", "project", "restricted"):
            raise ConformanceError("visibility-invalid-value")
        strictness = _visibility_strictness(artifact_visibility)
        if strictness > current_strictness:
            current = artifact_visibility
            current_strictness = strictness
    return current


def _validate_runtime_artifact(artifact: dict, event_run_id: str, payload_stage_id: str) -> None:
    """Strict validation of a RuntimeArtifact. Rejects missing fields, defaults, and empty evidence."""
    # Check visibility is valid enum
    vis = artifact.get("visibility")
    if vis not in ("public", "project", "restricted"):
        raise ConformanceError("visibility-invalid-value")

    # Check visibility_resolution present and complete
    resolution = artifact.get("visibility_resolution")
    if not isinstance(resolution, dict):
        raise ConformanceError("visibility-missing-resolution")
    if not resolution.get("contributors"):
        raise ConformanceError("visibility-empty-contributors")

    # Check every contributor has non-empty classification_evidence
    for c in resolution["contributors"]:
        if not isinstance(c.get("classification_evidence"), list) or not c["classification_evidence"]:
            raise ConformanceError("visibility-no-evidence")
        av = c.get("asserted_visibility")
        if av not in ("public", "project", "restricted"):
            raise ConformanceError("visibility-invalid-value")
        if not isinstance(c.get("contributor_id"), str) or not c["contributor_id"].strip():
            raise ConformanceError("visibility-missing-contributor-id")

    # Check contributor_id uniqueness
    contributor_ids = [c["contributor_id"] for c in resolution["contributors"]]
    if len(contributor_ids) != len(set(contributor_ids)):
        raise ConformanceError("visibility-contributor-conflict")

    # Invariant 4: visibility MUST equal resolution resolved_visibility
    if resolution.get("resolved_visibility") != vis:
        raise ConformanceError("visibility-resolved-mismatch")

    # Check resolution_audit counts match contributors
    audit = resolution.get("resolution_audit", {})
    if audit.get("contributor_count") != len(resolution["contributors"]):
        raise ConformanceError("visibility-audit-counts-mismatch")
    restricted = sum(1 for c in resolution["contributors"] if c.get("asserted_visibility") == "restricted")
    project = sum(1 for c in resolution["contributors"] if c.get("asserted_visibility") == "project")
    public = sum(1 for c in resolution["contributors"] if c.get("asserted_visibility") == "public")
    if audit.get("restricted_count") != restricted or audit.get("project_count") != project or audit.get("public_count") != public:
        raise ConformanceError("visibility-audit-counts-mismatch")

    # Invariant 3: every source_artifact must have a matching contributor
    source_artifacts = artifact.get("source_artifacts", [])
    contributor_refs = {(c.get("contributor_ref", {}).get("artifact_id"), c.get("contributor_kind")) for c in resolution["contributors"] if c.get("contributor_kind") == "source_artifact"}
    for sa in source_artifacts:
        if (sa.get("artifact_id"), "source_artifact") not in contributor_refs:
            raise ConformanceError("visibility-source-omission")

    # Check no extra unknown fields at top level
    allowed = {"artifact_ref", "origin_run", "origin_stage", "produced_by", "source_artifacts", "visibility", "visibility_resolution"}
    extra = set(artifact.keys()) - allowed
    if extra:
        raise ConformanceError("visibility-extra-fields:" + ",".join(sorted(extra)))


def reduce_event(contract: dict, prior: dict | None, event_type: str, payload: dict, event_run_id: str = "run-root") -> dict:
    validate_payload(contract, event_type, payload)
    rule = contract["reducers"][event_type]
    status = None if prior is None else prior["status"]
    allowed = rule["allowed_status"]
    allowed_set = set(allowed) if isinstance(allowed, list) else {allowed}
    if status not in allowed_set:
        raise ConformanceError("reducer-prior-status")

    if event_type == "run.created":
        graph = copy.deepcopy(payload["stage_graph"])
        stage_states = {
            stage["stage_id"]: {
                "stage_id": stage["stage_id"],
                "name": stage["name"],
                "required": stage["required"],
                "status": "pending",
                "gates": copy.deepcopy(stage.get("gates", [])),
                "gate_decisions": {},
                "gate_decision_history": [],
                "artifacts_produced": [],
            }
            for stage in graph["stages"]
        }
        visibility_context = copy.deepcopy(payload["visibility_context"])
        return {
            "run_id": event_run_id,
            "status": "pending",
            "created_at": payload["created_at"],
            "started_at": None,
            "run_provenance": copy.deepcopy(payload["run_provenance"]),
            "trigger": payload["trigger"],
            "executor_identity": payload["executor_identity"],
            "run_ordinal": payload["run_ordinal"],
            "stage_graph": graph,
            "lineage": copy.deepcopy(payload.get("lineage")),
            "stage_states": stage_states,
            "current_stage_id": None,
            "visibility_context": visibility_context,
            "runtime_artifacts": [],
            "artifact_refs": [],
            "resolved_run_visibility": visibility_context["resolved_run_visibility"],
            "interventions": [],
            "audit_events": [],
            "child_actions": [],
            "events_count": 1,
        }

    next_state = copy.deepcopy(prior)

    def stage_for(stage_id: str) -> dict:
        stage = next_state["stage_states"].get(stage_id)
        if stage is None:
            raise ConformanceError("stage-reference")
        return stage

    def gate_for(stage: dict, gate_id: str) -> dict:
        for gate in stage["gates"]:
            if gate["gate_id"] == gate_id:
                return gate
        raise ConformanceError("gate-reference")

    def payload_decision_ids(items: list) -> set[str]:
        result = set()
        for item in items:
            if isinstance(item, str):
                result.add(item)
            elif isinstance(item, dict) and isinstance(item.get("decision_id"), str):
                result.add(item["decision_id"])
            else:
                raise ConformanceError("stage-complete-decision-reference")
        return result

    def validate_reevaluation(stage: dict, payload: dict) -> None:
        current = stage["gate_decisions"].get(payload["gate_id"])
        reference = payload.get("reevaluates_decision_id")
        if current is None:
            if reference is not None:
                raise ConformanceError("gate-reevaluation-without-prior")
            return
        if reference != current["decision_id"]:
            raise ConformanceError("gate-reevaluation-current-decision")
        if payload["decision_id"] == current["decision_id"]:
            raise ConformanceError("gate-reevaluation-new-decision-id")
        old_evidence = {canonical_bytes(item) for item in current["evidence"]}
        if not any(canonical_bytes(item) not in old_evidence for item in payload["evidence"]):
            raise ConformanceError("gate-reevaluation-new-evidence")

    if event_type == "run.started":
        if payload["executor_identity"] != next_state["executor_identity"]:
            raise ConformanceError("run-start-executor-identity")
        next_state["status"] = "active"
        next_state["started_at"] = payload["started_at"]
    elif event_type == "run.stage.started":
        stage = stage_for(payload["stage_id"])
        if stage["status"] != "pending":
            raise ConformanceError("stage-start-transition")
        incoming = [edge for edge in next_state["stage_graph"]["edges"] if edge["to"] == payload["stage_id"]]
        if not incoming and payload["stage_id"] not in next_state["stage_graph"]["entry_stages"]:
            raise ConformanceError("stage-entry-graph")
        for edge in incoming:
            predecessor_status = stage_for(edge["from"])["status"]
            condition = edge.get("condition", "always")
            allowed_predecessor = {
                "always": {"completed", "skipped"},
                "on_pass": {"completed"},
                "on_fail": {"failed"},
                "on_skip": {"skipped"},
            }.get(condition)
            if allowed_predecessor is None or predecessor_status not in allowed_predecessor:
                raise ConformanceError("stage-entry-predecessor")
        stage["status"] = "active"
        stage["started_at"] = payload["started_at"]
        stage["entry_evidence"] = copy.deepcopy(payload["entry_evidence"])
        next_state["current_stage_id"] = payload["stage_id"]
    elif event_type == "run.gate.evaluated":
        stage = stage_for(payload["stage_id"])
        if stage["status"] != "active":
            raise ConformanceError("gate-stage-active")
        gate = gate_for(stage, payload["gate_id"])
        if payload["outcome"] not in {"pass", "fail", "blocked", "inconclusive", "human_review_required"}:
            raise ConformanceError("gate-outcome")
        validate_reevaluation(stage, payload)
        decision = {
            "gate_id": payload["gate_id"],
            "decision_id": payload["decision_id"],
            "outcome": payload["outcome"],
            "execution_mode": payload["execution_mode"],
            "evaluated_at": payload["evaluated_at"],
            "evaluated_by": payload["evaluated_by"],
            "evidence": copy.deepcopy(payload["evidence"]),
            "degradation_note": payload.get("degradation_note"),
            "reevaluates_decision_id": payload.get("reevaluates_decision_id"),
        }
        if gate["required"] and decision["outcome"] == "pass" and not decision["evidence"]:
            raise ConformanceError("required-gate-evidence")
        stage["gate_decisions"][payload["gate_id"]] = decision
        stage["gate_decision_history"].append(copy.deepcopy(decision))
    elif event_type == "run.gate.blocked":
        stage = stage_for(payload["stage_id"])
        if stage["status"] != "active":
            raise ConformanceError("gate-stage-active")
        gate_for(stage, payload["gate_id"])
        if payload["outcome"] != "blocked":
            raise ConformanceError("gate-blocked-outcome")
        validate_reevaluation(stage, payload)
        decision = {
            "gate_id": payload["gate_id"],
            "decision_id": payload["decision_id"],
            "outcome": "blocked",
            "execution_mode": payload["execution_mode"],
            "evaluated_at": payload["evaluated_at"],
            "evaluated_by": payload["evaluated_by"],
            "evidence": copy.deepcopy(payload["evidence"]),
            "degradation_note": payload.get("degradation_note"),
            "blocked_reason": payload["blocked_reason"],
            "required_evidence": copy.deepcopy(payload["required_evidence"]),
            "reevaluates_decision_id": payload.get("reevaluates_decision_id"),
        }
        stage["gate_decisions"][payload["gate_id"]] = decision
        stage["gate_decision_history"].append(copy.deepcopy(decision))
    elif event_type == "run.gate.overridden":
        stage = stage_for(payload["stage_id"])
        if stage["status"] != "active":
            raise ConformanceError("gate-stage-active")
        gate = gate_for(stage, payload["gate_id"])
        if gate["required"]:
            raise ConformanceError("required-gate-no-override")
        if gate.get("allow_gate_override") is not True:
            raise ConformanceError("optional-gate-override-contract")
        if payload["outcome"] != "pass":
            raise ConformanceError("gate-override-outcome")
        original = stage["gate_decisions"].get(payload["gate_id"])
        if original is None or original["decision_id"] != payload["original_decision_id"]:
            raise ConformanceError("gate-override-original-decision")
        intervention = next((item for item in next_state["interventions"] if item["intervention_id"] == payload["authorizing_intervention_id"]), None)
        if intervention is None or intervention["intervention_type"] != "override_gate" or intervention["authorized_by"] != payload["authorized_by"] or intervention["reason"] != payload["override_reason"]:
            raise ConformanceError("gate-override-intervention")
        decision = {
            "gate_id": payload["gate_id"],
            "decision_id": payload["new_decision_id"],
            "outcome": payload["outcome"],
            "original_decision_id": payload["original_decision_id"],
            "execution_mode": payload["execution_mode"],
            "evaluated_at": payload["evaluated_at"],
            "evaluated_by": payload["evaluated_by"],
            "evidence": copy.deepcopy(payload["evidence"]),
            "degradation_note": payload.get("degradation_note"),
            "override_reason": payload["override_reason"],
            "authorized_by": payload["authorized_by"],
            "authorized_at": payload["authorized_at"],
        }
        stage["gate_decisions"][payload["gate_id"]] = decision
        stage["gate_decision_history"].append(copy.deepcopy(decision))
    elif event_type == "run.intervention":
        if payload["intervention_type"] not in {"skip_stage", "override_gate", "force_retry", "force_terminal", "provide_evidence"}:
            raise ConformanceError("intervention-type")
        if payload["authorized_by"] not in {"architect", "human"}:
            raise ConformanceError("intervention-authority")
        if payload["intervention_type"] == "skip_stage" and payload["authorized_by"] != "human":
            raise ConformanceError("skip-stage-human-authority")
        if any(item["intervention_id"] == payload["intervention_id"] for item in next_state["interventions"]):
            raise ConformanceError("intervention-identity")
        next_state["interventions"].append(copy.deepcopy(payload))
    elif event_type == "run.stage.skipped":
        stage = stage_for(payload["stage_id"])
        if stage["status"] != "pending":
            raise ConformanceError("stage-skip-transition")
        if stage["required"]:
            raise ConformanceError("required-stage-no-skip")
        if payload["authorized_by"] != "human":
            raise ConformanceError("skip-stage-human-authority")
        intervention = next((item for item in next_state["interventions"] if item["intervention_id"] == payload["authorizing_intervention_id"]), None)
        if intervention is None or intervention["intervention_type"] != "skip_stage" or intervention["authorized_by"] != "human" or intervention["reason"] != payload["reason"]:
            raise ConformanceError("stage-skip-intervention")
        stage["status"] = "skipped"
        stage["completed_at"] = payload["skipped_at"]
        stage["skip_reason"] = payload["reason"]
    elif event_type == "run.stage.completed":
        stage = stage_for(payload["stage_id"])
        if stage["status"] != "active":
            raise ConformanceError("stage-complete-transition")
        for gate in stage["gates"]:
            decision = stage["gate_decisions"].get(gate["gate_id"])
            if decision is None:
                raise ConformanceError("stage-complete-gates-evaluated")
            if gate["required"] and (decision["outcome"] != "pass" or not decision.get("evidence")):
                raise ConformanceError("required-gate-pass-evidence")
            if not gate["required"] and decision["outcome"] != "pass":
                raise ConformanceError("optional-gate-requires-pass-or-override")
        expected_decisions = {decision["decision_id"] for decision in stage["gate_decisions"].values()}
        if payload_decision_ids(payload["gate_decisions"]) != expected_decisions:
            raise ConformanceError("stage-complete-decision-reference")
        stage["status"] = "completed"
        stage["completed_at"] = payload["completed_at"]
        stage["artifacts_produced"].extend(copy.deepcopy(payload["artifacts_produced"]))
        runtime_artifacts = [_require_runtime_artifact(raw, event_run_id, payload["stage_id"]) for raw in payload["artifacts_produced"]]
        for artifact in runtime_artifacts:
            _validate_runtime_artifact(artifact, event_run_id, payload["stage_id"])
        next_state.setdefault("runtime_artifacts", []).extend(runtime_artifacts)
        next_state["artifact_refs"] = [artifact["artifact_ref"] for artifact in next_state["runtime_artifacts"]]
        if "visibility_context" in next_state:
            next_state["resolved_run_visibility"] = _compute_resolved_run_visibility(next_state["visibility_context"], next_state["runtime_artifacts"])
        next_state["current_stage_id"] = None
    elif event_type == "run.stage.failed":
        stage = stage_for(payload["stage_id"])
        if stage["status"] != "active":
            raise ConformanceError("stage-fail-transition")
        stage["status"] = "failed"
        stage["failed_at"] = payload["failed_at"]
        stage["error"] = copy.deepcopy(payload["error"])
        stage["failure_category"] = payload["failure_category"]
        stage["failure_is_transient"] = payload["failure_is_transient"]
        stage["failure_is_deterministic"] = payload["failure_is_deterministic"]
        stage["retry_eligible"] = payload["retry_eligible"]
        stage["artifacts_produced"].extend(copy.deepcopy(payload["artifacts_produced_before_failure"]))
        runtime_artifacts = [_require_runtime_artifact(raw, event_run_id, payload["stage_id"]) for raw in payload["artifacts_produced_before_failure"]]
        for artifact in runtime_artifacts:
            _validate_runtime_artifact(artifact, event_run_id, payload["stage_id"])
        next_state.setdefault("runtime_artifacts", []).extend(runtime_artifacts)
        next_state["artifact_refs"] = [artifact["artifact_ref"] for artifact in next_state["runtime_artifacts"]]
        if "visibility_context" in next_state:
            next_state["resolved_run_visibility"] = _compute_resolved_run_visibility(next_state["visibility_context"], next_state["runtime_artifacts"])
        next_state["status"] = "failed"
        next_state["failed_at"] = payload["failed_at"]
        next_state["failed_stage_id"] = payload["stage_id"]
        next_state["error"] = copy.deepcopy(payload["error"])
        next_state["failure_category"] = payload["failure_category"]
        next_state["failure_is_transient"] = payload["failure_is_transient"]
        next_state["failure_is_deterministic"] = payload["failure_is_deterministic"]
        next_state["retry_eligible"] = payload["retry_eligible"]
    elif event_type == "run.completed":
        terminal_stages = set(next_state["stage_graph"]["terminal_stages"])
        if set(payload["terminal_stages_completed"]) != terminal_stages:
            raise ConformanceError("run-completed-terminal-stage-list")
        if any(stage_for(stage_id)["status"] != "completed" for stage_id in terminal_stages):
            raise ConformanceError("run-completed-terminal-stages")
        for stage in next_state["stage_states"].values():
            if stage["required"] and stage["status"] != "completed":
                raise ConformanceError("run-completed-required-stage")
            for gate in stage["gates"]:
                if gate["required"]:
                    decision = stage["gate_decisions"].get(gate["gate_id"])
                    if decision is None or decision["outcome"] != "pass" or not decision.get("evidence"):
                        raise ConformanceError("run-completed-required-gate")
        if payload["total_event_count"] != prior["events_count"] + 1:
            raise ConformanceError("run-completed-event-count")
        if payload["final_projection_digest"] != compute_completion_digest(prior, payload):
            raise ConformanceError("run-completed-projection-digest")
        next_state["status"] = "completed"
        next_state["completed_at"] = payload["completed_at"]
        next_state["final_projection_digest"] = payload["final_projection_digest"]
        next_state["declared_total_event_count"] = payload["total_event_count"]
    elif event_type == "run.failed":
        failed_stage = stage_for(payload["failed_stage_id"])
        if failed_stage["status"] != "active":
            raise ConformanceError("run-failed-stage-active")
        failed_stage["status"] = "failed"
        failed_stage["failed_at"] = payload["failed_at"]
        failed_stage["error"] = copy.deepcopy(payload["error"])
        failed_stage["failure_category"] = payload["failure_category"]
        failed_stage["failure_is_transient"] = payload["failure_is_transient"]
        failed_stage["failure_is_deterministic"] = payload["failure_is_deterministic"]
        failed_stage["retry_eligible"] = payload["retry_eligible"]
        next_state["status"] = "failed"
        next_state["failed_at"] = payload["failed_at"]
        next_state["failed_stage_id"] = payload["failed_stage_id"]
        next_state["error"] = copy.deepcopy(payload["error"])
        next_state["failure_category"] = payload["failure_category"]
        next_state["failure_is_transient"] = payload["failure_is_transient"]
        next_state["failure_is_deterministic"] = payload["failure_is_deterministic"]
        next_state["retry_eligible"] = payload["retry_eligible"]
    elif event_type == "run.blocked":
        next_state["status"] = "blocked"
        next_state["blocked_at"] = payload["blocked_at"]
        next_state["blocked_reason"] = payload["blocked_reason"]
        next_state["resolution_paths"] = copy.deepcopy(payload["resolution_paths"])
        next_state["required_evidence"] = copy.deepcopy(payload["required_evidence"])
    elif event_type == "run.interrupted":
        if payload["last_event_order"] != prior["events_count"]:
            raise ConformanceError("run-interrupted-event-order")
        next_state["status"] = "interrupted"
        next_state["interrupted_at"] = payload["interrupted_at"]
        next_state["last_event_order"] = payload["last_event_order"]
        next_state["interruption_cause"] = payload["interruption_cause"]
        next_state["checkpoint_available"] = payload["checkpoint_available"]
    elif event_type == "run.terminated":
        if payload["from_status"] != status:
            raise ConformanceError("run-terminated-from-status")
        if payload["terminal_status"] not in {"failed", "blocked"}:
            raise ConformanceError("run-terminated-terminal-status")
        if payload["terminated_by"] not in {"architect", "human"}:
            raise ConformanceError("run-terminated-authority")
        next_state["status"] = payload["terminal_status"]
        next_state["terminated_at"] = payload["terminated_at"]
        next_state["terminated_by"] = payload["terminated_by"]
        next_state["termination_reason"] = payload["termination_reason"]
    elif event_type in {"run.retry.initiated", "run.resumed", "run.redesign"}:
        lineage = payload["lineage"]
        expected_kind = {"run.retry.initiated": "retry", "run.resumed": "resume", "run.redesign": "redesign"}[event_type]
        if lineage["lineage_kind"] != expected_kind:
            raise ConformanceError("lineage-action-kind")
        if lineage["parent_run_id"] != prior["run_id"] or lineage["parent_status"] != status:
            raise ConformanceError("lineage-action-parent")
        if not isinstance(payload["new_run_id"], str) or not payload["new_run_id"] or payload["new_run_id"] == prior["run_id"]:
            raise ConformanceError("lineage-action-child-id")
        if lineage["parent_boundary_event_id"] != prior.get("latest_event_id") or lineage["parent_boundary_event_type"] != prior.get("latest_event_type") or lineage["parent_boundary_event_order"] != prior.get("latest_event_order"):
            raise ConformanceError("lineage-action-boundary")
        if lineage["parent_boundary_event_type"] == "run.terminated":
            if prior.get("latest_terminal_status") is None:
                raise ConformanceError("lineage-boundary-terminal-status")
            validate_lineage(contract, lineage, prior["latest_terminal_status"])
        if payload["authorized_by"] not in ({"architect", "human", "system"} if event_type == "run.retry.initiated" else {"architect", "human"}):
            raise ConformanceError("lineage-action-authority")
        if event_type == "run.retry.initiated":
            if not (1 <= payload["current_retry_count"] <= payload["max_retries"] <= 3):
                raise ConformanceError("retry-count-bounds")
            if payload["failure_category"] != prior.get("failure_category"):
                raise ConformanceError("retry-failure-category")
            if payload["authorized_by"] == "system":
                required_flags = payload.get("failure_is_transient") is True and payload.get("failure_is_deterministic") is True
                parent_flags = prior.get("failure_is_transient") is True and prior.get("failure_is_deterministic") is True
                if payload["current_retry_count"] >= payload["max_retries"] or payload["retry_strategy"] != "full" or payload["failure_category"] != "command_failed" or not required_flags or not parent_flags:
                    raise ConformanceError("system-auto-retry-policy")
            elif "failure_is_transient" in payload or "failure_is_deterministic" in payload:
                raise ConformanceError("manual-retry-failure-flags-forbidden")
        elif event_type == "run.resumed":
            if payload["checkpoint_event_order"] > lineage["parent_boundary_event_order"] or prior.get("checkpoint_available") is not True:
                raise ConformanceError("resume-checkpoint-boundary")
        action = {"event_type": event_type, "new_run_id": payload["new_run_id"], "lineage": copy.deepcopy(lineage), "authorized_by": payload["authorized_by"]}
        if event_type == "run.retry.initiated":
            action.update({"retry_strategy": payload["retry_strategy"], "current_retry_count": payload["current_retry_count"], "max_retries": payload["max_retries"], "failure_category": payload["failure_category"]})
        elif event_type == "run.resumed":
            action.update({"checkpoint_event_order": payload["checkpoint_event_order"], "recovery_action": payload["recovery_action"]})
        else:
            action["revised_stage_graph"] = copy.deepcopy(payload["revised_stage_graph"])
            next_state["status"] = "failed"
            next_state["terminated_at"] = payload["authorized_at"]
            next_state["termination_reason"] = lineage["lineage_reason"]
        next_state["child_actions"].append(action)
        next_state["audit_events"].append({"event_type": event_type, "payload": copy.deepcopy(payload)})
    else:
        next_status = rule["next_status"]
        if next_status == "payload.terminal_status":
            next_state["status"] = payload["terminal_status"]
        elif next_status not in {"unchanged", status}:
            next_state["status"] = next_status
    next_state["events_count"] += 1
    return next_state


def projection_digest(projection: dict, excluded: list[str]) -> str:
    preimage = {key: value for key, value in projection.items() if key not in excluded}
    return digest(preimage)


def validate_lineage(contract: dict, lineage: dict, boundary_terminal_status: str | None = None) -> None:
    boundary_kinds = {
        "retry": "retry",
        "resume": "resume",
        "more_evidence": "more_evidence",
    }
    matches = []
    for key, expected in contract["lineage_boundaries"].items():
        expected_kind = expected.get("lineage_kind", boundary_kinds.get(key))
        if (
            lineage["lineage_kind"] == expected_kind
            and lineage["parent_status"] == expected["parent_status"]
            and lineage["parent_boundary_event_type"] == expected["parent_boundary_event_type"]
        ):
            matches.append(expected)
    if not matches:
        if lineage["lineage_kind"] not in {"retry", "resume", "more_evidence", "redesign"}:
            raise ConformanceError("lineage-kind")
        if not any(lineage["parent_status"] == item["parent_status"] for item in contract["lineage_boundaries"].values()):
            raise ConformanceError("lineage-parent-status")
        raise ConformanceError("lineage-boundary-type")
    expected = matches[0]
    required_terminal_status = expected.get("terminal_status")
    if boundary_terminal_status is not None and boundary_terminal_status != required_terminal_status:
        raise ConformanceError("lineage-boundary-terminal-status")
    if lineage["parent_boundary_event_type"] == "run.terminated" and boundary_terminal_status is None:
        if required_terminal_status != lineage["parent_status"]:
            raise ConformanceError("lineage-boundary-terminal-status")
    if not lineage["parent_boundary_event_id"] or lineage["parent_boundary_event_order"] < 1:
        raise ConformanceError("lineage-boundary-reference")


class MemoryAppendStore:
    """Executable model of the contract's append decision order."""

    def __init__(self, signing_key: bytes = b"runtime-state-conformance-key", contract: dict | None = None):
        self.events = []
        self.by_client_id = {}
        self.signing_key = signing_key
        self.verification_keys = {"conformance-key-1": signing_key}
        self.contract = copy.deepcopy(contract if contract is not None else load_fixture())
        self.projections = {}
        self.receipts_by_run = {}

    def _head(self, run_id: str):
        run_events = [event for event in self.events if event["run_id"] == run_id]
        if not run_events:
            return {"event_order": 0, "content_digest": ZERO_DIGEST}
        last = run_events[-1]
        return {"event_order": last["event_order"], "content_digest": last["content_digest"]}

    @property
    def head(self):
        return self._head("run-root")

    def _signed_receipt(self, payload: dict) -> dict:
        signature = hmac.new(self.signing_key, canonical_bytes(payload), hashlib.sha256).hexdigest()
        return {"algorithm": "HMAC-SHA256", "key_id": "conformance-key-1", "signed_payload": payload, "signature": signature}

    def _validate_request_envelope(self, request: dict) -> None:
        common = {"run_id", "event_type", "payload", "actor_role", "actor_identity", "trigger_artifact", "reason", "recommended_action", "expected_stream_head", "client_event_id", "prev_event_digest"}
        causation = {"causation_id", "causation_chain"} & set(request)
        if set(request) - (common | {"causation_id", "causation_chain"}) or not common.issubset(request) or len(causation) != 1:
            raise ConformanceError("append-request-shape")
        for field in ("run_id", "event_type", "actor_identity", "reason", "client_event_id"):
            require_string(request[field], "append-request-string")
        if request["event_type"] not in FROZEN_EVENT_TYPES:
            raise ConformanceError("append-request-event-type")
        if request["actor_role"] not in {"runner", "architect", "validator", "planner", "human"}:
            raise ConformanceError("append-request-actor-role")
        if request["recommended_action"] not in {"none", "retry", "resume", "intervention_required", "more_evidence", "escalate"}:
            raise ConformanceError("append-request-recommended-action")
        require_artifact_ref(request["trigger_artifact"])
        if not isinstance(request["expected_stream_head"], dict) or set(request["expected_stream_head"]) != {"event_order", "content_digest"}:
            raise ConformanceError("append-request-head-shape")
        require_integer(request["expected_stream_head"]["event_order"], 0, "append-request-head-order")
        require_digest(request["expected_stream_head"]["content_digest"])
        require_digest(request["prev_event_digest"])
        if "causation_id" in request:
            require_string(request["causation_id"], "append-request-causation")
            prior_ids = {event["event_id"] for event in self.events if event["run_id"] == request["run_id"]}
            if request["causation_id"] not in prior_ids:
                raise ConformanceError("append-request-causation")
        else:
            require_string_array(request["causation_chain"])
            prior_ids = {event["event_id"] for event in self.events if event["run_id"] == request["run_id"]}
            if any(event_id not in prior_ids for event_id in request["causation_chain"]):
                raise ConformanceError("append-request-causation")

    def append(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise ConformanceError("append-request-shape")
        client_id = request.get("client_event_id")
        existing = self.by_client_id.get(client_id)
        if existing is not None:
            if canonical_bytes(existing["request"]) == canonical_bytes(request):
                return copy.deepcopy(existing["receipt"])
            raise ConformanceError("divergent-duplicate")
        self._validate_request_envelope(request)
        current_head = self._head(request["run_id"])
        if request["expected_stream_head"] != current_head:
            raise StaleHeadError({
                "code": "stale_head",
                "current_stream_head": copy.deepcopy(current_head),
                "last_stored_receipt": copy.deepcopy(self.receipts_by_run.get(request["run_id"])),
            })
        if request["prev_event_digest"] != current_head["content_digest"]:
            raise ConformanceError("hash-chain-link")
        prior_projection = copy.deepcopy(self.projections.get(request["run_id"]))
        next_projection = reduce_event(self.contract, prior_projection, request["event_type"], request["payload"], event_run_id=request["run_id"])
        run_event_count = len([event for event in self.events if event["run_id"] == request["run_id"]])
        stored = copy.deepcopy(request)
        stored["event_id"] = "evt-" + str(len(self.events) + 1)
        stored["event_order"] = run_event_count + 1
        stored["schema_version"] = "0.9.0"
        stored["occurred_at"] = "2026-07-16T00:00:00Z"
        stored["prior_state"] = {} if prior_projection is None else prior_projection
        next_projection["latest_event_id"] = stored["event_id"]
        next_projection["latest_event_type"] = stored["event_type"]
        next_projection["latest_event_order"] = stored["event_order"]
        if stored["event_type"] == "run.terminated":
            next_projection["latest_terminal_status"] = stored["payload"]["terminal_status"]
        else:
            next_projection.pop("latest_terminal_status", None)
        stored["next_state"] = copy.deepcopy(next_projection)
        stored["content_digest"] = digest(stored)
        self.events.append(stored)
        self.projections[request["run_id"]] = copy.deepcopy(next_projection)
        new_head = self._head(request["run_id"])
        signed_payload = {"run_id": request["run_id"], **new_head}
        receipt = {
            "event_id": stored["event_id"],
            "event_order": stored["event_order"],
            "stored_content_digest": stored["content_digest"],
            "new_stream_head": new_head,
            "signed_receipt": self._signed_receipt(signed_payload),
        }
        self.by_client_id[client_id] = {"request": copy.deepcopy(request), "receipt": copy.deepcopy(receipt)}
        self.receipts_by_run[request["run_id"]] = copy.deepcopy(receipt)
        return receipt

    def verify_receipt(self, receipt: dict, expected_run_id: str) -> None:
        if set(receipt) != {"event_id", "event_order", "stored_content_digest", "new_stream_head", "signed_receipt"}:
            raise ConformanceError("signed-receipt-envelope-shape")
        signed = receipt["signed_receipt"]
        if set(signed) != {"algorithm", "key_id", "signed_payload", "signature"}:
            raise ConformanceError("signed-receipt-shape")
        if signed["algorithm"] != "HMAC-SHA256":
            raise ConformanceError("signed-receipt-algorithm")
        if set(receipt["new_stream_head"]) != {"event_order", "content_digest"}:
            raise ConformanceError("signed-receipt-head-shape")
        if set(signed["signed_payload"]) != {"run_id", "event_order", "content_digest"}:
            raise ConformanceError("signed-receipt-payload-shape")
        require_string(receipt["event_id"], "signed-receipt-event-id")
        require_integer(receipt["event_order"], 1, "signed-receipt-event-order")
        require_digest(receipt["stored_content_digest"])
        require_integer(receipt["new_stream_head"]["event_order"], 1, "signed-receipt-head-order")
        require_digest(receipt["new_stream_head"]["content_digest"])
        require_string(signed["key_id"], "signed-receipt-key-id")
        require_string(signed["signature"], "signed-receipt-signature")
        if not re.fullmatch(r"[0-9a-f]{64}", signed["signature"]):
            raise ConformanceError("signed-receipt-signature")
        verification_key = self.verification_keys.get(signed["key_id"])
        if verification_key is None:
            raise ConformanceError("signed-receipt-key-id")
        require_string(signed["signed_payload"]["run_id"], "signed-receipt-run-id")
        require_integer(signed["signed_payload"]["event_order"], 1, "signed-receipt-payload-order")
        require_digest(signed["signed_payload"]["content_digest"])
        expected = hmac.new(verification_key, canonical_bytes(signed["signed_payload"]), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signed["signature"]):
            raise ConformanceError("signed-receipt-verification")
        if signed["signed_payload"]["run_id"] != expected_run_id:
            raise ConformanceError("signed-receipt-run-context")
        if signed["signed_payload"]["event_order"] != receipt["new_stream_head"]["event_order"]:
            raise ConformanceError("signed-receipt-head-binding")
        if signed["signed_payload"]["content_digest"] != receipt["new_stream_head"]["content_digest"]:
            raise ConformanceError("signed-receipt-head-binding")
        if receipt["event_order"] != receipt["new_stream_head"]["event_order"]:
            raise ConformanceError("signed-receipt-event-order-binding")
        if receipt["stored_content_digest"] != receipt["new_stream_head"]["content_digest"]:
            raise ConformanceError("signed-receipt-content-digest-binding")

    def verify_tail(self, trusted_receipt: dict, expected_run_id: str) -> None:
        self.verify_stream(expected_run_id, trusted_receipt)

    def verify_stream(self, run_id: str, trusted_receipt: dict | None = None) -> dict | None:
        run_events = sorted(
            (event for event in self.events if event["run_id"] == run_id),
            key=lambda event: event["event_order"],
        )
        replayed = None
        previous_digest = ZERO_DIGEST
        for expected_order, event in enumerate(run_events, 1):
            if type(event.get("event_order")) is not int or event["event_order"] != expected_order:
                raise ConformanceError("stream-event-order-gap")
            if event.get("prev_event_digest") != previous_digest:
                raise ConformanceError("stream-prev-event-digest-link")
            digest_preimage = copy.deepcopy(event)
            stored_digest = digest_preimage.pop("content_digest", None)
            if stored_digest != digest(digest_preimage):
                raise ConformanceError("stream-content-digest")
            expected_prior = {} if replayed is None else replayed
            if event.get("prior_state") != expected_prior:
                raise ConformanceError("stream-prior-state-replay")
            replayed = reduce_event(
                self.contract,
                replayed,
                event["event_type"],
                copy.deepcopy(event["payload"]),
                event_run_id=run_id,
            )
            replayed["latest_event_id"] = event["event_id"]
            replayed["latest_event_type"] = event["event_type"]
            replayed["latest_event_order"] = event["event_order"]
            if event["event_type"] == "run.terminated":
                replayed["latest_terminal_status"] = event["payload"]["terminal_status"]
            else:
                replayed.pop("latest_terminal_status", None)
            if event.get("next_state") != replayed:
                raise ConformanceError("stream-next-state-replay")
            previous_digest = stored_digest
        rebuilt_head = {
            "event_order": len(run_events),
            "content_digest": previous_digest,
        }
        if trusted_receipt is not None:
            self.verify_receipt(trusted_receipt, run_id)
            if rebuilt_head != trusted_receipt["new_stream_head"]:
                raise ConformanceError("tail-truncation")
        return replayed


def genesis_payload(contract: dict, lineage=None, event_run_id: str = "run-root") -> dict:
    payload = {
        "run_provenance": copy.deepcopy(contract["run_provenance"]),
        "trigger": "api",
        "executor_identity": "runner-conformance",
        "run_ordinal": 2 if lineage else 1,
        "created_at": "2026-07-16T00:00:00Z",
        "stage_graph": copy.deepcopy(contract["stage_graph"]),
        "visibility_context": {
            "trigger_visibility": {
                "contributor_id": "conformance-trigger-001",
                "contributor_kind": "trigger_provenance",
                "contributor_ref": {"artifact_id": "conformance-api-trigger", "artifact_kind": "request_artifact"},
                "asserted_visibility": "public",
                "authority": "Conformance test trigger carries public visibility.",
                "classification_evidence": [{"artifact_id": "conformance-api-trigger", "artifact_kind": "request_artifact"}]
            },
            "policy_contributors": [
                {
                    "contributor_id": "conformance-policy-001",
                    "contributor_kind": "project_policy",
                    "contributor_ref": {"artifact_id": "railyard-conformance-policy", "artifact_kind": "policy"},
                    "asserted_visibility": "public",
                    "authority": "Conformance policy permits public disclosure.",
                    "classification_evidence": [{"artifact_id": "railyard-conformance-policy", "artifact_kind": "policy"}]
                }
            ],
            "contract_contributors": [
                {
                    "contributor_id": "conformance-contract-001",
                    "contributor_kind": "governing_contract",
                    "contributor_ref": {"artifact_id": "runtime-state-contract", "artifact_kind": "contract", "artifact_version": "0.9.0"},
                    "asserted_visibility": "public",
                    "authority": "Governing contract declares public visibility.",
                    "classification_evidence": [{"artifact_id": "runtime-state-contract", "artifact_kind": "contract", "artifact_version": "0.9.0"}]
                }
            ],
            "run_visibility_resolution": {
                "resolution_id": "conformance-resolution-run-001",
                "resolved_at": "2026-07-16T00:00:00Z",
                "contributors": [
                    {
                        "contributor_id": "conformance-trigger-001",
                        "contributor_kind": "trigger_provenance",
                        "contributor_ref": {"artifact_id": "conformance-api-trigger", "artifact_kind": "request_artifact"},
                        "asserted_visibility": "public",
                        "authority": "Conformance test trigger carries public visibility.",
                        "classification_evidence": [{"artifact_id": "conformance-api-trigger", "artifact_kind": "request_artifact"}]
                    },
                    {
                        "contributor_id": "conformance-policy-001",
                        "contributor_kind": "project_policy",
                        "contributor_ref": {"artifact_id": "railyard-conformance-policy", "artifact_kind": "policy"},
                        "asserted_visibility": "public",
                        "authority": "Conformance policy permits public disclosure.",
                        "classification_evidence": [{"artifact_id": "railyard-conformance-policy", "artifact_kind": "policy"}]
                    },
                    {
                        "contributor_id": "conformance-contract-001",
                        "contributor_kind": "governing_contract",
                        "contributor_ref": {"artifact_id": "runtime-state-contract", "artifact_kind": "contract", "artifact_version": "0.9.0"},
                        "asserted_visibility": "public",
                        "authority": "Governing contract declares public visibility.",
                        "classification_evidence": [{"artifact_id": "runtime-state-contract", "artifact_kind": "contract", "artifact_version": "0.9.0"}]
                    }
                ],
                "resolution_rule": "most_restrictive",
                "resolved_visibility": "public",
                "resolution_audit": {"contributor_count": 3, "restricted_count": 0, "project_count": 0, "public_count": 3, "applied_rule": "most_restrictive"}
            },
            "resolved_run_visibility": "public"
        },
    }
    if lineage:
        payload["lineage"] = copy.deepcopy(lineage)
    return payload


def append_request(client_id="client-1", reason="create") -> dict:
    contract = load_fixture()
    return {
        "run_id": "run-root",
        "event_type": "run.created",
        "payload": genesis_payload(contract),
        "causation_chain": [],
        "actor_role": "runner",
        "actor_identity": "runner-conformance",
        "trigger_artifact": {"artifact_id": "request-1", "artifact_kind": "request_artifact"},
        "reason": reason,
        "recommended_action": "none",
        "expected_stream_head": {"event_order": 0, "content_digest": ZERO_DIGEST},
        "client_event_id": client_id,
        "prev_event_digest": ZERO_DIGEST,
    }


def started_request(receipt: dict, client_id="client-started") -> dict:
    request = append_request(client_id, "start")
    request["event_type"] = "run.started"
    request["payload"] = {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"}
    request["expected_stream_head"] = copy.deepcopy(receipt["new_stream_head"])
    request["prev_event_digest"] = receipt["new_stream_head"]["content_digest"]
    return request


def resolve_most_restrictive(contributors):
    allowed = {"public", "project", "restricted"}
    resolved = "public"
    for c in contributors:
        av = c.get("asserted_visibility", "public")
        if av not in allowed:
            raise ValueError(f"invalid visibility: {av}")
        if av == "restricted":
            return "restricted"
        if av == "project":
            resolved = "project"
    return resolved


class RuntimeStateContractTests(unittest.TestCase):

    def pay_gate(self, state, stage_id, gate_id, decision_id, outcome):
        """Helper to evaluate a gate and return updated state."""
        decision = {
            "stage_id": stage_id, "gate_id": gate_id, "decision_id": decision_id,
            "outcome": outcome, "execution_mode": "full",
            "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1",
            "evidence": [{"artifact_id": f"report-{decision_id}", "artifact_kind": "validation_report"}]
        }
        new_state = reduce_event(self.contract, state, "run.gate.evaluated", decision)
        state.clear()
        state.update(new_state)
    def setUp(self):
        self.contract = load_fixture()

    def test_contract_taxonomy_schema_reducer_and_payload_sufficiency(self):
        validate_contract_shape(self.contract)

    def test_frozen_surface_hashes_reject_single_and_coordinated_drift(self):
        contract_bytes = CONTRACT.read_bytes()
        fixture_bytes = FIXTURE.read_bytes()
        validate_frozen_surface_bytes(contract_bytes, fixture_bytes)
        candidates = (
            (contract_bytes + b"\n", fixture_bytes),
            (contract_bytes, fixture_bytes + b"\n"),
            (contract_bytes.replace(b"0.8.2", b"0.8.3", 1), fixture_bytes.replace(b"0.8.2", b"0.8.3", 1)),
        )
        for candidate_contract, candidate_fixture in candidates:
            with self.subTest(candidate=candidates.index((candidate_contract, candidate_fixture))), self.assertRaises(ConformanceError):
                validate_frozen_surface_bytes(candidate_contract, candidate_fixture)

    def test_taxonomy_negative_mutations_fail(self):
        for target in ("event_schemas", "reducers"):
            with self.subTest(target=target):
                mutated = copy.deepcopy(self.contract)
                mutated[target].pop("run.created")
                with self.assertRaises(ConformanceError):
                    validate_contract_shape(mutated)

    def test_reducer_payload_sufficiency_negative_mutation_fails(self):
        mutated = copy.deepcopy(self.contract)
        mutated["event_schemas"]["run.created"]["required"].remove("stage_graph")
        with self.assertRaisesRegex(ConformanceError, "frozen-payload-schema-crosswalk"):
            validate_contract_shape(mutated)

    def test_exhaustive_payload_schema_mutation_audit(self):
        for event_type, expected in FROZEN_EVENT_SCHEMAS.items():
            for field in expected["required"]:
                with self.subTest(event_type=event_type, operation="remove-required", field=field):
                    mutated = copy.deepcopy(self.contract)
                    mutated["event_schemas"][event_type]["required"].remove(field)
                    with self.assertRaisesRegex(ConformanceError, "frozen-payload-schema-crosswalk"):
                        validate_contract_shape(mutated)
                with self.subTest(event_type=event_type, operation="replace-required", field=field):
                    mutated = copy.deepcopy(self.contract)
                    index = mutated["event_schemas"][event_type]["required"].index(field)
                    mutated["event_schemas"][event_type]["required"][index] = "bogus_" + field
                    with self.assertRaisesRegex(ConformanceError, "frozen-payload-schema-crosswalk"):
                        validate_contract_shape(mutated)
            with self.subTest(event_type=event_type, operation="add-required"):
                mutated = copy.deepcopy(self.contract)
                mutated["event_schemas"][event_type]["required"].append("bogus_required")
                with self.assertRaisesRegex(ConformanceError, "frozen-payload-schema-crosswalk"):
                    validate_contract_shape(mutated)

            for field in expected.get("conditional", []):
                with self.subTest(event_type=event_type, operation="remove-conditional", field=field):
                    mutated = copy.deepcopy(self.contract)
                    mutated["event_schemas"][event_type]["conditional"].remove(field)
                    with self.assertRaisesRegex(ConformanceError, "frozen-payload-schema-crosswalk"):
                        validate_contract_shape(mutated)
                with self.subTest(event_type=event_type, operation="replace-conditional", field=field):
                    mutated = copy.deepcopy(self.contract)
                    index = mutated["event_schemas"][event_type]["conditional"].index(field)
                    mutated["event_schemas"][event_type]["conditional"][index] = "bogus_" + field
                    with self.assertRaisesRegex(ConformanceError, "frozen-payload-schema-crosswalk"):
                        validate_contract_shape(mutated)
            with self.subTest(event_type=event_type, operation="add-conditional"):
                mutated = copy.deepcopy(self.contract)
                mutated["event_schemas"][event_type].setdefault("conditional", []).append("bogus_conditional")
                with self.assertRaisesRegex(ConformanceError, "frozen-payload-schema-crosswalk"):
                    validate_contract_shape(mutated)

    def test_exhaustive_reducer_mutation_audit(self):
        for event_type, expected in FROZEN_REDUCERS.items():
            for property_name in ("allowed_status", "next_status"):
                with self.subTest(event_type=event_type, operation="change-transition", property=property_name):
                    mutated = copy.deepcopy(self.contract)
                    mutated["reducers"][event_type][property_name] = "bogus_" + property_name
                    with self.assertRaisesRegex(ConformanceError, "frozen-reducer-crosswalk"):
                        validate_contract_shape(mutated)
                with self.subTest(event_type=event_type, operation="remove-transition", property=property_name):
                    mutated = copy.deepcopy(self.contract)
                    mutated["reducers"][event_type].pop(property_name)
                    with self.assertRaisesRegex(ConformanceError, "frozen-reducer-crosswalk"):
                        validate_contract_shape(mutated)
                with self.subTest(event_type=event_type, operation="expand-transition", property=property_name):
                    mutated = copy.deepcopy(self.contract)
                    original = mutated["reducers"][event_type][property_name]
                    mutated["reducers"][event_type][property_name] = [original, "bogus_transition"]
                    with self.assertRaisesRegex(ConformanceError, "frozen-reducer-crosswalk"):
                        validate_contract_shape(mutated)
            for field in expected["reads"]:
                with self.subTest(event_type=event_type, operation="remove-read", field=field):
                    mutated = copy.deepcopy(self.contract)
                    mutated["reducers"][event_type]["reads"].remove(field)
                    with self.assertRaisesRegex(ConformanceError, "frozen-reducer-crosswalk"):
                        validate_contract_shape(mutated)
                with self.subTest(event_type=event_type, operation="replace-read", field=field):
                    mutated = copy.deepcopy(self.contract)
                    index = mutated["reducers"][event_type]["reads"].index(field)
                    mutated["reducers"][event_type]["reads"][index] = "bogus_" + field
                    with self.assertRaisesRegex(ConformanceError, "frozen-reducer-crosswalk"):
                        validate_contract_shape(mutated)
            with self.subTest(event_type=event_type, operation="add-read"):
                mutated = copy.deepcopy(self.contract)
                mutated["reducers"][event_type]["reads"].append("bogus_read")
                with self.assertRaisesRegex(ConformanceError, "frozen-reducer-crosswalk"):
                    validate_contract_shape(mutated)
            with self.subTest(event_type=event_type, operation="add-transition-property"):
                mutated = copy.deepcopy(self.contract)
                mutated["reducers"][event_type]["bogus_transition_property"] = "active"
                with self.assertRaisesRegex(ConformanceError, "frozen-reducer-crosswalk"):
                    validate_contract_shape(mutated)

    def test_exhaustive_coordinated_schema_and_read_mutations_fail(self):
        for event_type, expected in FROZEN_EVENT_SCHEMAS.items():
            for field in expected["required"]:
                if field not in FROZEN_REDUCERS[event_type]["reads"]:
                    continue
                with self.subTest(event_type=event_type, field=field):
                    mutated = copy.deepcopy(self.contract)
                    schema_index = mutated["event_schemas"][event_type]["required"].index(field)
                    read_index = mutated["reducers"][event_type]["reads"].index(field)
                    mutated["event_schemas"][event_type]["required"][schema_index] = "bogus_" + field
                    mutated["reducers"][event_type]["reads"][read_index] = "bogus_" + field
                    with self.assertRaises(ConformanceError):
                        validate_contract_shape(mutated)

    def test_validator_reported_mutations_are_rejected(self):
        mutations = []
        failed_next = copy.deepcopy(self.contract)
        failed_next["reducers"]["run.failed"]["next_status"] = "completed"
        mutations.append(failed_next)
        interrupted_next = copy.deepcopy(self.contract)
        interrupted_next["reducers"]["run.interrupted"]["next_status"] = "active"
        mutations.append(interrupted_next)
        coordinated_field = copy.deepcopy(self.contract)
        required_index = coordinated_field["event_schemas"]["run.failed"]["required"].index("failed_at")
        read_index = coordinated_field["reducers"]["run.failed"]["reads"].index("failed_at")
        coordinated_field["event_schemas"]["run.failed"]["required"][required_index] = "bogus_at"
        coordinated_field["reducers"]["run.failed"]["reads"][read_index] = "bogus_at"
        mutations.append(coordinated_field)
        for mutated in mutations:
            with self.subTest(mutation=mutations.index(mutated)), self.assertRaises(ConformanceError):
                validate_contract_shape(mutated)

    def test_system081_policy_surface_mutations_are_rejected(self):
        mutations = []
        for section, field in (
            ("stage_graph_validation", "requires_acyclic_graph"),
            ("auto_retry_policy", "required_retry_strategy"),
            ("redesign_policy", "resulting_parent_status"),
            ("gate_reevaluation", "requires_new_evidence"),
        ):
            mutated = copy.deepcopy(self.contract)
            mutated[section][field] = "mutated"
            mutations.append(mutated)
        for mutated in mutations:
            with self.subTest(mutation=mutations.index(mutated)), self.assertRaises(ConformanceError):
                validate_contract_shape(mutated)

    def test_distinct_created_pending_then_started_active(self):
        pending = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["started_at"])
        self.assertEqual(set(pending["stage_states"]), {"build", "publish"})
        active = reduce_event(self.contract, pending, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        self.assertEqual(active["status"], "active")

    def test_behavioral_run_terminal_and_interrupted_transitions(self):
        cases = {
            "run.failed": ({"failed_at": "2026-07-16T00:01:00Z", "failed_stage_id": "build", "error": {"code": "E", "message": "failed"}, "failure_category": "command_failed", "failure_is_transient": False, "failure_is_deterministic": True, "retry_eligible": False}, "failed"),
            "run.blocked": ({"blocked_at": "2026-07-16T00:01:00Z", "blocked_reason": "missing evidence", "resolution_paths": ["more_evidence"], "required_evidence": []}, "blocked"),
            "run.interrupted": ({"interrupted_at": "2026-07-16T00:01:00Z", "last_event_order": 2, "interruption_cause": "session_lost", "checkpoint_available": True}, "interrupted"),
        }
        for event_type, (payload, expected_status) in cases.items():
            with self.subTest(event_type=event_type):
                pending = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
                active = reduce_event(self.contract, pending, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
                if event_type == "run.failed":
                    active = reduce_event(self.contract, active, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
                result = reduce_event(self.contract, active, event_type, payload)
                self.assertEqual(result["status"], expected_status)
                for key, value in payload.items():
                    if key in {"terminal_stages_completed", "total_event_count"}:
                        continue
                    self.assertEqual(result.get(key), value)
                with self.assertRaisesRegex(ConformanceError, "reducer-prior-status"):
                    reduce_event(self.contract, pending, event_type, payload)

    def test_behavioral_completed_run_requires_and_applies_full_stage_state(self):
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        with self.assertRaisesRegex(ConformanceError, "stage-entry-predecessor"):
            reduce_event(self.contract, state, "run.stage.started", {"stage_id": "publish", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        decision = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-build", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [{"artifact_id": "report-1", "artifact_kind": "validation_report"}]}
        state = reduce_event(self.contract, state, "run.gate.evaluated", decision)
        state = reduce_event(self.contract, state, "run.stage.completed", {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-build"], "artifacts_produced": []})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "publish", "started_at": "2026-07-16T00:00:05Z", "entry_evidence": []})
        state = reduce_event(self.contract, state, "run.stage.completed", {"stage_id": "publish", "completed_at": "2026-07-16T00:00:06Z", "gate_decisions": [], "artifacts_produced": [_public_runtime_artifact("release-1", stage_id="publish")]})
        completed = {"completed_at": "2026-07-16T00:00:07Z", "terminal_stages_completed": ["publish"], "final_projection_digest": ZERO_DIGEST, "total_event_count": 8}
        completed["final_projection_digest"] = compute_completion_digest(state, completed)
        state = reduce_event(self.contract, state, "run.completed", completed)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["completed_at"], completed["completed_at"])
        self.assertEqual(state["final_projection_digest"], completed["final_projection_digest"])
        self.assertIn({"artifact_id": "release-1", "artifact_kind": "stage_output"}, state["artifact_refs"])

    def test_run_completed_rejects_incomplete_or_inconsistent_terminal_state(self):
        pending = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        active = reduce_event(self.contract, pending, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        base = {"completed_at": "2026-07-16T00:00:07Z", "terminal_stages_completed": ["publish"], "final_projection_digest": "sha256:" + ("1" * 64), "total_event_count": active["events_count"] + 1}
        with self.assertRaisesRegex(ConformanceError, "run-completed-terminal-stages"):
            reduce_event(self.contract, active, "run.completed", base)
        wrong_list = copy.deepcopy(base)
        wrong_list["terminal_stages_completed"] = ["build"]
        with self.assertRaisesRegex(ConformanceError, "run-completed-terminal-stage-list"):
            reduce_event(self.contract, active, "run.completed", wrong_list)
        forged = copy.deepcopy(active)
        forged["stage_states"]["publish"]["status"] = "completed"
        with self.assertRaisesRegex(ConformanceError, "run-completed-required-stage"):
            reduce_event(self.contract, forged, "run.completed", base)

    def test_stage_failed_requires_active_stage_and_applies_full_effect(self):
        pending = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        active = reduce_event(self.contract, pending, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        payload = {"stage_id": "build", "failed_at": "2026-07-16T00:00:03Z", "error": {"code": "E_BUILD", "message": "build failed"}, "failure_category": "command_failed", "failure_is_transient": True, "failure_is_deterministic": True, "artifacts_produced_before_failure": [_public_runtime_artifact("partial-1")], "retry_eligible": True}
        with self.assertRaisesRegex(ConformanceError, "stage-fail-transition"):
            reduce_event(self.contract, active, "run.stage.failed", payload)
        active = reduce_event(self.contract, active, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        failed = reduce_event(self.contract, active, "run.stage.failed", payload)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["stage_states"]["build"]["status"], "failed")
        self.assertEqual(failed["stage_states"]["build"]["error"], payload["error"])
        self.assertEqual(failed["failure_category"], "command_failed")
        self.assertIn({"artifact_id": "partial-1", "artifact_kind": "stage_output"}, failed["artifact_refs"])
        missing_stage = copy.deepcopy(payload)
        missing_stage["stage_id"] = "missing"
        with self.assertRaisesRegex(ConformanceError, "stage-reference"):
            reduce_event(self.contract, active, "run.stage.failed", missing_stage)

    def test_run_terminated_enforces_from_status_terminal_domain_and_effect(self):
        pending = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        payload = {"terminated_at": "2026-07-16T00:00:02Z", "terminated_by": "human", "termination_reason": "stop", "from_status": "pending", "terminal_status": "failed"}
        terminated = reduce_event(self.contract, pending, "run.terminated", payload)
        self.assertEqual(terminated["status"], "failed")
        self.assertEqual(terminated["termination_reason"], "stop")
        mismatch = copy.deepcopy(payload)
        mismatch["from_status"] = "active"
        with self.assertRaisesRegex(ConformanceError, "run-terminated-from-status"):
            reduce_event(self.contract, pending, "run.terminated", mismatch)
        completed_target = copy.deepcopy(payload)
        completed_target["terminal_status"] = "completed"
        with self.assertRaisesRegex(ConformanceError, "payload-enum-terminal_status"):
            reduce_event(self.contract, pending, "run.terminated", completed_target)
        wrong_authority = copy.deepcopy(payload)
        wrong_authority["terminated_by"] = "runner"
        with self.assertRaisesRegex(ConformanceError, "run-terminated-authority"):
            reduce_event(self.contract, pending, "run.terminated", wrong_authority)
        active = reduce_event(self.contract, pending, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        active_payload = copy.deepcopy(payload)
        active_payload["from_status"] = "active"
        active_payload["terminal_status"] = "blocked"
        self.assertEqual(reduce_event(self.contract, active, "run.terminated", active_payload)["status"], "blocked")
        interrupted = reduce_event(self.contract, active, "run.interrupted", {"interrupted_at": "2026-07-16T00:00:02Z", "last_event_order": active["events_count"], "interruption_cause": "session_lost", "checkpoint_available": True})
        interrupted_payload = copy.deepcopy(payload)
        interrupted_payload["from_status"] = "interrupted"
        self.assertEqual(reduce_event(self.contract, interrupted, "run.terminated", interrupted_payload)["status"], "failed")

    def test_gate_blocked_skip_intervention_and_optional_override_effects(self):
        pending = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        active = reduce_event(self.contract, pending, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        active_build = reduce_event(self.contract, active, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        blocked_payload = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-block", "outcome": "blocked", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [], "blocked_reason": "missing evidence", "required_evidence": []}
        blocked_gate = reduce_event(self.contract, active_build, "run.gate.blocked", blocked_payload)
        self.assertEqual(blocked_gate["stage_states"]["build"]["gate_decisions"]["gate-required"]["outcome"], "blocked")
        wrong_outcome = copy.deepcopy(blocked_payload)
        wrong_outcome["outcome"] = "pass"
        with self.assertRaisesRegex(ConformanceError, "gate-blocked-outcome"):
            reduce_event(self.contract, active_build, "run.gate.blocked", wrong_outcome)

        skip_intervention = {"intervention_id": "i-skip", "intervention_type": "skip_stage", "authorized_by": "human", "reason": "optional stage omitted", "evidence": []}
        skip_state = reduce_event(self.contract, active, "run.intervention", skip_intervention)
        skipped = reduce_event(self.contract, skip_state, "run.stage.skipped", {"stage_id": "publish", "skipped_at": "2026-07-16T00:00:03Z", "authorized_by": "human", "reason": "optional stage omitted", "authorizing_intervention_id": "i-skip"})
        self.assertEqual(skipped["stage_states"]["publish"]["status"], "skipped")

        custom = genesis_payload(self.contract)
        custom["stage_graph"]["stages"][0]["gates"][0]["required"] = False
        custom["stage_graph"]["stages"][0]["gates"][0]["allow_gate_override"] = True
        override_state = reduce_event(self.contract, None, "run.created", custom)
        override_state = reduce_event(self.contract, override_state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        override_state = reduce_event(self.contract, override_state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        override_state = reduce_event(self.contract, override_state, "run.gate.evaluated", {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-fail", "outcome": "fail", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": []})
        with self.assertRaisesRegex(ConformanceError, "optional-gate-requires-pass-or-override"):
            reduce_event(self.contract, override_state, "run.stage.completed", {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-fail"], "artifacts_produced": []})
        override_state = reduce_event(self.contract, override_state, "run.intervention", {"intervention_id": "i-override", "intervention_type": "override_gate", "authorized_by": "architect", "reason": "contract allows", "evidence": []})
        override_payload = {"stage_id": "build", "gate_id": "gate-required", "new_decision_id": "d-override", "original_decision_id": "d-fail", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:04Z", "evaluated_by": "architect", "evidence": [], "override_reason": "contract allows", "authorized_by": "architect", "authorized_at": "2026-07-16T00:00:04Z", "authorizing_intervention_id": "i-override"}
        wrong_override = copy.deepcopy(override_payload)
        wrong_override["outcome"] = "fail"
        with self.assertRaisesRegex(ConformanceError, "gate-override-outcome"):
            reduce_event(self.contract, override_state, "run.gate.overridden", wrong_override)
        overridden = reduce_event(self.contract, override_state, "run.gate.overridden", override_payload)
        self.assertEqual(overridden["stage_states"]["build"]["gate_decisions"]["gate-required"]["decision_id"], "d-override")
        self.assertEqual(len(overridden["stage_states"]["build"]["gate_decision_history"]), 2)

    def test_child_genesis_contains_reducible_lineage(self):
        lineage = {
            "parent_run_id": "run-parent",
            "lineage_kind": "resume",
            "lineage_reason": "Continue from checkpoint.",
            "parent_status": "interrupted",
            "parent_boundary_event_id": "evt-parent-7",
            "parent_boundary_event_type": "run.interrupted",
            "parent_boundary_event_order": 7,
        }
        validate_lineage(self.contract, lineage)
        child = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract, lineage), event_run_id="run-child")
        self.assertEqual(child["run_id"], "run-child")
        self.assertEqual(child["lineage"], lineage)
        self.assertEqual(child["run_provenance"], self.contract["run_provenance"])
        self.assertEqual(child["stage_graph"], self.contract["stage_graph"])

    def test_lineage_accepts_terminal_and_interrupted_boundaries(self):
        default_kinds = {"retry": "retry", "resume": "resume", "more_evidence": "more_evidence"}
        self.assertEqual(len(self.contract["lineage_boundaries"]), 11)
        for key, boundary in FROZEN_LINEAGE_BOUNDARIES.items():
            kind = boundary["lineage_kind"] if "lineage_kind" in boundary else default_kinds[key]
            lineage = {"parent_run_id": "p", "lineage_kind": kind, "lineage_reason": "reason", "parent_status": boundary["parent_status"], "parent_boundary_event_id": "e", "parent_boundary_event_type": boundary["parent_boundary_event_type"], "parent_boundary_event_order": 1}
            with self.subTest(boundary=key):
                validate_lineage(self.contract, lineage, boundary.get("terminal_status"))

    def test_lineage_negative_terminal_mislabel_fails(self):
        lineage = {"parent_run_id": "p", "lineage_kind": "resume", "lineage_reason": "reason", "parent_status": "interrupted", "parent_boundary_event_id": "e", "parent_boundary_event_type": "run.failed", "parent_boundary_event_order": 1}
        with self.assertRaisesRegex(ConformanceError, "lineage-boundary-type"):
            validate_lineage(self.contract, lineage)
        terminated = {"parent_run_id": "p", "lineage_kind": "retry", "lineage_reason": "reason", "parent_status": "failed", "parent_boundary_event_id": "e", "parent_boundary_event_type": "run.terminated", "parent_boundary_event_order": 1}
        with self.assertRaisesRegex(ConformanceError, "lineage-boundary-terminal-status"):
            validate_lineage(self.contract, terminated, "blocked")

    def test_append_decision_order(self):
        store = MemoryAppendStore()
        request = append_request()
        original = store.append(request)
        exact_retry = store.append(copy.deepcopy(request))
        self.assertEqual(exact_retry, original)
        self.assertEqual(len(store.events), 1)
        divergent = copy.deepcopy(request)
        divergent["reason"] = "different"
        with self.assertRaisesRegex(ConformanceError, "divergent-duplicate"):
            store.append(divergent)
        new_stale = append_request("client-2")
        with self.assertRaises(StaleHeadError) as caught:
            store.append(new_stale)
        self.assertEqual(caught.exception.rejection, {"code": "stale_head", "current_stream_head": original["new_stream_head"], "last_stored_receipt": original})

        empty_store = MemoryAppendStore()
        empty_stale = append_request("empty-stale")
        empty_stale["expected_stream_head"] = {"event_order": 1, "content_digest": "sha256:" + "f" * 64}
        with self.assertRaises(StaleHeadError) as empty_caught:
            empty_store.append(empty_stale)
        self.assertEqual(empty_caught.exception.rejection, {"code": "stale_head", "current_stream_head": {"event_order": 0, "content_digest": ZERO_DIGEST}, "last_stored_receipt": None})

    def test_append_validates_payload_runs_reducer_and_stores_transition(self):
        store = MemoryAppendStore()
        invalid = append_request("invalid-payload")
        invalid["payload"] = {"sample": True}
        with self.assertRaises(ConformanceError):
            store.append(invalid)
        self.assertEqual(store.events, [])

        first_request = append_request("created")
        first = store.append(first_request)
        self.assertEqual(store.events[0]["prior_state"], {})
        self.assertEqual(store.events[0]["next_state"]["status"], "pending")
        self.assertEqual(store.events[0]["next_state"]["run_id"], "run-root")

        premature_stage = {
            **append_request("premature-stage"),
            "event_type": "run.stage.started",
            "payload": {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []},
            "causation_id": first["event_id"],
            "expected_stream_head": copy.deepcopy(first["new_stream_head"]),
            "prev_event_digest": first["new_stream_head"]["content_digest"],
        }
        premature_stage.pop("causation_chain")
        with self.assertRaisesRegex(ConformanceError, "reducer-prior-status"):
            store.append(premature_stage)
        self.assertEqual(len(store.events), 1)

        started = copy.deepcopy(premature_stage)
        started["client_event_id"] = "started"
        started["event_type"] = "run.started"
        started["payload"] = {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"}
        store.append(started)
        self.assertEqual(store.events[1]["prior_state"]["status"], "pending")
        self.assertEqual(store.events[1]["next_state"]["status"], "active")

    def test_all_event_payloads_have_executable_typed_validation(self):
        artifact = _public_runtime_artifact("a-1", artifact_kind="evidence")
        error = {"code": "E_TEST", "message": "failure", "stack": "frame"}
        lineage = lambda kind, status, boundary: {"parent_run_id": "run-parent", "lineage_kind": kind, "lineage_reason": "reason", "parent_status": status, "parent_boundary_event_id": "evt-parent", "parent_boundary_event_type": boundary, "parent_boundary_event_order": 3}
        gate = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-1", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator", "evidence": [artifact]}
        samples = {
            "run.created": genesis_payload(self.contract),
            "run.started": {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"},
            "run.stage.started": {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": [artifact]},
            "run.stage.completed": {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-1"], "artifacts_produced": [artifact]},
            "run.stage.failed": {"stage_id": "build", "failed_at": "2026-07-16T00:00:04Z", "error": error, "failure_category": "command_failed", "failure_is_transient": True, "failure_is_deterministic": True, "artifacts_produced_before_failure": [artifact], "retry_eligible": True},
            "run.stage.skipped": {"stage_id": "publish", "skipped_at": "2026-07-16T00:00:04Z", "authorized_by": "human", "reason": "reason", "authorizing_intervention_id": "i-1"},
            "run.gate.evaluated": gate,
            "run.gate.blocked": {**gate, "outcome": "blocked", "blocked_reason": "missing", "required_evidence": [artifact]},
            "run.gate.overridden": {"stage_id": "build", "gate_id": "gate-required", "new_decision_id": "d-2", "original_decision_id": "d-1", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:04Z", "evaluated_by": "architect", "evidence": [artifact], "override_reason": "reason", "authorized_by": "architect", "authorized_at": "2026-07-16T00:00:04Z", "authorizing_intervention_id": "i-1"},
            "run.retry.initiated": {"new_run_id": "run-child", "lineage": lineage("retry", "failed", "run.failed"), "retry_strategy": "full", "current_retry_count": 1, "max_retries": 3, "failure_category": "command_failed", "authorized_by": "architect", "authorized_at": "2026-07-16T00:00:05Z"},
            "run.resumed": {"new_run_id": "run-child", "lineage": lineage("resume", "interrupted", "run.interrupted"), "checkpoint_event_order": 2, "recovery_action": "replay_from_checkpoint", "authorized_by": "architect", "authorized_at": "2026-07-16T00:00:05Z"},
            "run.redesign": {"new_run_id": "run-child", "lineage": lineage("redesign", "blocked", "run.blocked"), "revised_stage_graph": copy.deepcopy(self.contract["stage_graph"]), "authorized_by": "architect", "authorized_at": "2026-07-16T00:00:05Z"},
            "run.intervention": {"intervention_id": "i-1", "intervention_type": "provide_evidence", "authorized_by": "human", "reason": "reason", "evidence": [artifact]},
            "run.terminated": {"terminated_at": "2026-07-16T00:00:05Z", "terminated_by": "human", "termination_reason": "reason", "from_status": "active", "terminal_status": "failed"},
            "run.completed": {"completed_at": "2026-07-16T00:00:05Z", "terminal_stages_completed": ["publish"], "final_projection_digest": "sha256:" + "2" * 64, "total_event_count": 8},
            "run.failed": {"failed_at": "2026-07-16T00:00:05Z", "failed_stage_id": "build", "error": error, "failure_category": "command_failed", "failure_is_transient": False, "failure_is_deterministic": True, "retry_eligible": False},
            "run.blocked": {"blocked_at": "2026-07-16T00:00:05Z", "blocked_reason": "missing", "resolution_paths": ["more_evidence"], "required_evidence": [artifact]},
            "run.interrupted": {"interrupted_at": "2026-07-16T00:00:05Z", "last_event_order": 2, "interruption_cause": "session_lost", "checkpoint_available": True},
        }
        self.assertEqual(set(samples), FROZEN_EVENT_TYPES)
        for event_type, payload in samples.items():
            with self.subTest(event_type=event_type):
                validate_payload(self.contract, event_type, payload)
                for field in self.contract["event_schemas"][event_type]["required"]:
                    malformed = copy.deepcopy(payload)
                    malformed[field] = None
                    with self.assertRaises(ConformanceError, msg=event_type + ":" + field):
                        validate_payload(self.contract, event_type, malformed)
        degraded = copy.deepcopy(gate)
        degraded["execution_mode"] = "degraded_transport"
        degraded["degradation_note"] = "offline evidence transport"
        validate_payload(self.contract, "run.gate.evaluated", degraded)
        for malformed in ({k: v for k, v in degraded.items() if k != "degradation_note"}, {**gate, "degradation_note": "not degraded"}):
            with self.assertRaises(ConformanceError):
                validate_payload(self.contract, "run.gate.evaluated", malformed)

    def test_lineage_actions_bind_parent_boundary_and_effects(self):
        pending = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        active = reduce_event(self.contract, pending, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        active_stage = reduce_event(self.contract, active, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        failed = reduce_event(self.contract, active_stage, "run.failed", {"failed_at": "2026-07-16T00:00:03Z", "failed_stage_id": "build", "error": {"code": "E", "message": "failed"}, "failure_category": "command_failed", "failure_is_transient": True, "failure_is_deterministic": True, "retry_eligible": True})
        failed.update({"latest_event_id": "evt-f", "latest_event_type": "run.failed", "latest_event_order": 4})
        retry_lineage = {"parent_run_id": "run-root", "lineage_kind": "retry", "lineage_reason": "retry", "parent_status": "failed", "parent_boundary_event_id": "evt-f", "parent_boundary_event_type": "run.failed", "parent_boundary_event_order": 4}
        retry = {"new_run_id": "run-retry", "lineage": retry_lineage, "retry_strategy": "full", "current_retry_count": 1, "max_retries": 3, "failure_category": "command_failed", "authorized_by": "architect", "authorized_at": "2026-07-16T00:00:04Z"}
        retried = reduce_event(self.contract, failed, "run.retry.initiated", retry)
        self.assertEqual(retried["child_actions"][-1]["new_run_id"], "run-retry")
        for field, value, error_name in (("new_run_id", "run-root", "lineage-action-child-id"), ("current_retry_count", 4, "retry-count-bounds"), ("failure_category", "environment_issue", "retry-failure-category")):
            malformed = copy.deepcopy(retry)
            malformed[field] = value
            with self.assertRaisesRegex(ConformanceError, error_name):
                reduce_event(self.contract, failed, "run.retry.initiated", malformed)

        interrupted = reduce_event(self.contract, active, "run.interrupted", {"interrupted_at": "2026-07-16T00:00:03Z", "last_event_order": active["events_count"], "interruption_cause": "session_lost", "checkpoint_available": True})
        interrupted.update({"latest_event_id": "evt-i", "latest_event_type": "run.interrupted", "latest_event_order": 3})
        resume_lineage = {"parent_run_id": "run-root", "lineage_kind": "resume", "lineage_reason": "resume", "parent_status": "interrupted", "parent_boundary_event_id": "evt-i", "parent_boundary_event_type": "run.interrupted", "parent_boundary_event_order": 3}
        resumed = reduce_event(self.contract, interrupted, "run.resumed", {"new_run_id": "run-resume", "lineage": resume_lineage, "checkpoint_event_order": 2, "recovery_action": "replay_from_checkpoint", "authorized_by": "human", "authorized_at": "2026-07-16T00:00:04Z"})
        self.assertEqual(resumed["child_actions"][-1]["checkpoint_event_order"], 2)

        blocked = reduce_event(self.contract, active, "run.blocked", {"blocked_at": "2026-07-16T00:00:03Z", "blocked_reason": "contract gap", "resolution_paths": ["contract_redesign"], "required_evidence": []})
        blocked.update({"latest_event_id": "evt-b", "latest_event_type": "run.blocked", "latest_event_order": 3})
        redesign_lineage = {"parent_run_id": "run-root", "lineage_kind": "redesign", "lineage_reason": "redesign", "parent_status": "blocked", "parent_boundary_event_id": "evt-b", "parent_boundary_event_type": "run.blocked", "parent_boundary_event_order": 3}
        redesigned = reduce_event(self.contract, blocked, "run.redesign", {"new_run_id": "run-redesign", "lineage": redesign_lineage, "revised_stage_graph": copy.deepcopy(self.contract["stage_graph"]), "authorized_by": "architect", "authorized_at": "2026-07-16T00:00:04Z"})
        self.assertEqual(redesigned["child_actions"][-1]["revised_stage_graph"], self.contract["stage_graph"])

    def test_completion_digest_rejects_forgery_and_is_replay_stable(self):
        prior = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        prior["status"] = "active"
        prior["stage_states"]["build"]["status"] = "completed"
        prior["stage_states"]["build"]["gate_decisions"]["gate-required"] = {"decision_id": "d", "outcome": "pass", "evidence": [{"artifact_id": "e", "artifact_kind": "evidence"}]}
        prior["stage_states"]["publish"]["status"] = "completed"
        payload = {"completed_at": "2026-07-16T00:00:05Z", "terminal_stages_completed": ["publish"], "final_projection_digest": ZERO_DIGEST, "total_event_count": prior["events_count"] + 1}
        payload["final_projection_digest"] = compute_completion_digest(prior, payload)
        self.assertEqual(compute_completion_digest(copy.deepcopy(prior), copy.deepcopy(payload)), payload["final_projection_digest"])
        self.assertEqual(reduce_event(self.contract, prior, "run.completed", payload)["status"], "completed")
        forged = copy.deepcopy(payload)
        forged["final_projection_digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ConformanceError, "run-completed-projection-digest"):
            reduce_event(self.contract, prior, "run.completed", forged)

    def test_canonical_fixed_vectors_and_injectivity(self):
        for vector in self.contract["canonical_serialization"]["vectors"]:
            self.assertEqual(canonical_bytes(vector["value"]).decode("utf-8"), vector["canonical_utf8"])
            self.assertEqual(digest(vector["value"]), vector["sha256"])
        collision_candidates = [
            {"a": "bc", "ab": "c"}, {"a": "b", "cab": "c"},
            {"x": ["a,b", "c"]}, {"x": ["a", "b,c"]},
            {"x": {"a": "b:c"}}, {"x:a": "b:c"},
            {"x": None}, {"x": "null"}, {"x": True}, {"x": "true"},
            {"x": 1}, {"x": "1"}, [1, {"a": [False, None]}],
        ]
        serialized = [canonical_bytes(value) for value in collision_candidates]
        self.assertEqual(len(serialized), len(set(serialized)))

    def test_rfc8785_uses_utf16_code_unit_key_order(self):
        non_bmp = "\U00010000"
        bmp_private_use = "\ue000"
        value = {bmp_private_use: "bmp-private-use", non_bmp: "non-bmp"}
        expected = "{" + json.dumps(non_bmp, ensure_ascii=False) + ":\"non-bmp\"," + json.dumps(bmp_private_use, ensure_ascii=False) + ":\"bmp-private-use\"}"
        self.assertEqual(canonical_bytes(value).decode("utf-8"), expected)
        self.assertNotEqual(canonical_bytes(value), json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def test_canonical_negative_mutations(self):
        for value in (1.5, {"n": 2**60}, {"\ud800": "lone-surrogate-key"}, "\udfff"):
            with self.subTest(value=value), self.assertRaises(ConformanceError):
                canonical_bytes(value)

    def test_signed_receipt_verification_and_tail_truncation(self):
        validate_contract_shape(self.contract)
        store = MemoryAppendStore()
        receipt = store.append(append_request())
        self.assertEqual(set(receipt), set(self.contract["append_receipt"]["required"]))
        self.assertEqual(set(receipt["signed_receipt"]), set(self.contract["append_receipt"]["signed_receipt_required"]))
        store.verify_receipt(receipt, "run-root")
        tampered = copy.deepcopy(receipt)
        tampered["signed_receipt"]["signature"] = "0" * 64
        with self.assertRaisesRegex(ConformanceError, "signed-receipt-verification"):
            store.verify_receipt(tampered, "run-root")
        store.events.clear()
        with self.assertRaisesRegex(ConformanceError, "tail-truncation"):
            store.verify_tail(receipt, "run-root")

    def test_read_side_stream_verification_rejects_payload_order_and_link_tamper(self):
        store = MemoryAppendStore()
        created_receipt = store.append(append_request("stream-created"))
        trusted_receipt = store.append(started_request(created_receipt, "stream-started"))
        rebuilt = store.verify_stream("run-root", trusted_receipt)
        self.assertEqual(rebuilt, store.projections["run-root"])
        store.verify_tail(trusted_receipt, "run-root")
        original_events = copy.deepcopy(store.events)

        mutations = []
        payload_tamper = copy.deepcopy(original_events)
        payload_tamper[1]["payload"]["started_at"] = "2026-07-16T00:00:09Z"
        mutations.append(("payload", payload_tamper, "stream-content-digest"))
        order_tamper = copy.deepcopy(original_events)
        order_tamper[1]["event_order"] = 3
        mutations.append(("order", order_tamper, "stream-event-order-gap"))
        link_tamper = copy.deepcopy(original_events)
        link_tamper[1]["prev_event_digest"] = "sha256:" + "f" * 64
        mutations.append(("link", link_tamper, "stream-prev-event-digest-link"))
        coordinated_payload = copy.deepcopy(payload_tamper)
        preimage = copy.deepcopy(coordinated_payload[1])
        preimage.pop("content_digest")
        coordinated_payload[1]["content_digest"] = digest(preimage)
        mutations.append(("payload-rehashed", coordinated_payload, "stream-next-state-replay"))

        for name, events, expected_error in mutations:
            store.events = events
            with self.subTest(name=name), self.assertRaisesRegex(ConformanceError, expected_error):
                store.verify_stream("run-root", trusted_receipt)
        store.events = original_events

    def test_receipt_rejects_all_cross_field_and_context_mutations(self):
        store = MemoryAppendStore()
        receipt = store.append(append_request())
        mutations = []

        top_order = copy.deepcopy(receipt)
        top_order["event_order"] = 999
        mutations.append(("event-order-binding", top_order, "run-root"))

        top_digest = copy.deepcopy(receipt)
        top_digest["stored_content_digest"] = "sha256:" + ("f" * 64)
        mutations.append(("content-digest-binding", top_digest, "run-root"))

        wrong_context = copy.deepcopy(receipt)
        wrong_context["signed_receipt"] = store._signed_receipt({"run_id": "run-other", **receipt["new_stream_head"]})
        mutations.append(("run-context", wrong_context, "run-root"))

        head_order = copy.deepcopy(receipt)
        head_order["new_stream_head"]["event_order"] = 999
        mutations.append(("head-binding", head_order, "run-root"))

        head_digest = copy.deepcopy(receipt)
        head_digest["new_stream_head"]["content_digest"] = "sha256:" + ("e" * 64)
        mutations.append(("head-binding", head_digest, "run-root"))

        boolean_order = copy.deepcopy(receipt)
        boolean_order["event_order"] = True
        mutations.append(("boolean-order", boolean_order, "run-root"))

        malformed_digest = copy.deepcopy(receipt)
        malformed_digest["stored_content_digest"] = "sha256:not-a-digest"
        mutations.append(("malformed-digest", malformed_digest, "run-root"))

        non_string_event_id = copy.deepcopy(receipt)
        non_string_event_id["event_id"] = 7
        mutations.append(("non-string-event-id-valid-mac", non_string_event_id, "run-root"))

        signed_boolean_order = copy.deepcopy(receipt)
        signed_boolean_order["signed_receipt"] = store._signed_receipt({"run_id": "run-root", "event_order": True, "content_digest": receipt["new_stream_head"]["content_digest"]})
        mutations.append(("signed-boolean-order-valid-mac", signed_boolean_order, "run-root"))

        for name, mutated, expected_run_id in mutations:
            with self.subTest(name=name), self.assertRaises(ConformanceError):
                store.verify_receipt(mutated, expected_run_id)

    def test_receipt_rejects_algorithm_key_and_exact_shape_attacks(self):
        store = MemoryAppendStore()
        receipt = store.append(append_request())
        attacks = []

        none_algorithm = copy.deepcopy(receipt)
        none_algorithm["signed_receipt"]["algorithm"] = "NONE"
        attacks.append(("algorithm-none", none_algorithm, "signed-receipt-algorithm"))

        attacker_key = copy.deepcopy(receipt)
        attacker_payload = attacker_key["signed_receipt"]["signed_payload"]
        attacker_key["signed_receipt"]["key_id"] = "attacker-key"
        attacker_key["signed_receipt"]["signature"] = hmac.new(b"attacker-secret", canonical_bytes(attacker_payload), hashlib.sha256).hexdigest()
        attacks.append(("attacker-key", attacker_key, "signed-receipt-key-id"))

        containers = [
            ("envelope", lambda item: item, "signed-receipt-envelope-shape"),
            ("signed-receipt", lambda item: item["signed_receipt"], "signed-receipt-shape"),
            ("signed-payload", lambda item: item["signed_receipt"]["signed_payload"], "signed-receipt-payload-shape"),
            ("stream-head", lambda item: item["new_stream_head"], "signed-receipt-head-shape"),
        ]
        required_to_remove = {
            "envelope": "event_id",
            "signed-receipt": "algorithm",
            "signed-payload": "run_id",
            "stream-head": "event_order",
        }
        for name, select, expected_error in containers:
            extra = copy.deepcopy(receipt)
            select(extra)["unexpected"] = "attack"
            attacks.append((name + "-extra", extra, expected_error))
            missing = copy.deepcopy(receipt)
            select(missing).pop(required_to_remove[name])
            attacks.append((name + "-missing", missing, expected_error))

        for name, attacked, expected_error in attacks:
            with self.subTest(name=name), self.assertRaisesRegex(ConformanceError, expected_error):
                store.verify_receipt(attacked, "run-root")

    def test_signed_receipt_contract_negative_mutation_fails(self):
        mutated = copy.deepcopy(self.contract)
        mutated["append_receipt"]["required"].remove("signed_receipt")
        with self.assertRaisesRegex(ConformanceError, "signed-receipt-mandatory"):
            validate_contract_shape(mutated)

    def test_projection_digest_replay_determinism(self):
        store = MemoryAppendStore()
        created_receipt = store.append(append_request("replay-created"))
        trusted_receipt = store.append(started_request(created_receipt, "replay-started"))
        rebuilt_one = store.verify_stream("run-root", trusted_receipt)
        rebuilt_two = store.verify_stream("run-root", trusted_receipt)
        self.assertEqual(rebuilt_one, store.projections["run-root"])
        self.assertEqual(rebuilt_one, rebuilt_two)
        self.assertIsNot(rebuilt_one, rebuilt_two)
        one = {"projection_id": "random-a", "derived_at": "2026-07-16T01:00:00Z", "projection_digest": "ignored", "run_id": rebuilt_one["run_id"], "derived_at_event_order": 2, "projection_type": "run", "state": rebuilt_one, "event_range": {"first_event_order": 1, "last_event_order": 2}}
        two = copy.deepcopy(one)
        two["projection_id"] = "random-b"
        two["derived_at"] = "2030-01-01T00:00:00Z"
        two["state"] = rebuilt_two
        self.assertEqual(projection_digest(one, self.contract["projection_digest_excludes"]), projection_digest(two, self.contract["projection_digest_excludes"]))
        mutated = copy.deepcopy(self.contract)
        mutated["projection_digest_excludes"].remove("derived_at")
        with self.assertRaisesRegex(ConformanceError, "projection-digest-preimage"):
            validate_contract_shape(mutated)

    def test_required_gate_and_stage_invariants(self):
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        completed_payload = {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": [], "artifacts_produced": []}
        with self.assertRaisesRegex(ConformanceError, "stage-complete-gates-evaluated"):
            reduce_event(self.contract, state, "run.stage.completed", completed_payload)
        passed = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d1", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [{"artifact_id": "report-1", "artifact_kind": "validation_report", "digest": "sha256:" + "1" * 64}]}
        state = reduce_event(self.contract, state, "run.gate.evaluated", passed)
        completed_payload["gate_decisions"] = ["d1"]
        state = reduce_event(self.contract, state, "run.stage.completed", completed_payload)
        self.assertEqual(state["stage_states"]["build"]["status"], "completed")
        override = {"stage_id": "build", "gate_id": "gate-required", "new_decision_id": "d2", "original_decision_id": "d1", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:05Z", "evaluated_by": "human", "evidence": [], "override_reason": "not allowed", "authorized_by": "human", "authorized_at": "2026-07-16T00:00:05Z", "authorizing_intervention_id": "i1"}
        active = copy.deepcopy(state)
        active["status"] = "active"
        active["stage_states"]["build"]["status"] = "active"
        with self.assertRaisesRegex(ConformanceError, "required-gate-no-override"):
            reduce_event(self.contract, active, "run.gate.overridden", override)
        skip = {"stage_id": "build", "skipped_at": "2026-07-16T00:00:05Z", "authorized_by": "human", "reason": "not allowed", "authorizing_intervention_id": "i1"}
        active["stage_states"]["build"]["status"] = "pending"
        with self.assertRaisesRegex(ConformanceError, "required-stage-no-skip"):
            reduce_event(self.contract, active, "run.stage.skipped", skip)

    def test_stage_graph_dag_and_validator_contract_ref_invariants(self):
        for operation in ("missing-contract-ref", "scalar-contract-ref", "unresolved-contract-ref", "cycle"):
            graph = copy.deepcopy(self.contract["stage_graph"])
            if operation == "missing-contract-ref":
                graph["stages"][0]["gates"][0].pop("contract_ref")
            elif operation == "scalar-contract-ref":
                graph["stages"][0]["gates"][0]["contract_ref"] = "not-typed"
            elif operation == "unresolved-contract-ref":
                graph["stages"][0]["gates"][0]["contract_ref"]["locator"] = "references/does-not-exist.md"
            else:
                graph["edges"].append({"from": "publish", "to": "build", "condition": "always"})
            payload = genesis_payload(self.contract)
            payload["stage_graph"] = graph
            with self.subTest(operation=operation), self.assertRaises(ConformanceError):
                reduce_event(self.contract, None, "run.created", payload)

    def test_gate_reevaluation_requires_current_reference_new_id_and_new_evidence(self):
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        old_evidence = {"artifact_id": "report-old", "artifact_kind": "validation_report"}
        first = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-old", "outcome": "fail", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [old_evidence]}
        state = reduce_event(self.contract, state, "run.gate.evaluated", first)
        reevaluation = {**first, "decision_id": "d-new", "outcome": "pass", "evaluated_at": "2026-07-16T00:00:04Z", "reevaluates_decision_id": "d-old", "evidence": [old_evidence, {"artifact_id": "report-new", "artifact_kind": "validation_report"}]}
        updated = reduce_event(self.contract, state, "run.gate.evaluated", reevaluation)
        stage = updated["stage_states"]["build"]
        self.assertEqual(stage["gate_decisions"]["gate-required"]["decision_id"], "d-new")
        self.assertEqual([item["decision_id"] for item in stage["gate_decision_history"]], ["d-old", "d-new"])
        mutations = (
            {**reevaluation, "reevaluates_decision_id": "not-current"},
            {**reevaluation, "decision_id": "d-old"},
            {**reevaluation, "evidence": [old_evidence]},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)), self.assertRaises(ConformanceError):
                reduce_event(self.contract, state, "run.gate.evaluated", mutation)

    def test_system_auto_retry_policy_is_bound_to_parent_failure_projection(self):
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        state = reduce_event(self.contract, state, "run.failed", {"failed_at": "2026-07-16T00:00:03Z", "failed_stage_id": "build", "error": {"code": "E", "message": "failed"}, "failure_category": "command_failed", "failure_is_transient": True, "failure_is_deterministic": True, "retry_eligible": True})
        state.update({"latest_event_id": "evt-f", "latest_event_type": "run.failed", "latest_event_order": 4})
        lineage = {"parent_run_id": "run-root", "lineage_kind": "retry", "lineage_reason": "automatic retry", "parent_status": "failed", "parent_boundary_event_id": "evt-f", "parent_boundary_event_type": "run.failed", "parent_boundary_event_order": 4}
        retry = {"new_run_id": "run-retry", "lineage": lineage, "retry_strategy": "full", "current_retry_count": 1, "max_retries": 3, "failure_category": "command_failed", "authorized_by": "system", "authorized_at": "2026-07-16T00:00:04Z", "failure_is_transient": True, "failure_is_deterministic": True}
        retried = reduce_event(self.contract, state, "run.retry.initiated", retry)
        self.assertEqual(retried["child_actions"][-1]["new_run_id"], "run-retry")
        mutations = []
        for field in ("failure_is_transient", "failure_is_deterministic"):
            missing = copy.deepcopy(retry)
            missing.pop(field)
            mutations.append(missing)
            mutations.append({**retry, field: False})
        mutations.extend(({**retry, "retry_strategy": "resume"}, {**retry, "current_retry_count": 3}))
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)), self.assertRaises(ConformanceError):
                reduce_event(self.contract, state, "run.retry.initiated", mutation)
        manual = copy.deepcopy(retry)
        manual["authorized_by"] = "architect"
        with self.assertRaisesRegex(ConformanceError, "manual-retry-failure-flags-forbidden"):
            reduce_event(self.contract, state, "run.retry.initiated", manual)
        manual.pop("failure_is_transient")
        manual.pop("failure_is_deterministic")
        manual["current_retry_count"] = manual["max_retries"]
        reduce_event(self.contract, state, "run.retry.initiated", manual)

    def test_redesign_terminates_original_parent_as_failed(self):
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.blocked", {"blocked_at": "2026-07-16T00:00:02Z", "blocked_reason": "contract gap", "resolution_paths": ["contract_redesign"], "required_evidence": []})
        state.update({"latest_event_id": "evt-b", "latest_event_type": "run.blocked", "latest_event_order": 3})
        lineage = {"parent_run_id": "run-root", "lineage_kind": "redesign", "lineage_reason": "replace graph", "parent_status": "blocked", "parent_boundary_event_id": "evt-b", "parent_boundary_event_type": "run.blocked", "parent_boundary_event_order": 3}
        payload = {"new_run_id": "run-redesign", "lineage": lineage, "revised_stage_graph": copy.deepcopy(self.contract["stage_graph"]), "authorized_by": "architect", "authorized_at": "2026-07-16T00:00:04Z"}
        redesigned = reduce_event(self.contract, state, "run.redesign", payload)
        self.assertEqual(redesigned["status"], "failed")
        self.assertEqual(redesigned["terminated_at"], payload["authorized_at"])
        self.assertEqual(redesigned["termination_reason"], lineage["lineage_reason"])
        self.assertEqual(redesigned["child_actions"][-1]["revised_stage_graph"], payload["revised_stage_graph"])

    def test_ascii_public_contract_hygiene(self):
        raw = CONTRACT.read_bytes()
        self.assertTrue(raw)
        non_ascii = [(index, byte) for index, byte in enumerate(raw) if byte > 0x7F]
        self.assertEqual(non_ascii, [])

    def test_visibility_resolution_and_failure_codes(self):
        vis = self.contract["visibility"]
        self.assertEqual(vis["contract_version"], "0.9.0")
        self.assertEqual(vis["resolution_rule"], "most_restrictive")
        accepted_failures = {"visibility_no_evidence", "visibility_contributor_conflict", "visibility_downgrade", "visibility_invalid_value",
                             "source_contributor_omission", "origin_mismatch", "malformed_audit"}
        for case in vis["cases"]:
            with self.subTest(case_id=case["case_id"]):
                if case.get("expected_rejected"):
                    self.assertIn(case["expected_failure"], accepted_failures)
                    if case["expected_failure"] == "visibility_invalid_value":
                        with self.assertRaises(ValueError):
                            resolve_most_restrictive(case["contributors"])
                else:
                    vc_contributors = list(case["contributors"])
                    if case.get("visibility_context"):
                        vc = case["visibility_context"]
                        vc_contributors.append({"asserted_visibility": vc.get("trigger_visibility", "public")})
                        vc_contributors.extend(vc.get("policy_contributors", []))
                        vc_contributors.extend(vc.get("contract_contributors", []))
                    if case.get("artifact_visibilities"):
                        vc_contributors.extend(
                            {"asserted_visibility": av} for av in case["artifact_visibilities"]
                        )
                    resolved = resolve_most_restrictive(vc_contributors)
                    self.assertEqual(resolved, case["expected_resolved"])
                    if case.get("expected_audit"):
                        audit = {"restricted_count": 0, "project_count": 0, "public_count": 0}
                        for c in case["contributors"]:
                            av = c.get("asserted_visibility", "public")
                            audit[f"{av}_count"] += 1
                        if case.get("visibility_context"):
                            vc = case["visibility_context"]
                            tv = vc.get("trigger_visibility")
                            if tv:
                                audit[f"{tv}_count"] += 1
                            for pc in vc.get("policy_contributors", []):
                                av = pc.get("asserted_visibility", "public")
                                audit[f"{av}_count"] += 1
                            for cc in vc.get("contract_contributors", []):
                                av = cc.get("asserted_visibility", "public")
                                audit[f"{av}_count"] += 1
                        self.assertEqual(audit, case["expected_audit"])

    def test_visibility_context_is_immutable_after_run_created(self):
        """The projection's visibility_context matches the run.created payload exactly and doesn't change after subsequent events."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        self.assertEqual(state["visibility_context"], genesis_payload(self.contract)["visibility_context"])
        self.assertEqual(state["resolved_run_visibility"], "public")
        orig_context = copy.deepcopy(state["visibility_context"])
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        self.assertEqual(state["visibility_context"], orig_context)

    def test_resolved_run_visibility_tightens_with_stricter_artifact(self):
        """Run starts public, stage completes with project artifact -> resolved_run_visibility becomes project."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        self.assertEqual(state["resolved_run_visibility"], "public")
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        decision = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-pub", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [{"artifact_id": "report-1", "artifact_kind": "validation_report"}]}
        state = reduce_event(self.contract, state, "run.gate.evaluated", decision)
        project_artifact = _public_runtime_artifact("proj-out-1", stage_id="build")
        project_artifact["visibility"] = "project"
        project_artifact["visibility_resolution"]["resolved_visibility"] = "project"
        project_artifact["visibility_resolution"]["contributors"][0]["asserted_visibility"] = "project"
        project_artifact["visibility_resolution"]["resolution_audit"]["project_count"] = 1
        project_artifact["visibility_resolution"]["resolution_audit"]["public_count"] = 0
        state = reduce_event(self.contract, state, "run.stage.completed", {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-pub"], "artifacts_produced": [project_artifact]})
        self.assertEqual(state["resolved_run_visibility"], "project")
        self.assertEqual(state["visibility_context"]["resolved_run_visibility"], "public")

    def test_resolved_run_visibility_is_most_restrictive_across_artifacts(self):
        """Multiple artifacts at different visibilities -> resolved is most restrictive."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        decision = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-multi", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [{"artifact_id": "r", "artifact_kind": "validation_report"}]}
        state = reduce_event(self.contract, state, "run.gate.evaluated", decision)
        public_artifact = _public_runtime_artifact("pub-1", stage_id="build")
        restricted_artifact = _public_runtime_artifact("rest-1", stage_id="build")
        restricted_artifact["visibility"] = "restricted"
        restricted_artifact["visibility_resolution"]["resolved_visibility"] = "restricted"
        restricted_artifact["visibility_resolution"]["contributors"][0]["asserted_visibility"] = "restricted"
        restricted_artifact["visibility_resolution"]["resolution_audit"]["restricted_count"] = 1
        restricted_artifact["visibility_resolution"]["resolution_audit"]["public_count"] = 0
        state = reduce_event(self.contract, state, "run.stage.completed", {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-multi"], "artifacts_produced": [public_artifact, restricted_artifact]})
        self.assertEqual(state["resolved_run_visibility"], "restricted")
        self.assertEqual(len(state["runtime_artifacts"]), 2)

    def test_failed_stage_artifacts_contribute_to_visibility(self):
        """Failed stage partial artifacts still affect resolved_run_visibility."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        project_artifact = _public_runtime_artifact("partial-1", stage_id="build")
        project_artifact["visibility"] = "project"
        project_artifact["visibility_resolution"]["resolved_visibility"] = "project"
        project_artifact["visibility_resolution"]["contributors"][0]["asserted_visibility"] = "project"
        project_artifact["visibility_resolution"]["resolution_audit"]["project_count"] = 1
        project_artifact["visibility_resolution"]["resolution_audit"]["public_count"] = 0
        payload = {"stage_id": "build", "failed_at": "2026-07-16T00:00:03Z", "error": {"code": "E", "message": "failed"}, "failure_category": "command_failed", "failure_is_transient": True, "failure_is_deterministic": True, "artifacts_produced_before_failure": [project_artifact], "retry_eligible": True}
        state = reduce_event(self.contract, state, "run.stage.failed", payload)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["resolved_run_visibility"], "project")
        self.assertEqual(len(state["runtime_artifacts"]), 1)

    def test_artifact_origin_run_must_match_event_run(self):
        """Artifact with wrong origin_run is rejected."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        decision = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-mismatch", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [{"artifact_id": "r", "artifact_kind": "validation_report"}]}
        state = reduce_event(self.contract, state, "run.gate.evaluated", decision)
        bad_artifact = _public_runtime_artifact("bad-origin", stage_id="build")
        bad_artifact["origin_run"] = "run-other"
        with self.assertRaises(ConformanceError):
            reduce_event(self.contract, state, "run.stage.completed", {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-mismatch"], "artifacts_produced": [bad_artifact]})

    def test_artifact_origin_stage_must_match_payload_stage(self):
        """Artifact with wrong origin_stage is rejected."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        decision = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-stage-mismatch", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [{"artifact_id": "r", "artifact_kind": "validation_report"}]}
        state = reduce_event(self.contract, state, "run.gate.evaluated", decision)
        bad_artifact = _public_runtime_artifact("bad-stage", stage_id="build")
        bad_artifact["origin_stage"] = "publish"
        with self.assertRaises(ConformanceError):
            reduce_event(self.contract, state, "run.stage.completed", {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-stage-mismatch"], "artifacts_produced": [bad_artifact]})

    def test_source_contributor_must_appear_in_resolution(self):
        """Artifact with source_artifact not in resolution contributors is rejected."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        decision = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-source", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [{"artifact_id": "r", "artifact_kind": "validation_report"}]}
        state = reduce_event(self.contract, state, "run.gate.evaluated", decision)
        bad_artifact = _public_runtime_artifact("missing-source", stage_id="build")
        bad_artifact["source_artifacts"] = [{"artifact_id": "unlisted-source", "artifact_kind": "stage_output"}]
        with self.assertRaises(ConformanceError):
            reduce_event(self.contract, state, "run.stage.completed", {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-source"], "artifacts_produced": [bad_artifact]})

    def test_resolution_audit_counts_must_match_contributors(self):
        """Mismatched audit counts are rejected."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        decision = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-audit", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [{"artifact_id": "r", "artifact_kind": "validation_report"}]}
        state = reduce_event(self.contract, state, "run.gate.evaluated", decision)
        bad_artifact = _public_runtime_artifact("bad-audit", stage_id="build")
        bad_artifact["visibility"] = "restricted"
        bad_artifact["visibility_resolution"]["resolved_visibility"] = "restricted"
        bad_artifact["visibility_resolution"]["contributors"][0]["asserted_visibility"] = "restricted"
        bad_artifact["visibility_resolution"]["resolution_audit"]["restricted_count"] = 2
        bad_artifact["visibility_resolution"]["resolution_audit"]["public_count"] = 0
        with self.assertRaises(ConformanceError):
            reduce_event(self.contract, state, "run.stage.completed", {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-audit"], "artifacts_produced": [bad_artifact]})

    def test_projection_digest_is_stable_with_visibility_fields(self):
        """Same events replayed produce identical digest including visibility fields."""
        state1 = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state1 = reduce_event(self.contract, state1, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state1 = reduce_event(self.contract, state1, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        decision = {"stage_id": "build", "gate_id": "gate-required", "decision_id": "d-stable", "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z", "evaluated_by": "validator-1", "evidence": [{"artifact_id": "r", "artifact_kind": "validation_report"}]}
        state1 = reduce_event(self.contract, state1, "run.gate.evaluated", decision)
        artifact = _public_runtime_artifact("a1")
        state1 = reduce_event(self.contract, state1, "run.stage.completed", {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-stable"], "artifacts_produced": [artifact]})

        state2 = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract))
        state2 = reduce_event(self.contract, state2, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state2 = reduce_event(self.contract, state2, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        state2 = reduce_event(self.contract, state2, "run.gate.evaluated", copy.deepcopy(decision))
        state2 = reduce_event(self.contract, state2, "run.stage.completed", {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["d-stable"], "artifacts_produced": [artifact]})

        envelope1 = {"projection_id": "p1", "derived_at": "2026-07-16T01:00:00Z", "projection_digest": "ignored", "run_id": state1["run_id"], "state": state1, "derived_at_event_order": 5, "projection_type": "run", "event_range": {"first_event_order": 1, "last_event_order": 5}}
        envelope2 = {"projection_id": "p2", "derived_at": "2030-01-01T00:00:00Z", "projection_digest": "ignored", "run_id": state2["run_id"], "state": state2, "derived_at_event_order": 5, "projection_type": "run", "event_range": {"first_event_order": 1, "last_event_order": 5}}

        d1 = projection_digest(envelope1, self.contract["projection_digest_excludes"])
        d2 = projection_digest(envelope2, self.contract["projection_digest_excludes"])
        self.assertEqual(d1, d2)
        self.assertIsNot(state1, state2)

    def test_rejects_artifact_ref_only_stage_output(self):
        """ArtifactRef-only stage output is rejected - must be full RuntimeArtifact."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract, event_run_id="run-root"))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        self.pay_gate(state, "build", "gate-required", "d-artifact-only", "pass")
        with self.assertRaises(ConformanceError) as ctx:
            reduce_event(self.contract, state, "run.stage.completed", {
                "stage_id": "build", "completed_at": "2026-07-16T00:00:04Z",
                "gate_decisions": ["d-artifact-only"],
                "artifacts_produced": [{"artifact_id": "bad-ref", "artifact_kind": "stage_output"}]
            })
        self.assertIn("visibility-missing-fields", str(ctx.exception))

    def test_rejects_missing_visibility(self):
        """Missing visibility field on RuntimeArtifact is rejected."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract, event_run_id="run-root"))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        self.pay_gate(state, "build", "gate-required", "d-missing-vis", "pass")
        bad = _public_runtime_artifact("bad-vis", stage_id="build")
        del bad["visibility"]
        with self.assertRaises(ConformanceError) as ctx:
            reduce_event(self.contract, state, "run.stage.completed", {
                "stage_id": "build", "completed_at": "2026-07-16T00:00:04Z",
                "gate_decisions": ["d-missing-vis"],
                "artifacts_produced": [bad]
            })
        self.assertIn("visibility-missing-fields", str(ctx.exception))

    def test_rejects_missing_resolution(self):
        """Missing visibility_resolution is rejected."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract, event_run_id="run-root"))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        self.pay_gate(state, "build", "gate-required", "d-no-res", "pass")
        bad = _public_runtime_artifact("no-res", stage_id="build")
        del bad["visibility_resolution"]
        with self.assertRaises(ConformanceError) as ctx:
            reduce_event(self.contract, state, "run.stage.completed", {
                "stage_id": "build", "completed_at": "2026-07-16T00:00:04Z",
                "gate_decisions": ["d-no-res"],
                "artifacts_produced": [bad]
            })
        self.assertIn("visibility-missing-fields", str(ctx.exception))

    def test_rejects_empty_contributors(self):
        """Empty contributors array is rejected."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract, event_run_id="run-root"))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        self.pay_gate(state, "build", "gate-required", "d-empty-con", "pass")
        bad = _public_runtime_artifact("empty-con", stage_id="build")
        bad["visibility_resolution"]["contributors"] = []
        with self.assertRaises(ConformanceError) as ctx:
            reduce_event(self.contract, state, "run.stage.completed", {
                "stage_id": "build", "completed_at": "2026-07-16T00:00:04Z",
                "gate_decisions": ["d-empty-con"],
                "artifacts_produced": [bad]
            })
        self.assertIn("visibility-empty-contributors", str(ctx.exception))

    def test_rejects_invalid_visibility_enum(self):
        """Invalid visibility value is rejected."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract, event_run_id="run-root"))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        self.pay_gate(state, "build", "gate-required", "d-invalid", "pass")
        bad = _public_runtime_artifact("invalid-vis", stage_id="build")
        bad["visibility"] = "internal"
        with self.assertRaises(ConformanceError) as ctx:
            reduce_event(self.contract, state, "run.stage.completed", {
                "stage_id": "build", "completed_at": "2026-07-16T00:00:04Z",
                "gate_decisions": ["d-invalid"],
                "artifacts_produced": [bad]
            })
        self.assertIn("visibility-invalid-value", str(ctx.exception))

    def test_rejects_absent_origin_stage(self):
        """Absent origin_stage in stage-produced artifact is rejected."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract, event_run_id="run-root"))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        self.pay_gate(state, "build", "gate-required", "d-no-stage", "pass")
        bad = _public_runtime_artifact("no-stage", stage_id="build")
        del bad["origin_stage"]
        with self.assertRaises(ConformanceError) as ctx:
            reduce_event(self.contract, state, "run.stage.completed", {
                "stage_id": "build", "completed_at": "2026-07-16T00:00:04Z",
                "gate_decisions": ["d-no-stage"],
                "artifacts_produced": [bad]
            })
        self.assertIn("visibility-origin-stage-mismatch", str(ctx.exception))

    def test_rejects_extra_unknown_fields(self):
        """Extra unknown fields on RuntimeArtifact are rejected."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract, event_run_id="run-root"))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        self.pay_gate(state, "build", "gate-required", "d-extra", "pass")
        bad = _public_runtime_artifact("extra", stage_id="build")
        bad["unknown_field"] = "should_reject"
        with self.assertRaises(ConformanceError) as ctx:
            reduce_event(self.contract, state, "run.stage.completed", {
                "stage_id": "build", "completed_at": "2026-07-16T00:00:04Z",
                "gate_decisions": ["d-extra"],
                "artifacts_produced": [bad]
            })
        self.assertIn("visibility-extra-fields", str(ctx.exception))

    def test_rejects_default_to_public_attempt(self):
        """Artifact without explicit visibility is rejected - no default to public."""
        state = reduce_event(self.contract, None, "run.created", genesis_payload(self.contract, event_run_id="run-root"))
        state = reduce_event(self.contract, state, "run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"})
        state = reduce_event(self.contract, state, "run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []})
        self.pay_gate(state, "build", "gate-required", "d-no-default", "pass")
        bad = {"artifact_ref": {"artifact_id": "x", "artifact_kind": "x"}, "origin_run": "run-root", "origin_stage": "build", "produced_by": "r", "source_artifacts": [], "visibility_resolution": {"contributors": [], "resolution_rule": "most_restrictive", "resolved_visibility": "public", "resolution_audit": {"contributor_count": 0, "restricted_count": 0, "project_count": 0, "public_count": 0, "applied_rule": "most_restrictive"}}}
        with self.assertRaises(ConformanceError):
            reduce_event(self.contract, state, "run.stage.completed", {
                "stage_id": "build", "completed_at": "2026-07-16T00:00:04Z",
                "gate_decisions": ["d-no-default"],
                "artifacts_produced": [bad]
            })


if __name__ == "__main__":
    unittest.main(verbosity=2)
