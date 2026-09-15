"""Deterministic runtime state core per Runtime State Contract v0.9.0.

Pure APIs: deterministic, storage-neutral, no wall-clock, no I/O.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Frozen constants (from the frozen conformance fixture)
# ---------------------------------------------------------------------------

ZERO_DIGEST = "sha256:" + ("0" * 64)

FROZEN_EVENT_TYPES: set[str] = {
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

FROZEN_EVENT_SCHEMAS: dict = {
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

FROZEN_REDUCERS: dict = {
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

LINEAGE_BOUNDARIES: dict = {
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

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# ---------------------------------------------------------------------------
# Validation helpers (pure, deterministic)
# ---------------------------------------------------------------------------

def _require_string(value, rule_id="typed-string"):
    if not isinstance(value, str) or not value:
        return {"valid": False, "errors": [f"{rule_id}: not a non-empty string"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


def _require_bool(value, rule_id="typed-bool"):
    if type(value) is not bool:
        return {"valid": False, "errors": [f"{rule_id}: not a bool"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


def _require_integer(value, minimum=0, rule_id="typed-integer"):
    if type(value) is not int or value < minimum:
        return {"valid": False, "errors": [f"{rule_id}: not an integer or below {minimum}"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


def _require_digest(value, rule_id="typed-digest"):
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        return {"valid": False, "errors": [f"{rule_id}: not a sha256 digest"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


def _require_timestamp(value, rule_id="typed-timestamp"):
    if not isinstance(value, str) or not value:
        return {"valid": False, "errors": [f"{rule_id}: not a non-empty string"], "rule_id": rule_id}
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return {"valid": False, "errors": [f"{rule_id}: invalid ISO 8601"], "rule_id": rule_id}
    if parsed.tzinfo is None:
        return {"valid": False, "errors": [f"{rule_id}: timestamp missing timezone"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


def _require_string_array(value, allow_empty=True, rule_id="typed-string-array"):
    if not isinstance(value, list) or (not allow_empty and not value):
        return {"valid": False, "errors": [f"{rule_id}: not a string array"], "rule_id": rule_id}
    for item in value:
        if not isinstance(item, str) or not item:
            return {"valid": False, "errors": [f"{rule_id}: non-string element"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


def _require_artifact_ref(value, rule_id="typed-artifact-ref"):
    if not isinstance(value, dict):
        return {"valid": False, "errors": [f"{rule_id}: not an object"], "rule_id": rule_id}
    allowed = {"artifact_id", "artifact_kind", "artifact_version", "locator", "digest"}
    if not {"artifact_id", "artifact_kind"}.issubset(value) or not set(value).issubset(allowed):
        return {"valid": False, "errors": [f"{rule_id}: invalid ArtifactRef shape"], "rule_id": rule_id}
    if not isinstance(value["artifact_id"], str) or not value["artifact_id"]:
        return {"valid": False, "errors": [f"{rule_id}: invalid artifact_id"], "rule_id": rule_id}
    if not isinstance(value["artifact_kind"], str) or not value["artifact_kind"]:
        return {"valid": False, "errors": [f"{rule_id}: invalid artifact_kind"], "rule_id": rule_id}
    for field in ("artifact_version", "locator"):
        if field in value:
            if not isinstance(value[field], str) or not value[field]:
                return {"valid": False, "errors": [f"{rule_id}: invalid {field}"], "rule_id": rule_id}
    if "digest" in value:
        r = _require_digest(value["digest"])
        if not r["valid"]:
            return {"valid": False, "errors": [f"{rule_id}: invalid digest"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


def _require_artifact_array(value, rule_id="typed-artifact-array"):
    if not isinstance(value, list):
        return {"valid": False, "errors": [f"{rule_id}: not an array"], "rule_id": rule_id}
    for item in value:
        r = _require_artifact_ref(item)
        if not r["valid"]:
            return {"valid": False, "errors": [f"{rule_id}: non-ArtifactRef in array"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


VISIBILITY_ORDER = {"public": 1, "project": 2, "restricted": 3}
VISIBILITY_CONTRIBUTOR_FIELDS = {
    "contributor_id", "contributor_kind", "contributor_ref",
    "asserted_visibility", "authority", "classification_evidence",
}
VISIBILITY_RESOLUTION_FIELDS = {
    "resolution_id", "resolved_at", "contributors", "resolution_rule",
    "resolved_visibility", "resolution_audit",
}
VISIBILITY_AUDIT_FIELDS = {
    "contributor_count", "restricted_count", "project_count", "public_count",
    "applied_rule",
}
VISIBILITY_CONTEXT_FIELDS = {
    "trigger_visibility", "policy_contributors", "contract_contributors",
    "run_visibility_resolution", "resolved_run_visibility",
}
RUNTIME_ARTIFACT_REQUIRED_FIELDS = {
    "artifact_ref", "origin_run", "produced_by", "source_artifacts",
    "visibility", "visibility_resolution",
}
RUNTIME_ARTIFACT_FIELDS = RUNTIME_ARTIFACT_REQUIRED_FIELDS | {"origin_stage"}


def _visibility_failure(code: str) -> dict:
    return {"valid": False, "errors": [code], "rule_id": code.split(":", 1)[0]}


def _require_visibility_contributor(value) -> dict:
    if not isinstance(value, dict) or set(value) != VISIBILITY_CONTRIBUTOR_FIELDS:
        return _visibility_failure("visibility-contributor-missing")
    for field in ("contributor_id", "authority"):
        if not isinstance(value[field], str) or not value[field].strip():
            return _visibility_failure("visibility-contributor-missing")
    if value["contributor_kind"] not in {
        "source_artifact", "governing_contract", "project_policy", "trigger_provenance"
    }:
        return _visibility_failure("visibility-contributor-missing")
    ref = _require_artifact_ref(value["contributor_ref"])
    if not ref["valid"]:
        return _visibility_failure("visibility-contributor-missing")
    if value["contributor_kind"] == "trigger_provenance" and value["contributor_ref"]["artifact_kind"] not in {
        "ticket", "pipeline_config", "script", "request_artifact"
    }:
        return _visibility_failure("visibility-contributor-missing")
    if value["contributor_kind"] == "project_policy" and value["contributor_ref"]["artifact_kind"] != "policy":
        return _visibility_failure("visibility-contributor-missing")
    if value["asserted_visibility"] not in VISIBILITY_ORDER:
        return _visibility_failure("visibility-invalid-value")
    evidence = value["classification_evidence"]
    if not isinstance(evidence, list) or not evidence:
        return _visibility_failure("visibility-no-evidence")
    for item in evidence:
        if not _require_artifact_ref(item)["valid"]:
            return _visibility_failure("visibility-no-evidence")
    return {"valid": True, "errors": [], "rule_id": "visibility-contributor-valid"}


def _require_visibility_resolution(value) -> dict:
    if not isinstance(value, dict) or set(value) != VISIBILITY_RESOLUTION_FIELDS:
        return _visibility_failure("visibility-missing-resolution")
    if not isinstance(value["resolution_id"], str) or not value["resolution_id"].strip():
        return _visibility_failure("visibility-missing-resolution")
    if not _require_timestamp(value["resolved_at"])["valid"]:
        return _visibility_failure("visibility-missing-resolution")
    contributors = value["contributors"]
    if not isinstance(contributors, list) or not contributors:
        return _visibility_failure("visibility-empty-contributors")
    identities = {}
    reference_identities = {}
    for contributor in contributors:
        result = _require_visibility_contributor(contributor)
        if not result["valid"]:
            return result
        contributor_id = contributor["contributor_id"]
        if contributor_id in identities:
            return _visibility_failure("visibility-contributor-conflict")
        identities[contributor_id] = contributor["asserted_visibility"]
        reference_identity = (
            contributor["contributor_kind"],
            contributor["contributor_ref"]["artifact_id"],
            contributor["contributor_ref"]["artifact_kind"],
        )
        if reference_identity in reference_identities:
            return _visibility_failure("visibility-contributor-conflict")
        reference_identities[reference_identity] = contributor_id
    if value["resolution_rule"] != "most_restrictive":
        return _visibility_failure("visibility-downgrade")
    if value["resolved_visibility"] not in VISIBILITY_ORDER:
        return _visibility_failure("visibility-invalid-value")
    expected_visibility = max(
        (contributor["asserted_visibility"] for contributor in contributors),
        key=VISIBILITY_ORDER.__getitem__,
    )
    if value["resolved_visibility"] != expected_visibility:
        return _visibility_failure("visibility-downgrade")
    audit = value["resolution_audit"]
    if not isinstance(audit, dict) or set(audit) != VISIBILITY_AUDIT_FIELDS:
        return _visibility_failure("visibility-audit-counts-mismatch")
    if audit["applied_rule"] != "most_restrictive":
        return _visibility_failure("visibility-audit-counts-mismatch")
    expected_counts = {
        "contributor_count": len(contributors),
        "restricted_count": sum(c["asserted_visibility"] == "restricted" for c in contributors),
        "project_count": sum(c["asserted_visibility"] == "project" for c in contributors),
        "public_count": sum(c["asserted_visibility"] == "public" for c in contributors),
    }
    if any(type(audit[name]) is not int or audit[name] != count for name, count in expected_counts.items()):
        return _visibility_failure("visibility-audit-counts-mismatch")
    return {"valid": True, "errors": [], "rule_id": "visibility-resolution-valid"}


def _require_visibility_context(value) -> dict:
    if not isinstance(value, dict) or set(value) != VISIBILITY_CONTEXT_FIELDS:
        return _visibility_failure("visibility-context-missing")
    trigger = _require_visibility_contributor(value["trigger_visibility"])
    if not trigger["valid"]:
        return trigger
    if value["trigger_visibility"]["contributor_kind"] != "trigger_provenance":
        return _visibility_failure("visibility-contributor-missing")
    for field, expected_kind in (
        ("policy_contributors", "project_policy"),
        ("contract_contributors", "governing_contract"),
    ):
        contributors = value[field]
        if not isinstance(contributors, list) or not contributors:
            return _visibility_failure("visibility-contributor-missing")
        for contributor in contributors:
            result = _require_visibility_contributor(contributor)
            if not result["valid"]:
                return result
            if contributor["contributor_kind"] != expected_kind:
                return _visibility_failure("visibility-contributor-missing")
    resolution = _require_visibility_resolution(value["run_visibility_resolution"])
    if not resolution["valid"]:
        return resolution
    context_contributors = [
        value["trigger_visibility"], *value["policy_contributors"], *value["contract_contributors"]
    ]
    context_by_id = {item["contributor_id"]: item for item in context_contributors}
    if len(context_by_id) != len(context_contributors):
        return _visibility_failure("visibility-contributor-conflict")
    resolution_contributors = value["run_visibility_resolution"]["contributors"]
    resolution_by_id = {item["contributor_id"]: item for item in resolution_contributors}
    if set(resolution_by_id) != set(context_by_id):
        return _visibility_failure("visibility-contributor-missing")
    if any(resolution_by_id[key] != contributor for key, contributor in context_by_id.items()):
        return _visibility_failure("visibility-contributor-conflict")
    resolved = value["resolved_run_visibility"]
    if resolved not in VISIBILITY_ORDER:
        return _visibility_failure("visibility-invalid-value")
    if resolved != value["run_visibility_resolution"]["resolved_visibility"]:
        return _visibility_failure("visibility-downgrade")
    return {"valid": True, "errors": [], "rule_id": "visibility-context-valid"}


def _require_runtime_artifact(value, run_id: str, stage_id: str) -> dict:
    if not isinstance(value, dict):
        return _visibility_failure("visibility-not-runtime-artifact")
    missing = RUNTIME_ARTIFACT_REQUIRED_FIELDS - set(value)
    if missing:
        return _visibility_failure("visibility-missing-fields:" + ",".join(sorted(missing)))
    extra = set(value) - RUNTIME_ARTIFACT_FIELDS
    if extra:
        return _visibility_failure("visibility-extra-fields:" + ",".join(sorted(extra)))
    if not _require_artifact_ref(value["artifact_ref"])["valid"]:
        return _visibility_failure("visibility-incomplete-artifact-ref")
    if value["origin_run"] != run_id:
        return _visibility_failure("visibility-origin-run-mismatch")
    if value.get("origin_stage") != stage_id:
        return _visibility_failure("visibility-origin-stage-mismatch")
    if not isinstance(value["produced_by"], str) or not value["produced_by"].strip():
        return _visibility_failure("visibility-missing-fields:produced_by")
    if not isinstance(value["source_artifacts"], list):
        return _visibility_failure("visibility-source-omission")
    for source in value["source_artifacts"]:
        if not _require_artifact_ref(source)["valid"]:
            return _visibility_failure("visibility-source-omission")
    if value["visibility"] not in VISIBILITY_ORDER:
        return _visibility_failure("visibility-invalid-value")
    resolution_value = value["visibility_resolution"]
    if not isinstance(resolution_value, dict):
        return _visibility_failure("visibility-missing-resolution")
    contributors = resolution_value.get("contributors")
    if not isinstance(contributors, list) or not contributors:
        return _visibility_failure("visibility-empty-contributors")
    contributor_ids = []
    for contributor in contributors:
        if not isinstance(contributor, dict):
            return _visibility_failure("visibility-contributor-missing")
        evidence = contributor.get("classification_evidence")
        if not isinstance(evidence, list) or not evidence:
            return _visibility_failure("visibility-no-evidence")
        if contributor.get("asserted_visibility") not in VISIBILITY_ORDER:
            return _visibility_failure("visibility-invalid-value")
        contributor_id = contributor.get("contributor_id")
        if not isinstance(contributor_id, str) or not contributor_id.strip():
            return _visibility_failure("visibility-missing-contributor-id")
        contributor_ids.append(contributor_id)
    if len(contributor_ids) != len(set(contributor_ids)):
        return _visibility_failure("visibility-contributor-conflict")
    if resolution_value.get("resolved_visibility") != value["visibility"]:
        return _visibility_failure("visibility-resolved-mismatch")
    audit = resolution_value.get("resolution_audit")
    counts = {
        "contributor_count": len(contributors),
        "restricted_count": sum(c["asserted_visibility"] == "restricted" for c in contributors),
        "project_count": sum(c["asserted_visibility"] == "project" for c in contributors),
        "public_count": sum(c["asserted_visibility"] == "public" for c in contributors),
    }
    if not isinstance(audit, dict) or any(audit.get(field) != count for field, count in counts.items()):
        return _visibility_failure("visibility-audit-counts-mismatch")
    resolution = _require_visibility_resolution(resolution_value)
    if not resolution["valid"]:
        return resolution
    source_identities = {
        (source["artifact_id"], source["artifact_kind"])
        for source in value["source_artifacts"]
    }
    contributor_identities = {
        (c["contributor_ref"]["artifact_id"], c["contributor_ref"]["artifact_kind"])
        for c in value["visibility_resolution"]["contributors"]
        if c["contributor_kind"] == "source_artifact"
    }
    if not source_identities.issubset(contributor_identities):
        return _visibility_failure("visibility-source-omission")
    return {"valid": True, "errors": [], "rule_id": "visibility-runtime-artifact-valid"}


def _compute_resolved_run_visibility(visibility_context: dict, runtime_artifacts: list) -> str:
    current = visibility_context["resolved_run_visibility"]
    if current not in VISIBILITY_ORDER:
        raise _ReducerError("visibility-invalid-context-visibility")
    for artifact in runtime_artifacts:
        visibility = artifact["visibility"]
        if visibility not in VISIBILITY_ORDER:
            raise _ReducerError("visibility-invalid-value")
        if VISIBILITY_ORDER[visibility] > VISIBILITY_ORDER[current]:
            current = visibility
    return current


def _require_error(value, rule_id="typed-error"):
    if not isinstance(value, dict) or not {"code", "message"}.issubset(value) or not set(value).issubset({"code", "message", "stack"}):
        return {"valid": False, "errors": [f"{rule_id}: invalid error shape"], "rule_id": rule_id}
    if not isinstance(value["code"], str) or not value["code"]:
        return {"valid": False, "errors": [f"{rule_id}: invalid error.code"], "rule_id": rule_id}
    if not isinstance(value["message"], str) or not value["message"]:
        return {"valid": False, "errors": [f"{rule_id}: invalid error.message"], "rule_id": rule_id}
    if "stack" in value:
        if not isinstance(value["stack"], str) or not value["stack"]:
            return {"valid": False, "errors": [f"{rule_id}: invalid error.stack"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


def _require_lineage_shape(value, rule_id="typed-lineage"):
    required = {"parent_run_id", "lineage_kind", "lineage_reason", "parent_status", "parent_boundary_event_id", "parent_boundary_event_type", "parent_boundary_event_order"}
    if not isinstance(value, dict) or set(value) != required:
        return {"valid": False, "errors": [f"{rule_id}: incorrect fields"], "rule_id": rule_id}
    for field in required - {"parent_boundary_event_order"}:
        if not isinstance(value[field], str) or not value[field]:
            return {"valid": False, "errors": [f"{rule_id}: non-empty string required for {field}"], "rule_id": rule_id}
    if type(value["parent_boundary_event_order"]) is not int or value["parent_boundary_event_order"] < 1:
        return {"valid": False, "errors": [f"{rule_id}: parent_boundary_event_order invalid"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


def _require_stage_graph(value, rule_id="typed-stage-graph"):
    required = {"graph_id", "stages", "edges", "entry_stages", "terminal_stages"}
    if not isinstance(value, dict) or set(value) != required:
        return {"valid": False, "errors": [f"{rule_id}: incorrect top-level fields, expected {sorted(required)}"], "rule_id": rule_id}
    if not isinstance(value["graph_id"], str) or not value["graph_id"]:
        return {"valid": False, "errors": [f"{rule_id}: graph_id must be non-empty string"], "rule_id": rule_id}
    if not isinstance(value["stages"], list) or not value["stages"]:
        return {"valid": False, "errors": [f"{rule_id}: stages must be non-empty array"], "rule_id": rule_id}

    stage_ids = []
    for stage in value["stages"]:
        if not isinstance(stage, dict) or not {"stage_id", "name", "required", "status"}.issubset(stage):
            return {"valid": False, "errors": [f"{rule_id}: stage missing required fields"], "rule_id": rule_id}
        if not set(stage).issubset({"stage_id", "name", "required", "status", "gates"}):
            return {"valid": False, "errors": [f"{rule_id}: stage has extra fields"], "rule_id": rule_id}
        if not isinstance(stage["stage_id"], str) or not stage["stage_id"]:
            return {"valid": False, "errors": [f"{rule_id}: stage stage_id invalid"], "rule_id": rule_id}
        if not isinstance(stage["name"], str) or not stage["name"]:
            return {"valid": False, "errors": [f"{rule_id}: stage name invalid"], "rule_id": rule_id}
        if type(stage["required"]) is not bool:
            return {"valid": False, "errors": [f"{rule_id}: stage required must be bool"], "rule_id": rule_id}
        if stage["status"] != "pending":
            return {"valid": False, "errors": [f"{rule_id}: genesis stage status must be pending"], "rule_id": rule_id}
        stage_ids.append(stage["stage_id"])
        gates = stage.get("gates", [])
        if not isinstance(gates, list):
            return {"valid": False, "errors": [f"{rule_id}: gates must be array"], "rule_id": rule_id}
        gate_ids = set()
        for gate in gates:
            if not isinstance(gate, dict) or not {"gate_id", "gate_type", "required", "failure_behavior"}.issubset(gate):
                return {"valid": False, "errors": [f"{rule_id}: gate missing required fields"], "rule_id": rule_id}
            if not set(gate).issubset({"gate_id", "gate_type", "required", "failure_behavior", "allow_gate_override", "contract_ref"}):
                return {"valid": False, "errors": [f"{rule_id}: gate has extra fields"], "rule_id": rule_id}
            if not isinstance(gate["gate_id"], str) or not gate["gate_id"]:
                return {"valid": False, "errors": [f"{rule_id}: gate_id invalid"], "rule_id": rule_id}
            if not isinstance(gate["gate_type"], str) or not gate["gate_type"]:
                return {"valid": False, "errors": [f"{rule_id}: gate_type invalid"], "rule_id": rule_id}
            if type(gate["required"]) is not bool:
                return {"valid": False, "errors": [f"{rule_id}: gate required must be bool"], "rule_id": rule_id}
            if gate["failure_behavior"] not in {"halt_stage", "halt_run", "warn", "require_intervention"}:
                return {"valid": False, "errors": [f"{rule_id}: invalid failure_behavior"], "rule_id": rule_id}
            if "allow_gate_override" in gate:
                if type(gate["allow_gate_override"]) is not bool:
                    return {"valid": False, "errors": [f"{rule_id}: allow_gate_override must be bool"], "rule_id": rule_id}
            if gate["gate_type"] == "validator":
                if "contract_ref" not in gate:
                    return {"valid": False, "errors": [f"{rule_id}: validator gate requires contract_ref"], "rule_id": rule_id}
                r = _require_artifact_ref(gate["contract_ref"])
                if not r["valid"]:
                    return r
                if not gate["contract_ref"].get("locator"):
                    return {"valid": False, "errors": [f"{rule_id}: contract_ref locator unresolved"], "rule_id": rule_id}
            if gate["gate_id"] in gate_ids:
                return {"valid": False, "errors": [f"{rule_id}: duplicate gate_id"], "rule_id": rule_id}
            gate_ids.add(gate["gate_id"])
    if len(stage_ids) != len(set(stage_ids)):
        return {"valid": False, "errors": [f"{rule_id}: duplicate stage_id"], "rule_id": rule_id}

    r = _require_string_array(value["entry_stages"], allow_empty=False, rule_id=rule_id)
    if not r["valid"]:
        return r
    r = _require_string_array(value["terminal_stages"], allow_empty=False, rule_id=rule_id)
    if not r["valid"]:
        return r
    if not set(value["entry_stages"] + value["terminal_stages"]).issubset(stage_ids):
        return {"valid": False, "errors": [f"{rule_id}: entry/terminal stages not in stage_ids"], "rule_id": rule_id}
    if not isinstance(value["edges"], list):
        return {"valid": False, "errors": [f"{rule_id}: edges must be array"], "rule_id": rule_id}
    for edge in value["edges"]:
        if not isinstance(edge, dict) or not {"from", "to"}.issubset(edge) or not set(edge).issubset({"from", "to", "condition"}):
            return {"valid": False, "errors": [f"{rule_id}: edge shape invalid"], "rule_id": rule_id}
        if edge["from"] not in stage_ids or edge["to"] not in stage_ids or edge["from"] == edge["to"]:
            return {"valid": False, "errors": [f"{rule_id}: edge references invalid stages"], "rule_id": rule_id}
        if edge.get("condition", "always") not in {"always", "on_pass", "on_fail", "on_skip"}:
            return {"valid": False, "errors": [f"{rule_id}: invalid edge condition"], "rule_id": rule_id}

    # Check graph is acyclic
    adjacency = {sid: [] for sid in stage_ids}
    for edge in value["edges"]:
        adjacency[edge["from"]].append(edge["to"])
    visiting = set()
    visited = set()

    def _visit(sid):
        if sid in visiting:
            return True
        if sid in visited:
            return False
        visiting.add(sid)
        for succ in adjacency[sid]:
            if _visit(succ):
                return True
        visiting.remove(sid)
        visited.add(sid)
        return False

    for sid in stage_ids:
        if _visit(sid):
            return {"valid": False, "errors": [f"{rule_id}: graph contains cycle"], "rule_id": rule_id}
    return {"valid": True, "errors": [], "rule_id": rule_id}


def _require_provenance(value, rule_id="typed-provenance"):
    if not isinstance(value, dict) or not {"origin_artifact", "governing_contracts"}.issubset(value) or not set(value).issubset({"origin_artifact", "origin_epic", "governing_contracts", "additional_sources"}):
        return {"valid": False, "errors": [f"{rule_id}: invalid provenance shape"], "rule_id": rule_id}
    r = _require_artifact_ref(value["origin_artifact"], rule_id)
    if not r["valid"]:
        return r
    if "origin_epic" in value:
        r = _require_artifact_ref(value["origin_epic"], rule_id)
        if not r["valid"]:
            return r
    r = _require_artifact_array(value["governing_contracts"], rule_id)
    if not r["valid"]:
        return r
    if not value["governing_contracts"]:
        return {"valid": False, "errors": [f"{rule_id}: governing_contracts must not be empty"], "rule_id": rule_id}
    r = _require_artifact_array(value.get("additional_sources", []), rule_id)
    if not r["valid"]:
        return r
    return {"valid": True, "errors": [], "rule_id": rule_id}


# ---------------------------------------------------------------------------
# RFC 8785 JCS canonical serialization
# ---------------------------------------------------------------------------

def _utf16_sort_key(key: str) -> bytes:
    return key.encode("utf-16-be")


def canonical_serialize(obj) -> bytes:
    """Canonicalize a value according to RFC 8785 JSON Canonicalization Scheme.
    Returns canonical UTF-8 bytes.
    """
    def _validate_string(s: str):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in s):
            raise ValueError("canonical: lone surrogate rejected")

    def _validate(item):
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, str):
            _validate_string(item)
            return
        if isinstance(item, int) and not isinstance(item, bool):
            if not (-(2 ** 53) + 1 <= item <= (2 ** 53) - 1):
                raise ValueError("canonical: integer out of I-JSON range")
            return
        if isinstance(item, list):
            for child in item:
                _validate(child)
            return
        if isinstance(item, dict):
            for key in item:
                if not isinstance(key, str):
                    raise ValueError("canonical: non-string object key")
                _validate_string(key)
            for child in item.values():
                _validate(child)
            return
        raise ValueError("canonical: unsupported type")

    def _serialize(item) -> str:
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
            return "[" + ",".join(_serialize(child) for child in item) + "]"
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + _serialize(item[key])
            for key in sorted(item, key=_utf16_sort_key)
        ) + "}"

    _validate(obj)
    return _serialize(obj).encode("utf-8")


def compute_digest(obj, exclude_fields=None) -> str:
    """Compute a sha256:<64 hex> digest over canonical bytes."""
    if exclude_fields is not None:
        preimage = {k: v for k, v in obj.items() if k not in exclude_fields}
    else:
        preimage = obj
    return "sha256:" + hashlib.sha256(canonical_serialize(preimage)).hexdigest()


# ---------------------------------------------------------------------------
# Append request validation
# ---------------------------------------------------------------------------

def validate_append_request(request: dict) -> dict:
    """Validate an AppendRequest shape. Returns dict with valid, errors, rule_id."""
    errors = []
    common = {"run_id", "event_type", "payload", "actor_role", "actor_identity",
              "trigger_artifact", "reason", "recommended_action",
              "expected_stream_head", "client_event_id", "prev_event_digest"}
    causation_present = {"causation_id", "causation_chain"} & set(request)
    extra = set(request) - (common | {"causation_id", "causation_chain"})
    if extra:
        errors.append("append-request-extra: " + ",".join(sorted(extra)))
    missing = common - set(request)
    if missing:
        errors.append("append-request-missing: " + ",".join(sorted(missing)))
    if len(causation_present) != 1:
        errors.append("append-request-causation: exactly one of causation_id or causation_chain required")
    if errors:
        return {"valid": False, "errors": errors, "rule_id": "append-request-shape"}

    for field in ("run_id", "event_type", "actor_identity", "reason", "client_event_id"):
        r = _require_string(request[field], f"append-request-string-{field}")
        if not r["valid"]:
            return r

    if request["event_type"] not in FROZEN_EVENT_TYPES:
        return {"valid": False, "errors": ["append-request-event-type unknown"], "rule_id": "append-request-event-type"}

    if request["actor_role"] not in {"runner", "architect", "validator", "planner", "human"}:
        return {"valid": False, "errors": ["append-request-actor-role invalid"], "rule_id": "append-request-actor-role"}

    if request["recommended_action"] not in {"none", "retry", "resume", "intervention_required", "more_evidence", "escalate"}:
        return {"valid": False, "errors": ["append-request-recommended-action invalid"], "rule_id": "append-request-recommended-action"}

    r = _require_artifact_ref(request["trigger_artifact"], "append-request-trigger-artifact")
    if not r["valid"]:
        return r

    head = request["expected_stream_head"]
    if not isinstance(head, dict) or set(head) != {"event_order", "content_digest"}:
        return {"valid": False, "errors": ["append-request-head-shape"], "rule_id": "append-request-head-shape"}
    r = _require_integer(head["event_order"], 0, "append-request-head-order")
    if not r["valid"]:
        return r
    r = _require_digest(head["content_digest"])
    if not r["valid"]:
        return r
    r = _require_digest(request["prev_event_digest"])
    if not r["valid"]:
        return r
    if "causation_id" in request:
        r = _require_string(request["causation_id"], "append-request-causation-id")
        if not r["valid"]:
            return r
    else:
        r = _require_string_array(request["causation_chain"], rule_id="append-request-causation-chain")
        if not r["valid"]:
            return r
    r = validate_payload(request["event_type"], request["payload"])
    if not r["valid"]:
        return r
    return {"valid": True, "errors": [], "rule_id": "append-request-valid"}


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

def validate_payload(event_type: str, payload: dict) -> dict:
    """Validate a payload against its named schema. Returns {valid, errors, rule_id}."""
    errors = []
    if event_type not in FROZEN_EVENT_TYPES:
        return {"valid": False, "errors": ["event-schema-missing"], "rule_id": "event-schema-missing"}
    if not isinstance(payload, dict):
        return {"valid": False, "errors": ["payload-object"], "rule_id": "payload-object"}

    schema = FROZEN_EVENT_SCHEMAS[event_type]
    required = set(schema["required"])
    conditional = set(schema.get("conditional", []))
    missing = required - set(payload)
    if missing:
        if event_type == "run.created" and "visibility_context" in missing:
            return _visibility_failure("visibility-context-missing")
        return {"valid": False, "errors": ["payload-required:" + ",".join(sorted(missing))], "rule_id": "payload-required"}
    extra = set(payload) - (required | conditional)
    if extra:
        return {"valid": False, "errors": ["payload-extra:" + ",".join(sorted(extra))], "rule_id": "payload-extra"}

    timestamp_fields = {"created_at", "started_at", "completed_at", "failed_at", "skipped_at",
                        "evaluated_at", "authorized_at", "terminated_at", "blocked_at", "interrupted_at"}
    positive_integer_fields = {"run_ordinal", "current_retry_count", "max_retries",
                               "checkpoint_event_order", "total_event_count", "last_event_order"}
    boolean_fields = {"retry_eligible", "checkpoint_available", "failure_is_transient", "failure_is_deterministic"}
    digest_fields = {"final_projection_digest"}
    artifact_array_fields = {"entry_evidence", "evidence", "required_evidence", "restored_evidence"}
    runtime_artifact_array_fields = {"artifacts_produced", "artifacts_produced_before_failure"}
    string_array_fields = {"terminal_stages_completed", "resolution_paths"}
    structured_fields = {"run_provenance", "stage_graph", "lineage", "revised_stage_graph", "error", "gate_decisions", "visibility_context"}
    all_fields = required | conditional
    string_fields = all_fields - timestamp_fields - positive_integer_fields - boolean_fields - digest_fields - artifact_array_fields - runtime_artifact_array_fields - string_array_fields - structured_fields

    for field in string_fields & set(payload):
        r = _require_string(payload[field], "typed-payload-string-" + field)
        if not r["valid"]:
            return r
    for field in timestamp_fields & set(payload):
        r = _require_timestamp(payload[field], "typed-payload-timestamp-" + field)
        if not r["valid"]:
            return r
    for field in positive_integer_fields & set(payload):
        r = _require_integer(payload[field], 1, "typed-payload-integer-" + field)
        if not r["valid"]:
            return r
    for field in boolean_fields & set(payload):
        r = _require_bool(payload[field], "typed-payload-bool-" + field)
        if not r["valid"]:
            return r
    for field in digest_fields & set(payload):
        r = _require_digest(payload[field])
        if not r["valid"]:
            return r
    for field in artifact_array_fields & set(payload):
        r = _require_artifact_array(payload[field], "typed-artifact-array-" + field)
        if not r["valid"]:
            return r
    for field in runtime_artifact_array_fields & set(payload):
        if not isinstance(payload[field], list):
            return _visibility_failure("visibility-not-runtime-artifact")
    for field in string_array_fields & set(payload):
        r = _require_string_array(payload[field], rule_id="typed-string-array-" + field)
        if not r["valid"]:
            return r

    if "run_provenance" in payload:
        r = _require_provenance(payload["run_provenance"])
        if not r["valid"]:
            return r
    if "stage_graph" in payload:
        r = _require_stage_graph(payload["stage_graph"])
        if not r["valid"]:
            return r
    if "revised_stage_graph" in payload:
        r = _require_stage_graph(payload["revised_stage_graph"])
        if not r["valid"]:
            return r
    if "lineage" in payload:
        r = _require_lineage_shape(payload["lineage"])
        if not r["valid"]:
            return r
        r = validate_lineage(payload["lineage"])
        if not r["valid"]:
            return r
    if "error" in payload:
        r = _require_error(payload["error"])
        if not r["valid"]:
            return r
    if "gate_decisions" in payload:
        if not isinstance(payload["gate_decisions"], list):
            return {"valid": False, "errors": ["typed-gate-decisions"], "rule_id": "typed-gate-decisions"}
        for decision in payload["gate_decisions"]:
            if isinstance(decision, str):
                if not decision:
                    return {"valid": False, "errors": ["typed-gate-decisions"], "rule_id": "typed-gate-decisions"}
            elif isinstance(decision, dict) and set(decision) == {"decision_id"}:
                r = _require_string(decision["decision_id"], "typed-gate-decisions")
                if not r["valid"]:
                    return r
            else:
                return {"valid": False, "errors": ["typed-gate-decisions"], "rule_id": "typed-gate-decisions"}

    if "visibility_context" in payload:
        r = _require_visibility_context(payload["visibility_context"])
        if not r["valid"]:
            return r

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
            return {"valid": False, "errors": [f"payload-enum-{field}"], "rule_id": f"payload-enum-{field}"}

    if "resolution_paths" in payload:
        if not set(payload["resolution_paths"]).issubset({"more_evidence", "contract_redesign", "human_intervention", "capability_restoration"}):
            return {"valid": False, "errors": ["payload-enum-resolution-paths"], "rule_id": "payload-enum-resolution-paths"}

    if event_type == "run.gate.blocked" and payload["outcome"] != "blocked":
        return {"valid": False, "errors": ["gate-blocked-outcome must be blocked"], "rule_id": "gate-blocked-outcome"}
    if event_type == "run.gate.overridden" and payload["outcome"] != "pass":
        return {"valid": False, "errors": ["gate-override-outcome must be pass"], "rule_id": "gate-override-outcome"}

    if "execution_mode" in payload:
        degraded = payload["execution_mode"] != "full"
        if degraded and "degradation_note" not in payload:
            return {"valid": False, "errors": ["gate-degradation-note-required"], "rule_id": "gate-degradation-note-required"}
        if not degraded and "degradation_note" in payload:
            return {"valid": False, "errors": ["gate-degradation-note-forbidden"], "rule_id": "gate-degradation-note-forbidden"}

    authority_domains = {
        "run.stage.skipped": {"human"},
        "run.gate.overridden": {"architect", "human"},
        "run.retry.initiated": {"architect", "human", "system"},
        "run.resumed": {"architect", "human"},
        "run.redesign": {"architect", "human"},
        "run.intervention": {"architect", "human"},
    }
    if event_type in authority_domains and payload["authorized_by"] not in authority_domains[event_type]:
        return {"valid": False, "errors": [f"payload-authority: {event_type} requires authorized_by in {authority_domains[event_type]}"], "rule_id": "payload-authority"}

    return {"valid": True, "errors": [], "rule_id": "payload-valid"}


# ---------------------------------------------------------------------------
# Append decision evaluation
# ---------------------------------------------------------------------------

def evaluate_append_decision(existing_events: list, request: dict, current_head: dict) -> dict:
    """Evaluate append decision per Section 2.2 of the contract.

    Returns a decision dict with `code` and contextual information.
    existing_events: list of stored RuntimeEvents for the run
    request: AppendRequest dict
    current_head: {event_order, content_digest} for the run
    """
    # 1. Check global idempotency
    # In this pure API the caller provides whether client_event_id exists.
    # The decision order is:
    # (a) idempotency lookup, (b) stale head, (c) hash chain, (d) full validation

    # 2. Stale head check
    if request["expected_stream_head"] != current_head:
        return {
            "code": "stale_head",
            "current_stream_head": copy.deepcopy(current_head),
            "last_stored_receipt": None,  # caller should provide this if available
        }

    # 3. Hash chain link
    if request["prev_event_digest"] != current_head["content_digest"]:
        return {"code": "hash_chain_link"}

    # 4. Validate request
    r = validate_append_request(request)
    if not r["valid"]:
        return {"code": "invalid_request", "errors": r["errors"], "rule_id": r["rule_id"]}

    # 5. Validate payload
    r = validate_payload(request["event_type"], request["payload"])
    if not r["valid"]:
        return {"code": "invalid_payload", "errors": r["errors"], "rule_id": r["rule_id"]}

    # 6. Check transition validity (reducer preconditions)
    prior = None
    prior_event = {}
    if existing_events:
        last = existing_events[-1]
        prior = last.get("next_state")
        prior_event = last

    try:
        _ = _reduce_event_internal(prior, request["event_type"], request["payload"], request["run_id"])
    except _ReducerError as e:
        return {"code": e.code}

    # 7. If previous event had a prior_state, check if this transition is valid
    # Additional transition checks based on payload-dependent requirements
    try:
        additional_check = _validate_payload_transition(request["event_type"], request["payload"], prior, prior_event)
        if additional_check is not None:
            return additional_check
    except _ReducerError as e:
        return {"code": e.code}

    return {"code": "ok"}


class _ReducerError(Exception):
    def __init__(self, code: str):
        self.code = code


# ---------------------------------------------------------------------------
# Core reducer: pure function of (prior_projection, event, event_type) -> next_projection
# ---------------------------------------------------------------------------

def _stage_for(states: dict, stage_id: str) -> dict:
    stage = states.get(stage_id)
    if stage is None:
        raise _ReducerError("reducer-stage-reference")
    return stage


def _gate_for(stage: dict, gate_id: str) -> dict:
    for gate in stage["gates"]:
        if gate["gate_id"] == gate_id:
            return gate
    raise _ReducerError("reducer-gate-reference")


def _payload_decision_ids(items: list) -> set:
    result = set()
    for item in items:
        if isinstance(item, str):
            result.add(item)
        elif isinstance(item, dict) and isinstance(item.get("decision_id"), str):
            result.add(item["decision_id"])
        else:
            raise _ReducerError("reducer-decision-reference")
    return result


def _validate_reevaluation(stage: dict, payload: dict):
    current = stage["gate_decisions"].get(payload["gate_id"])
    reference = payload.get("reevaluates_decision_id")
    if current is None:
        if reference is not None:
            raise _ReducerError("gate-reevaluation-without-prior")
        return
    if reference != current["decision_id"]:
        raise _ReducerError("gate-reevaluation-current-decision")
    if payload["decision_id"] == current["decision_id"]:
        raise _ReducerError("gate-reevaluation-new-decision-id")
    old_evidence = {canonical_serialize(item) for item in current["evidence"]}
    if not any(canonical_serialize(item) not in old_evidence for item in payload["evidence"]):
        raise _ReducerError("gate-reevaluation-new-evidence")


def _reduce_event_internal(prior: dict | None, event_type: str, payload: dict, run_id: str = "run-root") -> dict:
    """Internal reducer that raises _ReducerError for contract violations.
    Called by `apply_reducer` after external validation.
    """
    status = None if prior is None else prior["status"]
    rule = FROZEN_REDUCERS[event_type]
    allowed = rule["allowed_status"]
    allowed_set = set(allowed) if isinstance(allowed, list) else {allowed}
    if status not in allowed_set:
        raise _ReducerError("reducer-prior-status")

    if event_type == "run.created":
        graph = copy.deepcopy(payload["stage_graph"])
        visibility_context = copy.deepcopy(payload["visibility_context"])
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
        return {
            "run_id": run_id,
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

    if event_type == "run.started":
        if payload["executor_identity"] != next_state["executor_identity"]:
            raise _ReducerError("run-start-executor-identity")
        next_state["status"] = "active"
        next_state["started_at"] = payload["started_at"]
    elif event_type == "run.stage.started":
        stage = _stage_for(next_state["stage_states"], payload["stage_id"])
        if stage["status"] != "pending":
            raise _ReducerError("stage-start-transition")
        incoming = [edge for edge in next_state["stage_graph"]["edges"] if edge["to"] == payload["stage_id"]]
        if not incoming and payload["stage_id"] not in next_state["stage_graph"]["entry_stages"]:
            raise _ReducerError("stage-entry-graph")
        for edge in incoming:
            predecessor_status = _stage_for(next_state["stage_states"], edge["from"])["status"]
            condition = edge.get("condition", "always")
            allowed_predecessor = {
                "always": {"completed", "skipped"},
                "on_pass": {"completed"},
                "on_fail": {"failed"},
                "on_skip": {"skipped"},
            }.get(condition)
            if allowed_predecessor is None or predecessor_status not in allowed_predecessor:
                raise _ReducerError("stage-entry-predecessor")
        stage["status"] = "active"
        stage["started_at"] = payload["started_at"]
        stage["entry_evidence"] = copy.deepcopy(payload["entry_evidence"])
        next_state["current_stage_id"] = payload["stage_id"]
    elif event_type == "run.gate.evaluated":
        stage = _stage_for(next_state["stage_states"], payload["stage_id"])
        if stage["status"] != "active":
            raise _ReducerError("gate-stage-active")
        gate = _gate_for(stage, payload["gate_id"])
        if payload["outcome"] not in {"pass", "fail", "blocked", "inconclusive", "human_review_required"}:
            raise _ReducerError("gate-outcome")
        _validate_reevaluation(stage, payload)
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
            raise _ReducerError("required-gate-evidence")
        stage["gate_decisions"][payload["gate_id"]] = decision
        stage["gate_decision_history"].append(copy.deepcopy(decision))
    elif event_type == "run.gate.blocked":
        stage = _stage_for(next_state["stage_states"], payload["stage_id"])
        if stage["status"] != "active":
            raise _ReducerError("gate-stage-active")
        _gate_for(stage, payload["gate_id"])
        if payload["outcome"] != "blocked":
            raise _ReducerError("gate-blocked-outcome")
        _validate_reevaluation(stage, payload)
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
        stage = _stage_for(next_state["stage_states"], payload["stage_id"])
        if stage["status"] != "active":
            raise _ReducerError("gate-stage-active")
        gate = _gate_for(stage, payload["gate_id"])
        if gate["required"]:
            raise _ReducerError("required-gate-no-override")
        if gate.get("allow_gate_override") is not True:
            raise _ReducerError("optional-gate-override-contract")
        if payload["outcome"] != "pass":
            raise _ReducerError("gate-override-outcome")
        original = stage["gate_decisions"].get(payload["gate_id"])
        if original is None or original["decision_id"] != payload["original_decision_id"]:
            raise _ReducerError("gate-override-original-decision")
        intervention = next((item for item in next_state["interventions"] if item["intervention_id"] == payload["authorizing_intervention_id"]), None)
        if intervention is None or intervention["intervention_type"] != "override_gate" or intervention["authorized_by"] != payload["authorized_by"] or intervention["reason"] != payload["override_reason"]:
            raise _ReducerError("gate-override-intervention")
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
            raise _ReducerError("intervention-type")
        if payload["authorized_by"] not in {"architect", "human"}:
            raise _ReducerError("intervention-authority")
        if payload["intervention_type"] == "skip_stage" and payload["authorized_by"] != "human":
            raise _ReducerError("skip-stage-human-authority")
        if any(item["intervention_id"] == payload["intervention_id"] for item in next_state["interventions"]):
            raise _ReducerError("intervention-identity")
        next_state["interventions"].append(copy.deepcopy(payload))
    elif event_type == "run.stage.skipped":
        stage = _stage_for(next_state["stage_states"], payload["stage_id"])
        if stage["status"] != "pending":
            raise _ReducerError("stage-skip-transition")
        if stage["required"]:
            raise _ReducerError("required-stage-no-skip")
        if payload["authorized_by"] != "human":
            raise _ReducerError("skip-stage-human-authority")
        intervention = next((item for item in next_state["interventions"] if item["intervention_id"] == payload["authorizing_intervention_id"]), None)
        if intervention is None or intervention["intervention_type"] != "skip_stage" or intervention["authorized_by"] != "human" or intervention["reason"] != payload["reason"]:
            raise _ReducerError("stage-skip-intervention")
        stage["status"] = "skipped"
        stage["completed_at"] = payload["skipped_at"]
        stage["skip_reason"] = payload["reason"]
    elif event_type == "run.stage.completed":
        stage = _stage_for(next_state["stage_states"], payload["stage_id"])
        if stage["status"] != "active":
            raise _ReducerError("stage-complete-transition")
        for gate in stage["gates"]:
            decision = stage["gate_decisions"].get(gate["gate_id"])
            if decision is None:
                raise _ReducerError("stage-complete-gates-evaluated")
            if gate["required"] and (decision["outcome"] != "pass" or not decision.get("evidence")):
                raise _ReducerError("required-gate-pass-evidence")
            if not gate["required"] and decision["outcome"] != "pass":
                raise _ReducerError("optional-gate-requires-pass-or-override")
        expected_decisions = {decision["decision_id"] for decision in stage["gate_decisions"].values()}
        if _payload_decision_ids(payload["gate_decisions"]) != expected_decisions:
            raise _ReducerError("stage-complete-decision-reference")
        runtime_artifacts = []
        for raw in payload["artifacts_produced"]:
            result = _require_runtime_artifact(raw, run_id, payload["stage_id"])
            if not result["valid"]:
                raise _ReducerError(result["errors"][0])
            runtime_artifacts.append(copy.deepcopy(raw))
        stage["status"] = "completed"
        stage["completed_at"] = payload["completed_at"]
        stage["artifacts_produced"].extend(copy.deepcopy(payload["artifacts_produced"]))
        next_state["runtime_artifacts"].extend(runtime_artifacts)
        next_state["artifact_refs"] = [copy.deepcopy(artifact["artifact_ref"]) for artifact in next_state["runtime_artifacts"]]
        next_state["resolved_run_visibility"] = _compute_resolved_run_visibility(
            next_state["visibility_context"], next_state["runtime_artifacts"]
        )
        next_state["current_stage_id"] = None
    elif event_type == "run.stage.failed":
        stage = _stage_for(next_state["stage_states"], payload["stage_id"])
        if stage["status"] != "active":
            raise _ReducerError("stage-fail-transition")
        runtime_artifacts = []
        for raw in payload["artifacts_produced_before_failure"]:
            result = _require_runtime_artifact(raw, run_id, payload["stage_id"])
            if not result["valid"]:
                raise _ReducerError(result["errors"][0])
            runtime_artifacts.append(copy.deepcopy(raw))
        stage["status"] = "failed"
        stage["failed_at"] = payload["failed_at"]
        stage["error"] = copy.deepcopy(payload["error"])
        stage["failure_category"] = payload["failure_category"]
        stage["failure_is_transient"] = payload["failure_is_transient"]
        stage["failure_is_deterministic"] = payload["failure_is_deterministic"]
        stage["retry_eligible"] = payload["retry_eligible"]
        stage["artifacts_produced"].extend(copy.deepcopy(payload["artifacts_produced_before_failure"]))
        next_state["runtime_artifacts"].extend(runtime_artifacts)
        next_state["artifact_refs"] = [copy.deepcopy(artifact["artifact_ref"]) for artifact in next_state["runtime_artifacts"]]
        next_state["resolved_run_visibility"] = _compute_resolved_run_visibility(
            next_state["visibility_context"], next_state["runtime_artifacts"]
        )
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
            raise _ReducerError("run-completed-terminal-stage-list")
        if any(_stage_for(next_state["stage_states"], sid)["status"] != "completed" for sid in terminal_stages):
            raise _ReducerError("run-completed-terminal-stages")
        for stage in next_state["stage_states"].values():
            if stage["required"] and stage["status"] != "completed":
                raise _ReducerError("run-completed-required-stage")
            for gate in stage["gates"]:
                if gate["required"]:
                    decision = stage["gate_decisions"].get(gate["gate_id"])
                    if decision is None or decision["outcome"] != "pass" or not decision.get("evidence"):
                        raise _ReducerError("run-completed-required-gate")
        if payload["total_event_count"] != prior["events_count"] + 1:
            raise _ReducerError("run-completed-event-count")
        completion_preimage = {
            "run_id": prior["run_id"],
            "status": "completed",
            "run_provenance": prior["run_provenance"],
            "trigger": prior["trigger"],
            "executor_identity": prior["executor_identity"],
            "run_ordinal": prior["run_ordinal"],
            "stage_graph": prior["stage_graph"],
            "lineage": prior["lineage"],
            "stage_states": prior["stage_states"],
            "visibility_context": prior["visibility_context"],
            "runtime_artifacts": prior["runtime_artifacts"],
            "artifact_refs": prior["artifact_refs"],
            "resolved_run_visibility": prior["resolved_run_visibility"],
            "completed_at": payload["completed_at"],
            "terminal_stages_completed": payload["terminal_stages_completed"],
            "events_count": payload["total_event_count"],
        }
        computed = "sha256:" + hashlib.sha256(canonical_serialize(completion_preimage)).hexdigest()
        if payload["final_projection_digest"] != computed:
            raise _ReducerError("run-completed-projection-digest")
        next_state["status"] = "completed"
        next_state["completed_at"] = payload["completed_at"]
        next_state["final_projection_digest"] = payload["final_projection_digest"]
        next_state["declared_total_event_count"] = payload["total_event_count"]
    elif event_type == "run.failed":
        failed_stage = _stage_for(next_state["stage_states"], payload["failed_stage_id"])
        if failed_stage["status"] != "active":
            raise _ReducerError("run-failed-stage-active")
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
            raise _ReducerError("run-interrupted-event-order")
        next_state["status"] = "interrupted"
        next_state["interrupted_at"] = payload["interrupted_at"]
        next_state["last_event_order"] = payload["last_event_order"]
        next_state["interruption_cause"] = payload["interruption_cause"]
        next_state["checkpoint_available"] = payload["checkpoint_available"]
    elif event_type in {"run.retry.initiated", "run.resumed", "run.redesign"}:
        lineage = payload["lineage"]
        expected_kind = {"run.retry.initiated": "retry", "run.resumed": "resume", "run.redesign": "redesign"}[event_type]
        if lineage["lineage_kind"] != expected_kind:
            raise _ReducerError("lineage-action-kind")
        if lineage["parent_run_id"] != prior["run_id"] or lineage["parent_status"] != status:
            raise _ReducerError("lineage-action-parent")
        if not isinstance(payload["new_run_id"], str) or not payload["new_run_id"] or payload["new_run_id"] == prior["run_id"]:
            raise _ReducerError("lineage-action-child-id")
        if lineage["parent_boundary_event_id"] != prior.get("latest_event_id") or lineage["parent_boundary_event_type"] != prior.get("latest_event_type") or lineage["parent_boundary_event_order"] != prior.get("latest_event_order"):
            raise _ReducerError("lineage-action-boundary")
        if lineage["parent_boundary_event_type"] == "run.terminated":
            if prior.get("latest_terminal_status") is None:
                raise _ReducerError("lineage-boundary-terminal-status")
            validate_lineage(lineage, prior.get("latest_terminal_status"))
        if payload["authorized_by"] not in ({"architect", "human", "system"} if event_type == "run.retry.initiated" else {"architect", "human"}):
            raise _ReducerError("lineage-action-authority")
        if event_type == "run.retry.initiated":
            if not (1 <= payload["current_retry_count"] <= payload["max_retries"] <= 3):
                raise _ReducerError("retry-count-bounds")
            if payload["failure_category"] != prior.get("failure_category"):
                raise _ReducerError("retry-failure-category")
            if payload["authorized_by"] == "system":
                required_flags = payload.get("failure_is_transient") is True and payload.get("failure_is_deterministic") is True
                parent_flags = prior.get("failure_is_transient") is True and prior.get("failure_is_deterministic") is True
                if payload["current_retry_count"] >= payload["max_retries"] or payload["retry_strategy"] != "full" or payload["failure_category"] != "command_failed" or not required_flags or not parent_flags:
                    raise _ReducerError("system-auto-retry-policy")
            elif "failure_is_transient" in payload or "failure_is_deterministic" in payload:
                raise _ReducerError("manual-retry-failure-flags-forbidden")
        elif event_type == "run.resumed":
            if payload["checkpoint_event_order"] > lineage["parent_boundary_event_order"] or prior.get("checkpoint_available") is not True:
                raise _ReducerError("resume-checkpoint-boundary")
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
    elif event_type == "run.terminated":
        if payload["from_status"] != status:
            raise _ReducerError("run-terminated-from-status")
        if payload["terminal_status"] not in {"failed", "blocked"}:
            raise _ReducerError("run-terminated-terminal-status")
        if payload["terminated_by"] not in {"architect", "human"}:
            raise _ReducerError("run-terminated-authority")
        next_state["status"] = payload["terminal_status"]
        next_state["terminated_at"] = payload["terminated_at"]
        next_state["terminated_by"] = payload["terminated_by"]
        next_state["termination_reason"] = payload["termination_reason"]
    else:
        next_status = rule["next_status"]
        if next_status == "payload.terminal_status":
            next_state["status"] = payload["terminal_status"]
        elif next_status not in {"unchanged", status}:
            next_state["status"] = next_status

    next_state["events_count"] += 1
    return next_state


def _validate_payload_transition(event_type: str, payload: dict, prior: dict | None, prior_event: dict):
    """Additional payload-dependent transition validation beyond the reducer."""
    if prior is None:
        return None  # genesis always valid here

    if event_type == "run.completed":
        # completion digest validation done in reducer
        pass
    elif event_type == "run.terminated":
        if payload["from_status"] != prior["status"]:
            return {"code": "invalid_transition", "errors": ["terminated from_status mismatch"], "rule_id": "terminated-from-status"}
    return None


def apply_reducer(prior_projection: dict | None, event: dict, event_type: str | None = None) -> dict:
    """Apply reducer to produce the next projection.

    Returns the next projection dict.
    Raises ValueError for contract violations.
    """
    if event_type is None:
        event_type = event["event_type"]
    payload = event.get("payload", {})

    # Validate payload
    r = validate_payload(event_type, payload)
    if not r["valid"]:
        raise ValueError("invalid_payload: " + "; ".join(r["errors"]))

    try:
        result = _reduce_event_internal(prior_projection, event_type, payload, prior_projection.get("run_id") if prior_projection else event.get("run_id", "run-root"))
    except _ReducerError as e:
        raise ValueError(e.code)

    return result


def initial_projection(run_created_event: dict) -> dict:
    """Create initial projection from a run.created event."""
    validation = validate_payload("run.created", run_created_event.get("payload", {}))
    if not validation["valid"]:
        raise ValueError("invalid_payload: " + "; ".join(validation["errors"]))
    return _reduce_event_internal(None, "run.created", run_created_event["payload"], run_created_event.get("run_id", "run-root"))


# ---------------------------------------------------------------------------
# Store-assigned event metadata binding (shared by journal write and replay)
# ---------------------------------------------------------------------------

def bind_store_event_metadata(projection: dict, event) -> dict:
    """Bind store-assigned event identity onto a reducer next_state.

    Returns an INDEPENDENT deep copy of ``projection`` with the
    ``latest_event_id``, ``latest_event_type`` and ``latest_event_order``
    fields set from the actual stored RuntimeEvent. For a ``run.terminated``
    event the ``latest_terminal_status`` field is set from the payload; for
    every other event type ``latest_terminal_status`` is removed when present.

    The store-assigned contract forbids silent defaults: an empty event
    identity or a boolean/zero ``event_order`` raises instead of being
    substituted by a caller payload field or an inferred value. This helper is
    pure: it does not mutate the caller projection or the event, and it never
    reads hidden state, clocks, or the request payload for metadata.
    """
    if not isinstance(event, dict):
        raise ValueError("event-metadata-bind-event-type")
    event_id = event.get("event_id")
    event_type = event.get("event_type")
    event_order = event.get("event_order")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event-metadata-bind-empty-event-id")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("event-metadata-bind-empty-event-type")
    if type(event_order) is not int or event_order < 1:
        raise ValueError("event-metadata-bind-invalid-event-order")
    bound = copy.deepcopy(projection)
    bound["latest_event_id"] = event_id
    bound["latest_event_type"] = event_type
    bound["latest_event_order"] = event_order
    if event_type == "run.terminated":
        payload = event.get("payload")
        if not isinstance(payload, dict) or "terminal_status" not in payload:
            raise ValueError("event-metadata-bind-terminal-status-missing")
        bound["latest_terminal_status"] = payload["terminal_status"]
    else:
        bound.pop("latest_terminal_status", None)
    return bound


# ---------------------------------------------------------------------------
# Event digest
# ---------------------------------------------------------------------------

def compute_event_digest(stored_event: dict) -> str:
    """Compute content_digest for a stored RuntimeEvent (excluding content_digest)."""
    preimage = {k: v for k, v in stored_event.items() if k != "content_digest"}
    return compute_digest(preimage)


# ---------------------------------------------------------------------------
# Projection digest
# ---------------------------------------------------------------------------

def compute_projection_digest(projection: dict) -> str:
    """Compute digest of a RunProjection, excluding projection_digest, projection_id, derived_at."""
    return compute_digest(projection, exclude_fields={"projection_digest", "projection_id", "derived_at"})


def compute_completion_digest(projection: dict) -> str:
    """Compute the stable completion digest for run.completed verification."""
    preimage = {
        "run_id": projection["run_id"],
        "status": "completed",
        "run_provenance": projection["run_provenance"],
        "trigger": projection["trigger"],
        "executor_identity": projection["executor_identity"],
        "run_ordinal": projection["run_ordinal"],
        "stage_graph": projection["stage_graph"],
        "lineage": projection["lineage"],
        "stage_states": projection["stage_states"],
        "visibility_context": projection["visibility_context"],
        "runtime_artifacts": projection["runtime_artifacts"],
        "artifact_refs": projection["artifact_refs"],
        "resolved_run_visibility": projection["resolved_run_visibility"],
        "completed_at": projection["completed_at"],
        "terminal_stages_completed": projection["terminal_stages_completed"],
        "events_count": projection["declared_total_event_count"],
    }
    return compute_digest(preimage)


# ---------------------------------------------------------------------------
# Receipt signing and verification
# ---------------------------------------------------------------------------

DEFAULT_CONFORMANCE_KEY = b"runtime-state-conformance-key"


def sign_receipt(run_id: str, event_order: int, content_digest: str, key_bytes: bytes = DEFAULT_CONFORMANCE_KEY) -> dict:
    """Create a signed receipt with HMAC-SHA256, key_id=conformance-key-1.
    Returns the signed_receipt sub-object dict.
    """
    signed_payload = {
        "run_id": run_id,
        "event_order": event_order,
        "content_digest": content_digest,
    }
    signature = hmac.new(key_bytes, canonical_serialize(signed_payload), hashlib.sha256).hexdigest()
    return {
        "algorithm": "HMAC-SHA256",
        "key_id": "conformance-key-1",
        "signed_payload": signed_payload,
        "signature": signature,
    }


def verify_receipt(receipt: dict, expected_run_id: str, trusted_key_bytes: bytes = DEFAULT_CONFORMANCE_KEY) -> dict:
    """Verify a signed receipt. Returns dict with valid, errors, rule_id."""
    errors = []

    # Check envelope shape
    if set(receipt) != {"event_id", "event_order", "stored_content_digest", "new_stream_head", "signed_receipt"}:
        errors.append("signed-receipt-envelope-shape")
    signed = receipt.get("signed_receipt")
    if signed is None or set(signed) != {"algorithm", "key_id", "signed_payload", "signature"}:
        errors.append("signed-receipt-shape")
    if errors:
        return {"valid": False, "errors": errors, "rule_id": "signed-receipt-format"}

    if signed["algorithm"] != "HMAC-SHA256":
        errors.append("signed-receipt-algorithm")
    new_head = receipt["new_stream_head"]
    if set(new_head) != {"event_order", "content_digest"}:
        errors.append("signed-receipt-head-shape")
    if set(signed["signed_payload"]) != {"run_id", "event_order", "content_digest"}:
        errors.append("signed-receipt-payload-shape")
    if errors:
        return {"valid": False, "errors": errors, "rule_id": "signed-receipt-format"}

    # Type checks
    if not isinstance(receipt["event_id"], str) or not receipt["event_id"]:
        errors.append("signed-receipt-event-id")
    if type(receipt["event_order"]) is not int or receipt["event_order"] < 1:
        errors.append("signed-receipt-event-order")
    if not isinstance(receipt["stored_content_digest"], str) or DIGEST_RE.fullmatch(receipt["stored_content_digest"]) is None:
        errors.append("signed-receipt-stored-digest")
    if type(new_head["event_order"]) is not int or new_head["event_order"] < 1:
        errors.append("signed-receipt-head-order")
    if not isinstance(new_head["content_digest"], str) or DIGEST_RE.fullmatch(new_head["content_digest"]) is None:
        errors.append("signed-receipt-head-digest")
    if not isinstance(signed["key_id"], str) or not signed["key_id"]:
        errors.append("signed-receipt-key-id")
    if errors:
        return {"valid": False, "errors": errors, "rule_id": "signed-receipt-types"}

    # Signature format
    if not isinstance(signed["signature"], str) or not re.fullmatch(r"[0-9a-f]{64}", signed["signature"]):
        errors.append("signed-receipt-signature-hex64")
    if errors:
        return {"valid": False, "errors": errors, "rule_id": "signed-receipt-signature-format"}

    # Key resolution
    if signed["key_id"] != "conformance-key-1":
        errors.append("signed-receipt-key-id-resolution")

    # Payload type checks
    payload = signed["signed_payload"]
    if not isinstance(payload["run_id"], str) or not payload["run_id"]:
        errors.append("signed-receipt-run-id")
    if type(payload["event_order"]) is not int or payload["event_order"] < 1:
        errors.append("signed-receipt-payload-order")
    if not isinstance(payload["content_digest"], str) or DIGEST_RE.fullmatch(payload["content_digest"]) is None:
        errors.append("signed-receipt-payload-digest")
    if errors:
        return {"valid": False, "errors": errors, "rule_id": "signed-receipt-payload-types"}

    # Signature verification
    expected_sig = hmac.new(trusted_key_bytes, canonical_serialize(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, signed["signature"]):
        errors.append("signed-receipt-verification")
    if errors:
        return {"valid": False, "errors": errors, "rule_id": "signed-receipt-crypto"}

    # Bindings
    if payload["run_id"] != expected_run_id:
        errors.append("signed-receipt-run-context")
    if payload["event_order"] != new_head["event_order"]:
        errors.append("signed-receipt-head-binding")
    if payload["content_digest"] != new_head["content_digest"]:
        errors.append("signed-receipt-head-binding")
    if receipt["event_order"] != new_head["event_order"]:
        errors.append("signed-receipt-event-order-binding")
    if receipt["stored_content_digest"] != new_head["content_digest"]:
        errors.append("signed-receipt-content-digest-binding")
    if errors:
        return {"valid": False, "errors": errors, "rule_id": "signed-receipt-integrity"}

    return {"valid": True, "errors": [], "rule_id": "signed-receipt-valid"}


# ---------------------------------------------------------------------------
# Stream integrity verification
# ---------------------------------------------------------------------------

def verify_stream_integrity(events: list) -> dict:
    """Verify the integrity of an ordered event stream.
    Returns dict with valid, errors, events_verified, and head digest.
    """
    errors = []
    replayed = None
    previous_digest = ZERO_DIGEST
    events_verified = 0

    for expected_order, event in enumerate(events, 1):
        # Check event_order gap-free
        if type(event.get("event_order")) is not int or event["event_order"] != expected_order:
            errors.append(f"stream-event-order-gap at expected {expected_order}, got {event.get('event_order')}")
            break

        # Check hash chain link
        if event.get("prev_event_digest") != previous_digest:
            errors.append(f"stream-prev-event-digest-link at order {expected_order}")
            break

        # Check content_digest
        digest_preimage = copy.deepcopy(event)
        stored_digest = digest_preimage.pop("content_digest", None)
        computed = compute_digest(digest_preimage)
        if stored_digest != computed:
            errors.append(f"stream-content-digest at order {expected_order}, stored={stored_digest}, computed={computed}")
            break

        # Check prior_state replay
        expected_prior = {} if replayed is None else replayed
        if event.get("prior_state") != expected_prior:
            errors.append(f"stream-prior-state-replay at order {expected_order}")

        # Replay
        try:
            replayed = _reduce_event_internal(replayed, event["event_type"], copy.deepcopy(event["payload"]), event.get("run_id", ""))
        except _ReducerError as e:
            errors.append(f"stream-reducer-error at order {expected_order}: {e.code}")
            break
        except KeyError:
            errors.append(f"stream-missing-event-fields at order {expected_order}")
            break

        # Bind store-assigned metadata from the current stored event so the
        # prior_state replay check for the next event uses the same complete
        # projection that the journal write-path persisted.
        try:
            replayed = bind_store_event_metadata(replayed, event)
        except ValueError as e:
            errors.append(f"stream-event-metadata-bind at order {expected_order}: {e}")
            break

        previous_digest = stored_digest
        events_verified += 1

    head = {"event_order": events_verified, "content_digest": previous_digest}

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "rule_id": "stream-integrity-valid" if len(errors) == 0 else "stream-integrity-failure",
        "events_verified": events_verified,
        "head": head,
    }


# ---------------------------------------------------------------------------
# Lineage validation
# ---------------------------------------------------------------------------

def validate_lineage(lineage_payload: dict, parent_projection: dict | None = None,
                     parent_boundary_event: dict | None = None) -> dict:
    """Validate a lineage payload against lineage boundary rules.

    If parent_projection and parent_boundary_event are provided, validates
    against contract rules for the lineage kind and parent status.
    Otherwise performs structural validation only.
    """
    errors = []
    # Structural validation
    r = _require_lineage_shape(lineage_payload, "lineage-shape")
    if not r["valid"]:
        return r

    boundary_terminal_status = None

    # If we have a parent boundary event with terminal_status
    if parent_boundary_event is not None:
        boundary_terminal_status = parent_boundary_event.get("payload", {}).get("terminal_status")

    # Validate against known lineage boundaries
    boundary_kinds = {
        "retry": "retry",
        "resume": "resume",
        "more_evidence": "more_evidence",
    }
    matches = []
    for key, expected in LINEAGE_BOUNDARIES.items():
        expected_kind = expected.get("lineage_kind", boundary_kinds.get(key))
        if (lineage_payload["lineage_kind"] == expected_kind
                and lineage_payload["parent_status"] == expected["parent_status"]
                and lineage_payload["parent_boundary_event_type"] == expected["parent_boundary_event_type"]):
            matches.append(expected)

    if not matches:
        if lineage_payload["lineage_kind"] not in {"retry", "resume", "more_evidence", "redesign"}:
            errors.append("lineage-kind-unknown")
        if not any(lineage_payload["parent_status"] == item["parent_status"] for item in LINEAGE_BOUNDARIES.values()):
            errors.append("lineage-parent-status-mismatch")
        if not errors:
            errors.append("lineage-boundary-type-mismatch")
        return {"valid": False, "errors": errors, "rule_id": "lineage-boundary-mismatch"}

    expected = matches[0]
    required_terminal_status = expected.get("terminal_status")

    if boundary_terminal_status is not None and boundary_terminal_status != required_terminal_status:
        errors.append(f"lineage-boundary-terminal-status mismatch: expected {required_terminal_status}, got {boundary_terminal_status}")
    elif lineage_payload["parent_boundary_event_type"] == "run.terminated" and boundary_terminal_status is None:
        if required_terminal_status != lineage_payload["parent_status"]:
            errors.append("lineage-boundary-terminal-status-implied-mismatch")

    if not lineage_payload["parent_boundary_event_id"] or lineage_payload["parent_boundary_event_order"] < 1:
        errors.append("lineage-boundary-reference-invalid")

    if errors:
        return {"valid": False, "errors": errors, "rule_id": "lineage-invalid"}

    return {"valid": True, "errors": [], "rule_id": "lineage-valid"}


# ---------------------------------------------------------------------------
# Stage graph validation
# ---------------------------------------------------------------------------

def validate_stage_graph(graph: dict) -> dict:
    """Validate a StageGraph structure."""
    return _require_stage_graph(graph)
