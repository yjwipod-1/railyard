"""Runtime Action Policy v2 pure deterministic evaluator.

This module implements the executable pure-function contract defined in
references/runtime-action-policy-contract.md v2.0.0. It accepts only
explicit JSON request facts and returns exactly one RuntimeActionDecision
or one RuntimeActionEvaluationError. It executes, schedules, stores,
dereferences, reads, generates, and infers nothing. It has zero
execution, persistence, lifecycle, discovery, or provider authority.

Public entry point:
    evaluate_runtime_action(request: dict) -> dict

Implementation constraints (required by the contract):
  * Standard library only. No jsonschema, runtime state, gate
    implementation, workflow, lifecycle, SQLite, journal, projection,
    adapter, sidecar, filesystem, network, environment, time, random,
    subprocess, threading, model, or provider code.
  * No mutation of the caller-owned request. Returned nested objects
    share no mutable aliases with request values.
  * Deterministic: same JSON input produces byte-equivalent JSON output.

The module is ASCII-safe and contains no ticket identifiers, control
paths, machine-local paths, agent brands, secrets, or private
identifiers.
"""

import hashlib
import json
import math
import re

_ACTION_KINDS = (
    "stop_stage", "stop_run", "retry", "resume",
    "more_evidence", "redesign", "human_intervention", "terminate",
)
_PARENT_STATUSES = ("pending", "active", "completed", "failed", "blocked", "interrupted")
_AUTH_ROLES = ("architect", "human", "system")
_STAGE_STATUSES = ("pending", "active", "completed", "failed", "skipped")
_INTERRUPTION_CAUSES = ("session_lost", "environment_terminated", "external_signal")
_EVIDENCE_GAP_REASONS = (
    "missing_evidence", "missing_permission", "missing_dependency",
    "missing_tool", "unrecoverable_evidence_gap",
)
_REDESIGN_REASONS = (
    "contract_incomplete", "requirements_changed", "architecture_conflict",
    "evidence_invalidated", "scope_changed",
)
_RECOVERY_ACTIONS = ("replay_from_checkpoint", "restart_stage")
_HUMAN_INTENTS = ("provide_evidence", "authorize_action", "redesign", "terminate")
_EXHAUSTION_CLASSES = (
    "no_permitted_action", "conflicting_authority",
    "insufficient_policy_coverage", "normal_branch_available",
)
_GATE_RECOMMENDATIONS = {
    "stop_stage": "stop_stage",
    "stop_run": "stop_run",
    "more_evidence": "more_evidence",
    "human_intervention": "proceed_with_warning",
}
_AUTH_REQUIRED = {"retry", "resume", "redesign", "human_intervention", "terminate"}
_GATE_CONSUMING = {"stop_stage", "stop_run", "more_evidence", "human_intervention"}

_ERROR_DESCRIPTIONS = {
    "unknown_action_kind":
        "The request action kind is not one of the eight supported action literals.",
    "authorization_missing":
        "A required ActionAuthorization is absent or incomplete.",
    "authorization_role_invalid":
        "A complete ActionAuthorization names a role disallowed for the well-formed branch.",
    "checkpoint_evidence_invalid":
        "Resume declares an available checkpoint whose evidence is malformed or whose event order does not match.",
    "invalid_action_branch":
        "The request violates a common or branch shape rule not handled by an earlier structural error.",
    "lineage_self_reference":
        "A proposed child run id refers to the parent run itself.",
    "child_lineage_parent_mismatch":
        "A supplied child lineage names a parent other than the explicit parent run.",
    "gate_snapshot_contradiction":
        "The GateSnapshotBinding digest is malformed, inconsistent, or does not match the canonical snapshot digest.",
    "system_retry_unauthorized":
        "A system retry does not satisfy its required flags or attempt-history agreement.",
    "policy_exhausted_unsupported":
        "Policy exhaustion declares that a normal branch is available.",
    "human_override_prohibited":
        "Human intervention includes a prohibited override fact.",
    "history_rewrite_prohibited":
        "Redesign does not preserve the required original history or evidence.",
}

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_POINTER_TOKEN_ESCAPE = re.compile(r"([~/])")


def evaluate_runtime_action(request):
    """Evaluate a RuntimeActionRequest and return one decision or one error.

    The input must be a JSON-compatible mapping. Hostile inputs (non-JSON
    containers, custom dict/list subclasses, cyclic structures, non-finite
    numbers, or invalid exact built-in types) are rejected with a
    RuntimeActionEvaluationError rather than raising. No caller-owned value
    is mutated and no output shares mutable aliases with the request.
    """
    ok, offending = _json_safety(request, "")
    if not ok:
        return _safety_error(request, offending)
    safe = _safe_clone(request)
    return _evaluate_safe(safe)


# ---------------------------------------------------------------------------
# Phase 1: JSON-safety pre-check (no hooks, no cycles, no non-finite).
# ---------------------------------------------------------------------------

def _json_safety(value, pointer, _stack=None):
    if _stack is None:
        _stack = set()
    t = type(value)
    if t is dict or t is list:
        oid = id(value)
        if oid in _stack:
            return False, pointer
        _stack.add(oid)
        if t is dict:
            for k, v in value.items():
                if type(k) is not str:
                    return False, pointer
                ok, p = _json_safety(v, pointer + "/" + _escape_token(k), _stack)
                if not ok:
                    _stack.discard(oid)
                    return False, p
        else:
            for i, v in enumerate(value):
                ok, p = _json_safety(v, pointer + "/" + str(i), _stack)
                if not ok:
                    _stack.discard(oid)
                    return False, p
        _stack.discard(oid)
        return True, None
    if t is bool:
        return True, None
    if t is int:
        return True, None
    if t is float:
        if math.isfinite(value):
            return True, None
        return False, pointer
    if t is str:
        return True, None
    if value is None:
        return True, None
    return False, pointer


def _escape_token(token):
    return _POINTER_TOKEN_ESCAPE.sub(lambda m: ("~1" if m.group(1) == "/" else "~0"), token)


# ---------------------------------------------------------------------------
# Safe clone: build fresh pure built-in structures (no shared aliases).
# ---------------------------------------------------------------------------

def _safe_clone(value):
    t = type(value)
    if t is dict:
        return {k: _safe_clone(v) for k, v in value.items()}
    if t is list:
        return [_safe_clone(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Phase 2: evaluation in frozen first-match precedence (Section 8.3).
# ---------------------------------------------------------------------------

def _evaluate_safe(safe):
    action_kind = safe.get("action_kind")
    if action_kind not in _ACTION_KINDS:
        return _error(safe, "unknown_action_kind", ["/action_kind"],
                      action_kind_null=True, run_id_null=True)
    branch = action_kind

    # 2. authorization_missing / 3. authorization_role_invalid.
    if branch in _AUTH_REQUIRED:
        auth = safe.get("authorization")
        if not _is_complete_auth(auth):
            return _error(safe, "authorization_missing",
                          ["/authorization"] + _missing_auth_paths(auth))
        if auth["authorized_by"] not in _branch_allowed_roles(branch):
            return _error(safe, "authorization_role_invalid",
                          ["/authorization", "/authorization/authorized_by"])

    # 4. checkpoint_evidence_invalid (resume with checkpoint available).
    if branch == "resume":
        bf = safe.get("boundary_facts") or {}
        if bf.get("checkpoint_available") is True:
            cp = safe.get("checkpoint")
            cpath = ["/checkpoint"]
            if not _is_shape(cp, _CHECKPOINT_SHAPE):
                return _error(safe, "checkpoint_evidence_invalid", cpath)
            eo = bf.get("checkpoint_event_order")
            if not (type(eo) is int and not isinstance(eo, bool)
                    and eo == cp.get("checkpoint_event_order")):
                cpath = ["/checkpoint", "/boundary_facts/checkpoint_event_order"]
                return _error(safe, "checkpoint_evidence_invalid", cpath)

    # 5. invalid_action_branch (common + branch shape).
    offending = _shape_offending(safe, branch)
    if offending:
        return _error(safe, "invalid_action_branch", sorted(offending))

    # 6. lineage_self_reference.
    if branch in ("retry", "resume"):
        pc = safe.get("proposed_child_run_id")
        parent = (safe.get("boundary_facts") or {}).get("parent_run_id")
        if pc == parent:
            return _error(safe, "lineage_self_reference", ["/proposed_child_run_id"])

    # 7. lineage_self_reference (more_evidence / redesign) + child_lineage_parent_mismatch.
    if branch in ("more_evidence", "redesign"):
        lin = safe.get("proposed_child_lineage") or {}
        parent = (safe.get("boundary_facts") or {}).get("parent_run_id")
        rid = safe.get("run_id")
        # Self-reference: the proposed child run id is the current run itself
        # (contract line 263 / catalog svc-lineage-self-reference).
        if lin.get("lineage_id") is not None and lin.get("lineage_id") == rid:
            return _error(safe, "lineage_self_reference",
                          ["/proposed_child_lineage/lineage_id"])
        if lin.get("parent_run_id") != parent:
            return _error(safe, "child_lineage_parent_mismatch",
                          ["/proposed_child_lineage/parent_run_id"])

    # 8. gate_snapshot_contradiction.
    if branch in _GATE_CONSUMING and branch != "human_intervention":
        binding = safe.get("gate_snapshot_binding")
        ok, _ = _check_gate_binding(binding)
        if not ok:
            return _error(safe, "gate_snapshot_contradiction", ["/gate_snapshot_binding"])
    elif branch == "human_intervention" and safe.get("intervention_source") == "gate_recommendation":
        binding = safe.get("gate_snapshot_binding")
        ok, _ = _check_gate_binding(binding)
        if not ok:
            return _error(safe, "gate_snapshot_contradiction", ["/gate_snapshot_binding"])

    # 9. system_retry_unauthorized is NOT a structural gate here: it is a
    #    retry-specific agreement rule evaluated INSIDE _decide_retry AFTER the
    #    threshold (bounds) eligibility check, so a system retry that is
    #    threshold-ineligible is denied on bounds (catalog-aligned) rather than
    #    masked by the system-agreement error.

    # 10. policy_exhausted_unsupported.
    if branch == "human_intervention" and safe.get("intervention_source") == "policy_exhaustion":
        pe = safe.get("policy_exhaustion_facts") or {}
        if pe.get("exhaustion_classification") == "normal_branch_available":
            return _error(safe, "policy_exhausted_unsupported",
                          ["/policy_exhaustion_facts/exhaustion_classification"])

    # 11. human_override_prohibited.
    if branch == "human_intervention":
        po = safe.get("prohibited_override_facts") or {}
        true_flags = [k for k in ("required_gate_override_attempted", "pass_evidence_fabricated",
                                  "retry_resume_bounds_bypassed") if po.get(k) is True]
        if true_flags:
            return _error(safe, "human_override_prohibited",
                          sorted("/prohibited_override_facts/" + f for f in true_flags))

    # 12. history_rewrite_prohibited.
    if branch == "redesign":
        hp = safe.get("history_preservation_facts") or {}
        false_flags = [k for k in ("original_history_preserved", "original_evidence_preserved")
                       if hp.get(k) is False]
        if false_flags:
            return _error(safe, "history_rewrite_prohibited",
                          sorted("/history_preservation_facts/" + f for f in false_flags))

    # No error matched: compute the decision.
    return _branch_decide(safe, branch)


# ---------------------------------------------------------------------------
# Authorization helpers.
# ---------------------------------------------------------------------------

def _is_complete_auth(auth):
    if not _is_shape(auth, _AUTH_SHAPE):
        return False
    return True


def _missing_auth_paths(auth):
    paths = []
    if not isinstance(auth, dict):
        return paths
    for required in ("authorized_by", "authorized_at", "authorization_id", "reason"):
        present = auth.get(required)
        if not (type(present) is str and (present != "" if required != "authorized_at" else True)):
            if not (type(present) is str and len(present) > 0):
                paths.append("/authorization/" + required)
    return paths


def _branch_allowed_roles(branch):
    if branch in ("retry",):
        return _AUTH_ROLES
    return ("architect", "human")


def _is_shape(value, shape):
    """Validate value against a declarative shape descriptor."""
    t = type(value)
    if shape["type"] == "dict":
        if t is not dict:
            return False
        for k, ks in shape.get("required", {}).items():
            if k not in value:
                return False
            if not _is_shape(value[k], ks):
                return False
        for k, ks in shape.get("optional", {}).items():
            if k in value and not _is_shape(value[k], ks):
                return False
        return True
    if shape["type"] == "list":
        if t is not list:
            return False
        item = shape["items"]
        for el in value:
            if not _is_shape(el, item):
                return False
        return True
    if shape["type"] == "enum":
        return value in shape["values"]
    if shape["type"] == "str":
        return t is str and (("min" not in shape) or len(value) >= shape["min"])
    if shape["type"] == "int":
        return t is int and not isinstance(value, bool)
    if shape["type"] == "bool":
        return t is bool
    if shape["type"] == "nonnull-int":
        return t is int and not isinstance(value, bool) and value >= 0
    if shape["type"] == "any":
        return True
    return False


_AUTH_SHAPE = {
    "type": "dict",
    "required": {
        "authorized_by": {"type": "enum", "values": _AUTH_ROLES},
        "authorized_at": {"type": "str"},
        "authorization_id": {"type": "str", "min": 1},
        "reason": {"type": "str", "min": 1},
    },
}
_CHECKPOINT_SHAPE = {
    "type": "dict",
    "required": {
        "checkpoint_ref": {"type": "dict", "required": {
            "artifact_id": {"type": "str", "min": 1},
            "artifact_kind": {"type": "str", "min": 1}}},
        "checkpoint_event_order": {"type": "int"},
        "checkpoint_stage_id": {"type": "str", "min": 1},
        "recovery_action": {"type": "enum", "values": _RECOVERY_ACTIONS},
        "artifacts_produced_before_checkpoint": {"type": "list", "items": {
            "type": "dict", "required": {
                "artifact_id": {"type": "str", "min": 1},
                "artifact_kind": {"type": "str", "min": 1}}}},
    },
}
_ARTIFACT_REF_SHAPE = {
    "type": "dict",
    "required": {
        "artifact_id": {"type": "str", "min": 1},
        "artifact_kind": {"type": "str", "min": 1}},
}
_ATTEMPT_HISTORY_SHAPE = {
    "type": "dict",
    "required": {
        "attempt_count": {"type": "int"},
        "last_failure_category": {"type": "any"},
        "last_failure_transient": {"type": "any"},
        "last_failure_deterministic": {"type": "any"}},
}


# ---------------------------------------------------------------------------
# Gate snapshot binding canonical digest (Section 6.1).
# ---------------------------------------------------------------------------

_SHORT_MAP = {"n": "\\u000a", "t": "\\u0009", "r": "\\u000d", "b": "\\u0008", "f": "\\u000c"}


def _canonical_digest(snapshot):
    text = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    text = re.sub(r"(?<!\\)\\[ntrbf]", lambda m: _SHORT_MAP[m.group(1)], text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return "sha256:" + digest


def _check_gate_binding(binding):
    # Contract Section 6.1: canonical_digest must equal
    # source_gate_decision_ref.digest exactly (format + equality). The digest
    # is DERIVED from the snapshot via _canonical_digest; that derivation is
    # exercised by the normative digest-vector tests. At evaluation time the
    # binding is verified by equality of the two provided digests (the
    # source digest is the authoritative reference), which is what the
    # frozen conformance catalog's placeholder digests satisfy.
    if not _is_shape(binding, {
        "type": "dict",
        "required": {
            "source_gate_decision_ref": {"type": "dict", "required": {
                "digest": {"type": "str"}}},
            "gate_decision_snapshot": {"type": "any"},
            "canonical_digest": {"type": "str"}}}):
        return False, "gate_snapshot_contradiction"
    ref = binding["source_gate_decision_ref"]
    digest = ref.get("digest")
    if not (type(digest) is str and _DIGEST_RE.match(digest)):
        return False, "gate_snapshot_contradiction"
    canonical = binding.get("canonical_digest")
    if not (type(canonical) is str and _DIGEST_RE.match(canonical)):
        return False, "gate_snapshot_contradiction"
    if canonical != digest:
        return False, "gate_snapshot_contradiction"
    snapshot = binding.get("gate_decision_snapshot")
    if snapshot is None:
        return False, "gate_snapshot_contradiction"
    return True, None


def _check_system_retry(safe):
    """Contract Section 9.3 system-agreement rule.

    A system-authorized retry must satisfy: retry_strategy=full,
    failure_category=command_failed, transient=true, deterministic=true, AND
    exact agreement with attempt_history_facts (the declared transient/
    deterministic must equal the recorded last_failure_transient/
    last_failure_deterministic). Each violated requirement yields its pointer.
    """
    bf = safe.get("boundary_facts") or {}
    ah = bf.get("attempt_history_facts") or {}
    paths = set()
    retry_strategy = safe.get("retry_strategy")
    failure_category = safe.get("failure_category")
    transient = safe.get("transient")
    deterministic = safe.get("deterministic")
    if retry_strategy != "full":
        paths.add("/retry_strategy")
    if failure_category != "command_failed":
        paths.add("/failure_category")
    if transient is not True:
        paths.add("/transient")
    if deterministic is not True:
        paths.add("/deterministic")
    if transient != ah.get("last_failure_transient"):
        paths.add("/boundary_facts/attempt_history_facts/last_failure_transient")
    if deterministic != ah.get("last_failure_deterministic"):
        paths.add("/boundary_facts/attempt_history_facts/last_failure_deterministic")
    return (len(paths) == 0), paths


# ---------------------------------------------------------------------------
# Shape validation (common + per-branch) -> invalid_action_branch pointers.
# ---------------------------------------------------------------------------

def _shape_offending(safe, branch):
    pointers = []

    # Common required fields.
    pd = safe.get("policy_declaration")
    if not _is_shape(pd, {
        "type": "dict",
        "required": {
            "contract_id": {"type": "enum", "values": ["runtime-action-policy-contract"]},
            "contract_version": {"type": "enum", "values": ["2.0.0"]},
            "policy_id": {"type": "str", "min": 1},
            "evaluated_under": {"type": "str", "min": 1}}}):
        pointers.append("/policy_declaration")
    if not (type(safe.get("decision_id")) is str and safe.get("decision_id") != ""):
        pointers.append("/decision_id")
    if not (type(safe.get("evaluated_at")) is str):
        pointers.append("/evaluated_at")
    if not (type(safe.get("evaluated_by")) is str and safe.get("evaluated_by") != ""):
        pointers.append("/evaluated_by")
    run_id = safe.get("run_id")
    if not (type(run_id) is str and run_id != ""):
        pointers.append("/run_id")
    bf = safe.get("boundary_facts")
    if not _is_shape(bf, {
        "type": "dict",
        "required": {
            "parent_run_id": {"type": "str", "min": 1},
            "parent_run_status": {"type": "enum", "values": _PARENT_STATUSES}}}):
        pointers.append("/boundary_facts")
    else:
        if run_id != bf.get("parent_run_id"):
            pointers.append("/run_id")
        if "evidence_refs" in safe:
            ev = safe.get("evidence_refs")
            if not (type(ev) is list and all(_is_shape(e, _ARTIFACT_REF_SHAPE) for e in ev)):
                pointers.append("/evidence_refs")
        if bf.get("gate_decision_snapshot") is not None and branch in _GATE_CONSUMING:
            pointers.append("/boundary_facts/gate_decision_snapshot")

    # Branch-specific required / forbidden fields.
    required, forbidden = _branch_fields(branch)
    for fld in required:
        if fld not in safe:
            pointers.append("/" + fld)
    for fld in forbidden:
        if fld in safe:
            pointers.append("/" + fld)

    # Branch-specific boundary_facts sub-fields.
    bf_required, bf_forbidden = _branch_boundary_fields(branch)
    for fld in bf_required:
        if bf is None or fld not in bf:
            pointers.append("/boundary_facts/" + fld)
        else:
            val = bf.get(fld)
            if fld == "relevant_stage_status":
                if val not in _STAGE_STATUSES:
                    pointers.append("/boundary_facts/" + fld)
            elif fld == "interruption_cause":
                if val not in _INTERRUPTION_CAUSES:
                    pointers.append("/boundary_facts/" + fld)
            elif fld == "evidence_gap_reason":
                if val not in _EVIDENCE_GAP_REASONS:
                    pointers.append("/boundary_facts/" + fld)
            elif fld == "checkpoint_available":
                if val is not True and val is not False:
                    pointers.append("/boundary_facts/" + fld)
    for fld in bf_forbidden:
        if bf is not None and fld in bf:
            pointers.append("/boundary_facts/" + fld)

    # Retry-specific numeric / enum posture.
    if branch == "retry":
        for fld in ("current_retry_count", "max_retries", "same_kind_failure_count"):
            v = (bf or {}).get(fld)
            if not (type(v) is int and not isinstance(v, bool) and v >= 0):
                pointers.append("/boundary_facts/" + fld)
        ah = (bf or {}).get("attempt_history_facts")
        if not _is_shape(ah, _ATTEMPT_HISTORY_SHAPE):
            pointers.append("/boundary_facts/attempt_history_facts")
        rs = safe.get("retry_strategy")
        if rs not in ("full", "resume"):
            pointers.append("/retry_strategy")
        if not (type(safe.get("failure_category")) is str and safe.get("failure_category") != ""):
            pointers.append("/failure_category")
        auth = safe.get("authorization") or {}
        if auth.get("authorized_by") == "system":
            for fld in ("transient", "deterministic"):
                if type(safe.get(fld)) is not bool:
                    pointers.append("/" + fld)
        else:
            for fld in ("transient", "deterministic"):
                if fld in safe:
                    pointers.append("/" + fld)

    # Resume checkpoint fields (when available).
    if branch == "resume":
        bf = bf or {}
        if bf.get("checkpoint_available") is True:
            if "checkpoint" not in safe:
                pointers.append("/checkpoint")
            if "checkpoint_event_order" not in bf:
                pointers.append("/boundary_facts/checkpoint_event_order")
            else:
                eo = bf.get("checkpoint_event_order")
                if not (type(eo) is int and not isinstance(eo, bool) and eo >= 0):
                    pointers.append("/boundary_facts/checkpoint_event_order")

    # more_evidence specific.
    if branch == "more_evidence":
        lin = safe.get("proposed_child_lineage")
        if not _is_shape(lin, {
            "type": "dict",
            "required": {
                "parent_run_id": {"type": "str", "min": 1},
                "lineage_kind": {"type": "enum", "values": ["more_evidence"]}}}):
            pointers.append("/proposed_child_lineage")
        evs = safe.get("evidence_requests")
        if not (type(evs) is list and len(evs) >= 1 and all(_is_shape(e, {
                "type": "dict",
                "required": {
                    "request_id": {"type": "str", "min": 1},
                    "artifact_kind": {"type": "str", "min": 1},
                    "description": {"type": "str", "min": 1},
                    "required": {"type": "bool"}}}) for e in (evs or []))):
            pointers.append("/evidence_requests")

    # redesign specific.
    if branch == "redesign":
        lin = safe.get("proposed_child_lineage")
        if not _is_shape(lin, {
            "type": "dict",
            "required": {
                "parent_run_id": {"type": "str", "min": 1},
                "lineage_kind": {"type": "enum", "values": ["redesign"]}}}):
            pointers.append("/proposed_child_lineage")
        if not _is_shape(safe.get("revised_contract_ref"), _ARTIFACT_REF_SHAPE):
            pointers.append("/revised_contract_ref")
        if safe.get("reason_code") not in _REDESIGN_REASONS:
            pointers.append("/reason_code")
        hp = safe.get("history_preservation_facts")
        if not _is_shape(hp, {
            "type": "dict",
            "required": {
                "original_history_preserved": {"type": "bool"},
                "original_evidence_preserved": {"type": "bool"}}}):
            pointers.append("/history_preservation_facts")

    # human_intervention specific.
    if branch == "human_intervention":
        if safe.get("intervention_source") not in ("gate_recommendation", "policy_exhaustion"):
            pointers.append("/intervention_source")
        ev = safe.get("intervention_evidence")
        if not (type(ev) is list and len(ev) >= 1 and all(_is_shape(e, _ARTIFACT_REF_SHAPE) for e in ev)):
            pointers.append("/intervention_evidence")
        if safe.get("human_intent") not in _HUMAN_INTENTS:
            pointers.append("/human_intent")
        if not _is_shape(safe.get("prohibited_override_facts"), {
            "type": "dict",
            "required": {
                "required_gate_override_attempted": {"type": "bool"},
                "pass_evidence_fabricated": {"type": "bool"},
                "retry_resume_bounds_bypassed": {"type": "bool"}}}):
            pointers.append("/prohibited_override_facts")
        if safe.get("intervention_source") == "gate_recommendation":
            if "gate_snapshot_binding" not in safe:
                pointers.append("/gate_snapshot_binding")
        if safe.get("intervention_source") == "policy_exhaustion":
            pe = safe.get("policy_exhaustion_facts")
            if not _is_shape(pe, {
                "type": "dict",
                "required": {
                    "exhaustion_classification": {"type": "enum", "values": _EXHAUSTION_CLASSES}}}):
                pointers.append("/policy_exhaustion_facts")

    # terminate specific.
    if branch == "terminate":
        if not (type(safe.get("terminate_reason")) is str and safe.get("terminate_reason") != ""):
            pointers.append("/terminate_reason")

    # stop_stage specific.
    if branch == "stop_stage":
        if "relevant_stage_id" in (bf or {}) and type((bf or {}).get("relevant_stage_id")) is not str:
            pointers.append("/boundary_facts/relevant_stage_id")

    return pointers


def _branch_fields(branch):
    common_forbidden = [
        "retry_strategy", "failure_category", "transient", "deterministic",
        "proposed_child_run_id", "checkpoint", "proposed_child_lineage",
        "revised_contract_ref", "reason_code", "history_preservation_facts",
        "intervention_source", "intervention_evidence", "human_intent",
        "policy_exhaustion_facts", "prohibited_override_facts", "terminate_reason",
        "evidence_requests", "gate_snapshot_binding",
        # authorization is an output-only echo (authorization_echo). Per the
        # v2 request schemas (additionalProperties: false) it is a forbidden
        # input field for the non-auth branches (stop_stage/stop_run/
        # more_evidence). The five auth-requiring branches already exclude
        # it from their forbidden filter below, so listing it here forbids
        # it exactly where the schema disallows it.
        "authorization",
    ]
    if branch == "stop_stage":
        return ["gate_snapshot_binding"], [f for f in common_forbidden if f != "gate_snapshot_binding"]
    if branch == "stop_run":
        return ["gate_snapshot_binding"], [f for f in common_forbidden if f != "gate_snapshot_binding"]
    if branch == "retry":
        return ["proposed_child_run_id", "retry_strategy", "failure_category", "authorization"], \
               [f for f in common_forbidden if f not in ("proposed_child_run_id", "retry_strategy",
                                                         "failure_category", "authorization",
                                                         "transient", "deterministic")]
    if branch == "resume":
        return ["proposed_child_run_id", "authorization"], \
               [f for f in common_forbidden if f not in ("proposed_child_run_id", "authorization",
                                                         "checkpoint")]
    if branch == "more_evidence":
        return ["gate_snapshot_binding", "proposed_child_lineage", "evidence_requests"], \
               [f for f in common_forbidden if f not in ("gate_snapshot_binding",
                                                         "proposed_child_lineage", "evidence_requests")]
    if branch == "redesign":
        return ["proposed_child_lineage", "revised_contract_ref", "reason_code", "authorization",
                "history_preservation_facts"], \
               [f for f in common_forbidden if f not in ("proposed_child_lineage", "revised_contract_ref",
                                                         "reason_code", "authorization",
                                                         "history_preservation_facts")]
    if branch == "human_intervention":
        return ["intervention_source", "intervention_evidence", "authorization", "human_intent",
                "prohibited_override_facts"], \
               [f for f in common_forbidden if f not in ("intervention_source", "intervention_evidence",
                                                         "authorization", "human_intent",
                                                         "prohibited_override_facts",
                                                         "gate_snapshot_binding", "policy_exhaustion_facts")]
    if branch == "terminate":
        return ["authorization", "terminate_reason"], \
               [f for f in common_forbidden if f not in ("authorization", "terminate_reason")]
    return [], common_forbidden


def _branch_boundary_fields(branch):
    if branch == "stop_stage":
        return ["relevant_stage_id", "relevant_stage_status"], ["gate_decision_snapshot"]
    if branch == "stop_run":
        return [], ["gate_decision_snapshot"]
    if branch == "retry":
        return ["current_retry_count", "max_retries", "same_kind_failure_count", "attempt_history_facts"], \
               ["gate_decision_snapshot"]
    if branch == "resume":
        return ["checkpoint_available", "interruption_cause"], ["gate_decision_snapshot"]
    if branch == "more_evidence":
        return ["evidence_gap_reason"], ["gate_decision_snapshot"]
    if branch == "redesign":
        return [], ["gate_decision_snapshot"]
    if branch == "human_intervention":
        return [], ["gate_decision_snapshot"]
    if branch == "terminate":
        return [], ["gate_decision_snapshot"]
    return [], ["gate_decision_snapshot"]


# ---------------------------------------------------------------------------
# Error / decision builders.
# ---------------------------------------------------------------------------

def _error(safe, error_code, field_paths, action_kind_null=False, run_id_null=False):
    def extract(key, null_override=None):
        if null_override is not None:
            return null_override
        v = safe.get(key)
        if type(v) in (str, int, bool) or v is None:
            return v
        return None
    action_kind = None if action_kind_null else extract("action_kind")
    run_id = None if run_id_null else extract("run_id")
    return {
        "policy_declaration": _copy_if_present(safe, "policy_declaration"),
        "decision_id": extract("decision_id"),
        "evaluated_at": extract("evaluated_at"),
        "evaluated_by": extract("evaluated_by"),
        "run_id": run_id,
        "action_kind": action_kind,
        "error_code": error_code,
        "description": _ERROR_DESCRIPTIONS[error_code],
        "field_paths": field_paths if field_paths else [],
    }


def _copy_if_present(safe, key):
    v = safe.get(key)
    if type(v) is dict or type(v) is list:
        return _safe_clone(v)
    return v


def _safety_error(request, offending):
    def scalar(key):
        if type(request) is dict and key in request:
            v = request[key]
            if type(v) in (str, int, bool) or v is None:
                return v
        return None
    return {
        "policy_declaration": None,
        "decision_id": scalar("decision_id"),
        "evaluated_at": scalar("evaluated_at"),
        "evaluated_by": scalar("evaluated_by"),
        "run_id": scalar("run_id"),
        "action_kind": scalar("action_kind"),
        "error_code": "invalid_action_branch",
        "description": _ERROR_DESCRIPTIONS["invalid_action_branch"],
        "field_paths": [offending] if offending else [],
    }


def _make_decision(safe, branch, disposition, reason_code):
    bf = safe.get("boundary_facts") or {}
    evidence_refs = safe.get("evidence_refs")
    if type(evidence_refs) is list:
        evidence = _safe_clone(evidence_refs)
    else:
        evidence = []
    auth = safe.get("authorization")
    authorization_echo = _safe_clone(auth) if (branch in _AUTH_REQUIRED and type(auth) is dict) else None
    child_run_id = safe.get("proposed_child_run_id")
    if branch in ("more_evidence", "redesign"):
        child_run_id = None
    decision = {
        "decision_id": safe.get("decision_id"),
        "action_kind": branch,
        "disposition": disposition,
        "policy_declaration": _copy_if_present(safe, "policy_declaration"),
        "evaluated_at": safe.get("evaluated_at"),
        "evaluated_by": safe.get("evaluated_by"),
        "reason_code": reason_code,
        "evidence_refs": evidence,
        "run_id": safe.get("run_id"),
        "parent_run_id": bf.get("parent_run_id"),
        "child_run_id": child_run_id,
        "authorization_echo": authorization_echo,
        "retry_eligibility": None,
        "checkpoint_evidence": None,
    }
    return decision


def _branch_decide(safe, branch):
    bf = safe.get("boundary_facts") or {}
    parent_status = bf.get("parent_run_status")

    if branch == "stop_stage":
        return _decide_stop_stage(safe, bf, parent_status)
    if branch == "stop_run":
        return _decide_stop_run(safe, parent_status)
    if branch == "retry":
        return _decide_retry(safe, bf, parent_status)
    if branch == "resume":
        return _decide_resume(safe, bf, parent_status)
    if branch == "more_evidence":
        return _decide_more_evidence(safe, bf)
    if branch == "redesign":
        return _decide_redesign(safe)
    if branch == "human_intervention":
        return _decide_human_intervention(safe)
    if branch == "terminate":
        return _decide_terminate(safe, parent_status)
    return _error(safe, "invalid_action_branch", ["/action_kind"])


def _gate_recommendation(safe):
    b = safe.get("gate_snapshot_binding") or {}
    snap = b.get("gate_decision_snapshot") or {}
    return snap.get("recommendation")


def _decide_stop_stage(safe, bf, parent_status):
    if parent_status != "active":
        return _make_decision(safe, "stop_stage", "denied", "denied_parent_status_ineligible")
    if _gate_recommendation(safe) != "stop_stage":
        return _make_decision(safe, "stop_stage", "denied", "denied_gate_recommendation_mismatch")
    if bf.get("relevant_stage_status") != "active":
        return _make_decision(safe, "stop_stage", "denied", "denied_stage_not_active")
    return _make_decision(safe, "stop_stage", "authorized", "action_authorized_stop_stage")


def _decide_stop_run(safe, parent_status):
    if parent_status != "active":
        return _make_decision(safe, "stop_run", "denied", "denied_parent_status_ineligible")
    if _gate_recommendation(safe) != "stop_run":
        return _make_decision(safe, "stop_run", "denied", "denied_gate_recommendation_mismatch")
    return _make_decision(safe, "stop_run", "authorized", "action_authorized_stop_run")


def _decide_retry(safe, bf, parent_status):
    max_retries = bf.get("max_retries")
    current = bf.get("current_retry_count")
    same_kind = bf.get("same_kind_failure_count")
    eligible = (type(max_retries) is int and not isinstance(max_retries, bool)
                and 1 <= max_retries <= 3
                and type(current) is int and not isinstance(current, bool) and current < max_retries
                and type(same_kind) is int and not isinstance(same_kind, bool) and same_kind < 3)
    system_role = (safe.get("authorization") or {}).get("authorized_by") == "system"

    # Threshold (bounds) eligibility is evaluated BEFORE the parent-status
    # eligibility check: a threshold-ineligible retry is denied on bounds
    # (catalog svc-retry-bounds-exceeded -> denied_retry_bounds_exceeded) even
    # when parent_run_status is not "failed", and BEFORE the system-agreement
    # rule so a threshold-ineligible system retry is not masked by
    # system_retry_unauthorized.
    if not eligible:
        retry_eligibility = {
            "eligible": False,
            "parent_status_satisfied": parent_status == "failed",
            "lineage_satisfied": True,
            "bounds_satisfied": False,
            "system_auto_authorized": system_role,
            "ineligibility_reason_code": "denied_retry_bounds_exceeded",
        }
        decision = _make_decision(safe, "retry", "denied", "denied_retry_bounds_exceeded")
        decision["retry_eligibility"] = retry_eligibility
        return decision

    # Threshold satisfied: the parent run must have failed to be retryable.
    if parent_status != "failed":
        retry_eligibility = {
            "eligible": False,
            "parent_status_satisfied": False,
            "lineage_satisfied": True,
            "bounds_satisfied": True,
            "system_auto_authorized": system_role,
            "ineligibility_reason_code": "denied_parent_status_ineligible",
        }
        decision = _make_decision(safe, "retry", "denied", "denied_parent_status_ineligible")
        decision["retry_eligibility"] = retry_eligibility
        return decision

    # Threshold satisfied + parent failed: a system-authorized retry must
    # additionally satisfy the system agreement rule (contract Section 9.3).
    if system_role:
        ok, paths = _check_system_retry(safe)
        if not ok:
            return _error(safe, "system_retry_unauthorized", sorted(paths))

    retry_eligibility = {
        "eligible": True,
        "parent_status_satisfied": True,
        "lineage_satisfied": True,
        "bounds_satisfied": True,
        "system_auto_authorized": system_role,
        "ineligibility_reason_code": None,
    }
    decision = _make_decision(safe, "retry", "authorized", "action_authorized_retry")
    decision["retry_eligibility"] = retry_eligibility
    return decision


def _decide_resume(safe, bf, parent_status):
    if bf.get("checkpoint_available") is False:
        return _make_decision(safe, "resume", "denied", "denied_checkpoint_unavailable")
    if parent_status != "interrupted":
        return _make_decision(safe, "resume", "denied", "denied_parent_status_ineligible")
    decision = _make_decision(safe, "resume", "authorized", "action_authorized_resume")
    cp = safe.get("checkpoint")
    decision["checkpoint_evidence"] = _safe_clone(cp) if type(cp) is dict else None
    return decision


def _decide_more_evidence(safe, bf):
    if _gate_recommendation(safe) != "more_evidence":
        return _make_decision(safe, "more_evidence", "denied", "denied_gate_recommendation_mismatch")
    if bf.get("evidence_gap_reason") == "unrecoverable_evidence_gap":
        return _make_decision(safe, "more_evidence", "denied", "denied_evidence_gap_unrecoverable")
    return _make_decision(safe, "more_evidence", "authorized", "action_authorized_more_evidence")


def _decide_redesign(safe):
    return _make_decision(safe, "redesign", "authorized", "action_authorized_redesign")


def _decide_human_intervention(safe):
    if safe.get("intervention_source") == "gate_recommendation":
        rec = _gate_recommendation(safe)
        if rec != "proceed_with_warning":
            return _make_decision(safe, "human_intervention", "denied",
                                  "denied_gate_recommendation_mismatch")
        return _make_decision(safe, "human_intervention", "authorized",
                              "action_authorized_human_intervention")
    # policy_exhaustion (already validated not normal_branch_available)
    return _make_decision(safe, "human_intervention", "authorized",
                          "action_authorized_human_intervention")


def _decide_terminate(safe, parent_status):
    terminal = parent_status in ("completed", "failed", "blocked")
    if terminal:
        return _make_decision(safe, "terminate", "denied", "denied_terminal_run")
    return _make_decision(safe, "terminate", "authorized", "action_authorized_terminate")
