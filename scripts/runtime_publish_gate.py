"""Pure publish-gate bridge v1.2.0.

Converts one accepted ValidatorMeshResult (v1.2) and caller-supplied Gate
request facts into exactly one canonical GateEvaluationRequest, then delegates
exactly once to ``evaluate_gate``. Returns the unchanged GateDecision or
GateEvaluationError.

Does not dispatch Validators, aggregate reports, append events, complete stages,
publish artifacts, or write lifecycle state. Every field origin is frozen:
caller copy, mesh copy, or contract-table derivation. No clock, random,
filesystem, workflow lookup, artifact dereference, or model inference.

The bridge is a deterministic projection of an already-returned
``ValidatorMeshResult``. It never revises the Mesh verdict, confidence,
requirement results, freshness assessments, or recommendation, and it never
mutates the Mesh result or the caller gate facts.

Public entry point: publish_to_gate(mesh_result, gate_facts) -> dict.
"""

import copy

from scripts.runtime_gate_decision import evaluate_gate

# ---------------------------------------------------------------------------
# Immutable contract tables (frozen from the mesh and Gate Decision contracts)
# ---------------------------------------------------------------------------

VALID_VERDICTS = frozenset(("pass", "fail", "blocked", "inconclusive", "human_review_required"))

# Active Runtime Validator Mesh contract version bound to bridge-generated refs.
MESH_CONTRACT_VERSION = "1.2.0"

EVIDENCE_CLASSIFICATION = {
    "pass": "complete",
    "fail": "complete",
    "blocked": "partial_recoverable",
    "inconclusive": "partial_absent",
    "human_review_required": "partial_absent",
}

GATE_FAILURE_CODE = {
    "fail": "validator_fail_deterministic",
    "blocked": "required_gate_blocked",
    "inconclusive": "contract_insufficient_inconclusive",
    "human_review_required": "contract_insufficient_human",
}

# ---------------------------------------------------------------------------
# Required fields on the accepted ValidatorMeshResult v1.2 (schema uses
# additionalProperties: false and defines exactly these ten fields)
# ---------------------------------------------------------------------------

_MESH_RESULT_REQUIRED = frozenset((
    "mesh_eval_id", "mesh_id", "aggregate_verdict", "aggregate_confidence",
    "report_bindings", "requirement_results", "freshness_assessments",
    "recommended_action", "evaluated_at", "evaluated_by",
))

# Required gate_facts fields
_GATE_FACTS_REQUIRED = frozenset((
    "run_context", "evaluated_at", "evaluated_by",
))

# Gate ArtifactRef keys accepted by Gate Decision v2.2.0. The Mesh-only
# ``digest`` is stripped during evidence projection.
_ARTIFACT_REF_KEYS = frozenset(("artifact_id", "artifact_kind", "artifact_version"))

# ---------------------------------------------------------------------------
# Bridge bridge-error construction (pre-call, no evaluate_gate)
# ---------------------------------------------------------------------------

_BRIDGE_ERROR_MESSAGE = "The publish bridge cannot construct a complete GateEvidenceEnvelope from the mesh evaluation result."


def _make_bridge_error(mesh_result):
    """Construct a ValidatorMeshEvaluationError with gate_bridge_construction_failed."""
    return {
        "mesh_eval_id": mesh_result.get("mesh_eval_id") if isinstance(mesh_result, dict) else None,
        "mesh_id": mesh_result.get("mesh_id") if isinstance(mesh_result, dict) else None,
        "error_code": "gate_bridge_construction_failed",
        "error_description": _BRIDGE_ERROR_MESSAGE,
        "run_context": None,
    }


# ---------------------------------------------------------------------------
# Structural validation helpers
# ---------------------------------------------------------------------------

def _is_nonempty_string(value):
    return isinstance(value, str) and len(value) > 0


def _has_complete_identity_triple(report_ref):
    """True when the report ref identity triple is three valid non-empty strings.

    A report ref may enter Gate evidence only when ``artifact_id``,
    ``artifact_kind``, and ``artifact_version`` are valid non-empty strings.
    A missing identity value is never synthesized.
    """
    if not isinstance(report_ref, dict):
        return False
    return (
        _is_nonempty_string(report_ref.get("artifact_id"))
        and _is_nonempty_string(report_ref.get("artifact_kind"))
        and _is_nonempty_string(report_ref.get("artifact_version"))
    )


# ---------------------------------------------------------------------------
# Bridge construction
# ---------------------------------------------------------------------------

def _build_evaluation_signal(mesh_result):
    """Construct the evaluation_signal for gate_type: validator from mesh result.

    Returns (signal_dict, None) on success, or (None, error_dict) on failure.
    """
    verdict = mesh_result.get("aggregate_verdict")
    if verdict not in VALID_VERDICTS:
        return None, _make_bridge_error(mesh_result)

    mesh_id = mesh_result.get("mesh_id")
    if not _is_nonempty_string(mesh_id):
        return None, _make_bridge_error(mesh_result)

    # report_ref: ArtifactRef for the mesh result itself, bound to the active
    # Mesh contract version. Gate Decision v2.2.0 requires this identity triple
    # to match evidence_envelope.validation_report exactly.
    report_ref = {
        "artifact_id": mesh_id,
        "artifact_kind": "report",
        "artifact_version": MESH_CONTRACT_VERSION,
    }

    signal = {
        "report_ref": copy.deepcopy(report_ref),
        "overall_verdict": verdict,
    }

    if verdict != "pass":
        failure_code = GATE_FAILURE_CODE.get(verdict)
        if failure_code is None:
            return None, _make_bridge_error(mesh_result)

        # A blocked verdict with no produced report bindings means the
        # dependency could not be dispatched or reached.
        bindings = mesh_result.get("report_bindings", [])
        if verdict == "blocked" and (not isinstance(bindings, list) or len(bindings) == 0):
            failure_code = "validator_unreachable"

        signal["failure_code"] = failure_code

    return signal, None


def _build_evidence_envelope(mesh_result, stage_id):
    """Construct the GateEvidenceEnvelope from a v1.2 mesh result.

    primary_evidence is built deterministically in returned ``report_bindings``
    order. A report ref may enter Gate evidence only when its identity triple
    (``artifact_id``, ``artifact_kind``, ``artifact_version``) consists of valid
    non-empty strings. Mesh-only ``digest`` is stripped; a missing identity
    value is never synthesized. Null/incomplete report refs are omitted from
    primary_evidence and their Mesh freshness facts are preserved through the
    deterministic non-empty ``missing_evidence_description``. The Mesh result
    is never mutated.

    Returns (envelope_dict, None) on success, or (None, error_dict) on failure.
    """
    mesh_eval_id = mesh_result.get("mesh_eval_id")
    if not _is_nonempty_string(mesh_eval_id):
        return None, _make_bridge_error(mesh_result)

    mesh_id = mesh_result.get("mesh_id")
    if not _is_nonempty_string(mesh_id):
        return None, _make_bridge_error(mesh_result)

    verdict = mesh_result.get("aggregate_verdict")
    if verdict not in VALID_VERDICTS:
        return None, _make_bridge_error(mesh_result)

    collected_at = mesh_result.get("evaluated_at")
    if not _is_nonempty_string(collected_at):
        return None, _make_bridge_error(mesh_result)

    collected_by = mesh_result.get("evaluated_by")
    if not _is_nonempty_string(collected_by):
        return None, _make_bridge_error(mesh_result)

    # envelope_id
    envelope_id = mesh_eval_id + "-gate-envelope"

    # gate_id
    gate_id = stage_id + "-validator-mesh-gate"

    # primary_evidence from report_bindings[].report_ref in declaration order.
    bindings = mesh_result.get("report_bindings", [])
    if not isinstance(bindings, list):
        bindings = []

    primary_evidence = []
    omitted_refs = []  # (binding_id, missing_fields) for refs omitted below
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        ref = binding.get("report_ref")
        if not _has_complete_identity_triple(ref):
            missing_fields = []
            for field in ("artifact_id", "artifact_kind", "artifact_version"):
                value = ref.get(field) if isinstance(ref, dict) else None
                if not _is_nonempty_string(value):
                    missing_fields.append("report_ref.%s" % field)
            omitted_refs.append((binding.get("binding_id", "unknown"), missing_fields))
            continue
        primary_evidence.append({
            "artifact_id": ref["artifact_id"],
            "artifact_kind": ref["artifact_kind"],
            "artifact_version": ref["artifact_version"],
        })

    # validation_report: ArtifactRef for the mesh result itself.
    validation_report = {
        "artifact_id": mesh_id,
        "artifact_kind": "report",
        "artifact_version": MESH_CONTRACT_VERSION,
    }

    # evidence_classification
    classification = EVIDENCE_CLASSIFICATION.get(verdict, "partial_absent")

    envelope = {
        "envelope_id": envelope_id,
        "gate_id": gate_id,
        "primary_evidence": primary_evidence,
        "supporting_evidence": [],
        "validation_report": copy.deepcopy(validation_report),
        "evidence_classification": classification,
        "collected_at": collected_at,
        "collected_by": collected_by,
    }

    # missing_evidence_description when classification is not complete
    if classification != "complete":
        missing_descriptions = []

        # Deterministic freshness facts by binding_id from the Mesh result.
        freshness_by_binding = {}
        assessments = mesh_result.get("freshness_assessments", [])
        if isinstance(assessments, list):
            for fa in assessments:
                if isinstance(fa, dict) and _is_nonempty_string(fa.get("binding_id")):
                    freshness_by_binding[fa["binding_id"]] = fa.get("freshness_status", "stale")

        # Omitted null/incomplete report refs preserve their Mesh freshness facts.
        described_bindings = set()
        for binding_id, missing_fields in omitted_refs:
            status = freshness_by_binding.get(binding_id)
            if status is not None:
                description = "binding %s: %s (%s)" % (
                    binding_id, status, ", ".join(missing_fields))
            else:
                description = "binding %s: incomplete report identity (%s)" % (
                    binding_id, ", ".join(missing_fields))
            missing_descriptions.append(description)
            described_bindings.add(binding_id)

        # Preserve the remaining non-current Mesh freshness facts.
        for binding_id, status in freshness_by_binding.items():
            if status == "current" or binding_id in described_bindings:
                continue
            missing_descriptions.append("binding %s: %s" % (binding_id, status))

        # If no freshness facts but still not complete, add a generic
        # description so the envelope satisfies schema constraints.
        if not missing_descriptions:
            missing_descriptions.append("validator mesh evidence is not complete")

        envelope["missing_evidence_description"] = missing_descriptions

    return envelope, None


def _build_gate_declaration(stage_id, failure_behavior):
    """Construct the GateDeclaration for the publish gate."""
    gate_id = stage_id + "-validator-mesh-gate"

    return {
        "gate_id": gate_id,
        "gate_type": "validator",
        "required": True,
        "allow_gate_override": False,
        "contract_ref": {
            "artifact_id": "runtime-validator-mesh-contract",
            "artifact_kind": "contract",
            "artifact_version": MESH_CONTRACT_VERSION,
        },
        "failure_behavior": failure_behavior,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def publish_to_gate(mesh_result, gate_facts):
    """Convert a validated ValidatorMeshResult v1.2 into one GateEvaluationRequest,
    delegate exactly once to ``evaluate_gate``, and return the result unchanged.

    Args:
        mesh_result: dict conforming to the accepted ValidatorMeshResult v1.2
            shape. Must contain the ten required mesh result fields.
        gate_facts: dict with caller-supplied Gate context:
            - run_context: {run_id, stage_id} (both non-empty)
            - evaluated_at: ISO 8601 timestamp string
            - evaluated_by: non-empty identity string

    Returns:
        dict: Exactly one GateDecision XOR GateEvaluationError.
            The returned object is a deep copy; callers are free to
            mutate it without affecting internal state.

        Returns a ValidatorMeshEvaluationError with
        gate_bridge_construction_failed when the mesh result or gate facts
        are insufficient to construct a complete GateEvaluationRequest.
    """
    # Validate mesh_result structural type
    if not isinstance(mesh_result, dict):
        return _make_bridge_error(mesh_result)

    # Validate all required mesh result fields are present
    missing_fields = [f for f in _MESH_RESULT_REQUIRED if f not in mesh_result]
    if missing_fields:
        return _make_bridge_error(mesh_result)

    # Validate mesh result is not already an error
    if "error_code" in mesh_result:
        return _make_bridge_error(mesh_result)

    # Validate gate_facts
    if not isinstance(gate_facts, dict):
        return _make_bridge_error(mesh_result)

    run_context = gate_facts.get("run_context")
    if not isinstance(run_context, dict):
        return _make_bridge_error(mesh_result)

    stage_id = run_context.get("stage_id")
    if not _is_nonempty_string(stage_id):
        return _make_bridge_error(mesh_result)

    run_id = run_context.get("run_id")
    if not _is_nonempty_string(run_id):
        return _make_bridge_error(mesh_result)

    evaluated_at = gate_facts.get("evaluated_at")
    if not _is_nonempty_string(evaluated_at):
        return _make_bridge_error(mesh_result)

    evaluated_by = gate_facts.get("evaluated_by")
    if not _is_nonempty_string(evaluated_by):
        return _make_bridge_error(mesh_result)

    # Validate verdict
    verdict = mesh_result.get("aggregate_verdict")
    if verdict not in VALID_VERDICTS:
        return _make_bridge_error(mesh_result)

    mesh_eval_id = mesh_result.get("mesh_eval_id")
    if not _is_nonempty_string(mesh_eval_id):
        return _make_bridge_error(mesh_result)

    # Build evaluation_signal
    signal, error = _build_evaluation_signal(mesh_result)
    if error is not None:
        return error

    # Build evidence envelope
    envelope, error = _build_evidence_envelope(mesh_result, stage_id)
    if error is not None:
        return error

    # The mesh result does not carry the declaration, so the failure behavior
    # is a frozen immutable bridge default compatible with required validator
    # gates (warn is forbidden for required gates).
    failure_behavior = "halt_run"

    # Build gate_declaration
    gate_decl = _build_gate_declaration(stage_id, failure_behavior)

    # Build GateEvaluationRequest
    decision_id = mesh_eval_id + "-gate-decision"

    gate_request = {
        "request_kind": "initial",
        "decision_id": decision_id,
        "evaluated_at": evaluated_at,
        "evaluated_by": evaluated_by,
        "gate_declaration": gate_decl,
        "evidence_envelope": envelope,
        "evaluation_signal": signal,
        "run_context": copy.deepcopy(run_context),
        "execution_mode": "full",
    }

    # Preserve input for post-call mutation check
    mesh_before = copy.deepcopy(mesh_result)
    facts_before = copy.deepcopy(gate_facts)

    # Delegate exactly once to evaluate_gate
    result = evaluate_gate(gate_request)

    # Verify caller objects unchanged (non-mutation probe)
    if mesh_result != mesh_before:
        raise RuntimeError("BUG: mesh_result was mutated during bridge construction")
    if gate_facts != facts_before:
        raise RuntimeError("BUG: gate_facts was mutated during bridge construction")

    # Return deep copy so caller receives independent object
    return copy.deepcopy(result)
