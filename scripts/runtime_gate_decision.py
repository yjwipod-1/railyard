"""Pure implementation of the frozen Runtime Gate Decision v2.2.0 contract.

This module deliberately has no imports: ``evaluate_gate`` is a deterministic,
side-effect-free transformation of JSON-compatible input.
"""

_TYPES = {"validator", "artifact_shape", "diff_review", "custom"}
_OUTCOMES = {"pass", "fail", "blocked", "inconclusive", "human_review_required"}
_ARTIFACT_KINDS = {"contract", "validation_report", "report", "artifact", "evidence", "custom_source", "diff"}
_FAILURE = {
    "validator_fail_deterministic": ("fail", "Validator found deterministic error-severity failures."),
    "validator_unreachable": ("blocked", "Independent Validator could not be dispatched or reached."),
    "validator_report_missing": ("blocked", "Validator was dispatched but no report was produced."),
    "evidence_incomplete": ("blocked", "Required evidence artifact is missing but recoverable."),
    "contract_unavailable": ("blocked", "Required Validation Contract could not be resolved."),
    "required_gate_blocked": ("blocked", "Required gate cannot proceed due to unavailable dependency."),
    "degradation_blocked_correctness": ("blocked", "Execution mode degraded below the correctness threshold."),
    "evidence_absent_inconclusive": ("inconclusive", "Required evidence artifact does not exist and cannot be produced."),
    "evidence_conflict_inconclusive": ("inconclusive", "Evidence from two or more independent sources contradicts."),
    "contract_insufficient_inconclusive": ("inconclusive", "Validation Contract is present but insufficient for a deterministic verdict."),
    "unsupported_judgment_inconclusive": ("inconclusive", "Gate requires a judgment the evaluation system cannot make."),
    "truth_hierarchy_inconclusive": ("inconclusive", "Validator used candidate output as truth source with no resolution path."),
    "evidence_absent_human": ("human_review_required", "Required evidence artifact does not exist and cannot be produced. Human review required."),
    "evidence_conflict_human": ("human_review_required", "Evidence from two or more independent sources contradicts. Human review required."),
    "contract_insufficient_human": ("human_review_required", "Validation Contract is present but insufficient for a deterministic verdict. Human review required."),
    "unsupported_judgment_human": ("human_review_required", "Gate requires a judgment the evaluation system cannot make. Human review required."),
    "truth_hierarchy_human": ("human_review_required", "Validator used candidate output as truth source with no resolution path. Human review required."),
}
_ERROR = {
    "invalid_override": "Gate override is invalid. Required gates cannot be overridden, or the gate does not allow override, or authorization is missing or incomplete.",
    "reevaluation_no_new_evidence": "Re-evaluation request contains no evidence not present in the prior evaluation.",
    "required_gate_warn_behavior": "Gate with required=true cannot use warn failure_behavior.",
    "report_reference_mismatch": "For validator gate_type, the evaluation_signal.report_ref and evidence_envelope.validation_report ArtifactRef identity triples do not match.",
    "invalid_evidence_classification": "The evidence classification value is not recognized or does not pair with the current verdict per the compatibility table.",
    "validator_verdict_mismatch": "The validator overall_verdict value is not a recognized five-value enumeration value.",
    "outcome_not_in_enum": "The computed outcome is not in the five-value closed enumeration.",
    "recommendation_not_in_matrix": "The (outcome, required, failure_behavior) combination is not in the recommendation matrix.",
    "failure_code_ambiguous": "Multiple failure codes apply or no single code resolves for this outcome and classification.",
    "failure_code_invalid": "The caller-supplied failure_code is not admissible for the (outcome, evidence_classification) pair per the failure-code matrix.",
    "invalid_input_branch": "The request contains a forbidden field for its branch, is missing a required field, or has an invalid request_kind value.",
    "unknown_gate_type": "The gate_type value is not one of the recognized closed enumeration values (validator, artifact_shape, diff_review, custom).",
}
_ADMISSIBLE = {
    ("fail", "complete"): {"validator_fail_deterministic"},
    ("blocked", "partial_recoverable"): {"evidence_incomplete", "validator_unreachable", "validator_report_missing", "contract_unavailable", "required_gate_blocked", "degradation_blocked_correctness"},
    ("inconclusive", "partial_absent"): {"evidence_absent_inconclusive", "contract_insufficient_inconclusive", "unsupported_judgment_inconclusive", "truth_hierarchy_inconclusive"},
    ("inconclusive", "conflicted"): {"evidence_conflict_inconclusive", "contract_insufficient_inconclusive", "unsupported_judgment_inconclusive", "truth_hierarchy_inconclusive"},
    ("human_review_required", "partial_absent"): {"evidence_absent_human", "contract_insufficient_human", "unsupported_judgment_human", "truth_hierarchy_human"},
    ("human_review_required", "conflicted"): {"evidence_conflict_human", "contract_insufficient_human", "unsupported_judgment_human", "truth_hierarchy_human"},
    ("human_review_required", "complete"): {"unsupported_judgment_human", "truth_hierarchy_human"},
    ("human_review_required", "partial_recoverable"): {"evidence_absent_human", "contract_insufficient_human", "unsupported_judgment_human", "truth_hierarchy_human"},
}

def _copy(value):
    if isinstance(value, dict): return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, list): return [_copy(v) for v in value]
    return value

def _nonempty(value): return isinstance(value, str) and bool(value)
def _artifact(value):
    return isinstance(value, dict) and set(value) <= {"artifact_id", "artifact_kind", "artifact_version"} and _nonempty(value.get("artifact_id")) and value.get("artifact_kind") in _ARTIFACT_KINDS and ("artifact_version" not in value or isinstance(value["artifact_version"], str))
def _identity(value): return (value["artifact_id"], value["artifact_kind"], value.get("artifact_version"))
def _context(value): return isinstance(value, dict) and set(value) == {"run_id", "stage_id"} and _nonempty(value.get("run_id")) and _nonempty(value.get("stage_id"))

def _error(request, code):
    request = request if isinstance(request, dict) else {}
    gate = request.get("gate_declaration")
    return {"decision_id": request.get("decision_id") if _nonempty(request.get("decision_id")) else None,
            "gate_id": gate.get("gate_id") if isinstance(gate, dict) and _nonempty(gate.get("gate_id")) else None,
            "error_code": code, "error_description": _ERROR[code],
            "run_context": _copy(request["run_context"]) if _context(request.get("run_context")) else None}

def _branch_ok(r):
    if not isinstance(r, dict) or r.get("request_kind") not in {"initial", "reevaluation", "override"}: return False
    common = {"request_kind", "decision_id", "evaluated_at", "evaluated_by", "gate_declaration", "run_context", "degradation_note"}
    kind = r["request_kind"]
    required = common - {"degradation_note"}
    allowed = set(common)
    if kind == "initial": required |= {"evidence_envelope", "evaluation_signal", "execution_mode"}; allowed |= {"evidence_envelope", "evaluation_signal", "execution_mode"}
    elif kind == "reevaluation": required |= {"evidence_envelope", "evaluation_signal", "previous_decision_snapshot", "prior_evidence_ids", "execution_mode"}; allowed |= {"evidence_envelope", "evaluation_signal", "previous_decision_snapshot", "prior_evidence_ids", "execution_mode"}
    else: required |= {"previous_decision", "override_authorization"}; allowed |= {"previous_decision", "override_authorization"}
    if not required <= set(r) or not set(r) <= allowed: return False
    if not (_nonempty(r.get("decision_id")) and isinstance(r.get("evaluated_at"), str) and _nonempty(r.get("evaluated_by")) and _context(r.get("run_context"))): return False
    if kind != "override":
        if r.get("execution_mode") not in {"full", "degraded_transport", "degraded_storage"}: return False
        if r["execution_mode"] != "full" and not _nonempty(r.get("degradation_note")): return False
    return True

def _gate(g):
    if not isinstance(g, dict) or set(g) - {"gate_id", "gate_type", "required", "allow_gate_override", "contract_ref", "failure_behavior"}: return False
    if not (_nonempty(g.get("gate_id")) and isinstance(g.get("required"), bool) and _nonempty(g.get("failure_behavior"))): return False
    if g["required"] is False and not isinstance(g.get("allow_gate_override"), bool): return False
    if g["required"] is True and g.get("allow_gate_override") is True: return False
    if g.get("gate_type") == "validator" and not (_artifact(g.get("contract_ref")) and g["contract_ref"]["artifact_kind"] == "contract"): return False
    return True

def _envelope(e, validator):
    if not isinstance(e, dict) or set(e) - {"envelope_id", "gate_id", "primary_evidence", "supporting_evidence", "validation_report", "evidence_classification", "missing_evidence_description", "collected_at", "collected_by"}: return False
    need = {"envelope_id", "gate_id", "primary_evidence", "evidence_classification", "collected_at", "collected_by"}
    if not need <= set(e) or not (_nonempty(e.get("envelope_id")) and _nonempty(e.get("gate_id")) and isinstance(e.get("primary_evidence"), list) and _nonempty(e.get("collected_at")) and _nonempty(e.get("collected_by"))): return False
    if not all(_artifact(x) for x in e["primary_evidence"]) or ("supporting_evidence" in e and (not isinstance(e["supporting_evidence"], list) or not all(_artifact(x) for x in e["supporting_evidence"]))): return False
    if validator and not _artifact(e.get("validation_report")): return False
    if "validation_report" in e and not _artifact(e.get("validation_report")): return False
    if not isinstance(e.get("evidence_classification"), str): return False
    if e["evidence_classification"] != "complete" and (not isinstance(e.get("missing_evidence_description"), list) or not all(_nonempty(x) for x in e["missing_evidence_description"])): return False
    return True

def _decision(value):
    """Validate a complete prior GateDecision without dereferencing it."""
    if not isinstance(value, dict): return False
    allowed = {"decision_id", "gate_id", "outcome", "execution_mode", "evidence", "recommendation", "failure_code", "failure_description", "evaluated_at", "evaluated_by", "degradation_note", "previous_decision_id", "override_authorization", "run_context"}
    required = {"decision_id", "gate_id", "outcome", "execution_mode", "evidence", "recommendation", "evaluated_at", "evaluated_by", "run_context"}
    if not required <= set(value) or not set(value) <= allowed: return False
    if not (_nonempty(value.get("decision_id")) and _nonempty(value.get("gate_id")) and value.get("outcome") in _OUTCOMES and value.get("execution_mode") in {"full", "degraded_transport", "degraded_storage"} and isinstance(value.get("evidence"), list) and all(_artifact(x) for x in value["evidence"]) and isinstance(value.get("evaluated_at"), str) and _nonempty(value.get("evaluated_by")) and _context(value.get("run_context"))): return False
    if value["execution_mode"] != "full" and not _nonempty(value.get("degradation_note")): return False
    if value["outcome"] in {"pass", "fail"} and not value["evidence"]: return False
    if value["outcome"] == "pass": return value.get("recommendation") == "proceed" and "failure_code" not in value and "failure_description" not in value
    if value["outcome"] == "blocked" and value.get("recommendation") != "more_evidence": return False
    if value["outcome"] in {"inconclusive", "human_review_required"} and value.get("recommendation") != "human_intervention": return False
    if value["outcome"] == "fail" and value.get("recommendation") not in {"stop_stage", "stop_run", "human_intervention", "proceed_with_warning"}: return False
    code = value.get("failure_code")
    return isinstance(code, str) and code in _FAILURE and _FAILURE[code][0] == value["outcome"] and value.get("failure_description") == _FAILURE[code][1]

def _project(e):
    values = list(e["primary_evidence"]) + list(e.get("supporting_evidence", [])) + ([e["validation_report"]] if e.get("validation_report") is not None else [])
    seen, result = set(), []
    for value in values:
        ident = _identity(value)
        if ident not in seen: seen.add(ident); result.append(_copy(value))
    return result

def _signal(s, typ):
    required_ref = {"validator": "report_ref", "artifact_shape": "artifact_ref", "diff_review": "diff_ref", "custom": "custom_source_ref"}[typ]
    outcome_key = "overall_verdict" if typ == "validator" else "outcome"
    if not isinstance(s, dict) or set(s) - {required_ref, outcome_key, "failure_code"} or not {required_ref, outcome_key} <= set(s) or not _artifact(s.get(required_ref)): return None
    outcome = s[outcome_key]
    if not isinstance(outcome, str): return "invalid_input_branch"
    if outcome in _OUTCOMES and (outcome == "pass") == ("failure_code" in s): return "invalid_input_branch"
    return outcome

def evaluate_gate(request: dict) -> dict:
    """Return exactly one GateDecision or GateEvaluationError for *request*."""
    if not _branch_ok(request): return _error(request, "invalid_input_branch")
    gate = request["gate_declaration"]
    if not isinstance(gate, dict) or gate.get("gate_type") not in _TYPES: return _error(request, "unknown_gate_type")
    if not _gate(gate): return _error(request, "invalid_input_branch")
    kind, typ = request["request_kind"], gate["gate_type"]
    if kind == "override":
        auth = request.get("override_authorization")
        previous = request.get("previous_decision")
        if gate["required"] or gate.get("allow_gate_override") is not True or not isinstance(auth, dict) or set(auth) != {"intervention_id", "authorized_by", "authorized_at", "reason"} or not all(_nonempty(auth.get(k)) for k in auth) or auth.get("authorized_by") not in {"architect", "human"} or not _decision(previous): return _error(request, "invalid_override")
        return {"decision_id": request["decision_id"], "gate_id": gate["gate_id"], "outcome": "pass", "execution_mode": "full", "evidence": _copy(previous["evidence"]), "recommendation": "proceed", "evaluated_at": request["evaluated_at"], "evaluated_by": request["evaluated_by"], "previous_decision_id": previous["decision_id"], "override_authorization": _copy(auth), "run_context": _copy(request["run_context"])}
    envelope = request["evidence_envelope"]
    if not _envelope(envelope, typ == "validator") or envelope["gate_id"] != gate["gate_id"]: return _error(request, "invalid_input_branch")
    projected = _project(envelope)
    signal = _signal(request["evaluation_signal"], typ)
    if signal is None or signal == "invalid_input_branch": return _error(request, "invalid_input_branch")
    if kind == "reevaluation":
        prior = request.get("prior_evidence_ids")
        snapshot = request.get("previous_decision_snapshot")
        if not isinstance(prior, list) or not all(isinstance(x, str) for x in prior) or not _decision(snapshot): return _error(request, "invalid_input_branch")
        if all(":".join(str(v) for v in _identity(x)) in prior for x in projected): return _error(request, "reevaluation_no_new_evidence")
    if gate["required"] and gate["failure_behavior"] == "warn": return _error(request, "required_gate_warn_behavior")
    if typ == "validator" and _identity(request["evaluation_signal"]["report_ref"]) != _identity(envelope["validation_report"]): return _error(request, "report_reference_mismatch")
    classification = envelope["evidence_classification"]
    if classification not in {"complete", "partial_recoverable", "partial_absent", "conflicted"}: return _error(request, "invalid_evidence_classification")
    permitted = _OUTCOMES if typ in {"validator", "custom"} else {"pass", "fail"}
    if signal not in permitted: return _error(request, "validator_verdict_mismatch" if typ == "validator" else "outcome_not_in_enum")
    outcome = signal
    allowed_classes = {"pass": {"complete"}, "fail": {"complete"}, "blocked": {"partial_recoverable"}, "inconclusive": {"partial_absent", "conflicted"}, "human_review_required": {"complete", "partial_recoverable", "partial_absent", "conflicted"}}
    if classification not in allowed_classes[outcome] or outcome in {"pass", "fail"} and not projected: return _error(request, "invalid_evidence_classification")
    behavior = gate["failure_behavior"]
    recommendation = {"pass": "proceed", "blocked": "more_evidence", "inconclusive": "human_intervention", "human_review_required": "human_intervention"}.get(outcome)
    if outcome == "fail": recommendation = {"halt_stage": "stop_stage", "halt_run": "stop_run", "require_intervention": "human_intervention", "warn": "proceed_with_warning"}.get(behavior)
    if recommendation is None: return _error(request, "recommendation_not_in_matrix")
    result = {"decision_id": request["decision_id"], "gate_id": gate["gate_id"], "outcome": outcome, "execution_mode": request["execution_mode"], "evidence": projected, "recommendation": recommendation, "evaluated_at": request["evaluated_at"], "evaluated_by": request["evaluated_by"], "run_context": _copy(request["run_context"])}
    if "degradation_note" in request: result["degradation_note"] = request["degradation_note"]
    if kind == "reevaluation": result["previous_decision_id"] = request["previous_decision_snapshot"]["decision_id"]
    if outcome != "pass":
        code = request["evaluation_signal"].get("failure_code")
        if not isinstance(code, str) or code not in _FAILURE: return _error(request, "failure_code_ambiguous")
        if code not in _ADMISSIBLE.get((outcome, classification), set()): return _error(request, "failure_code_invalid")
        result["failure_code"], result["failure_description"] = code, _FAILURE[code][1]
    return _copy(result)
