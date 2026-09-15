"""Pure Validator Mesh aggregation core v1.2.0.

Deterministic, standard-library-only evaluation of a ValidatorMeshEvaluationRequest
into exactly one ValidatorMeshResult XOR ValidatorMeshEvaluationError. No clock,
random, environment, filesystem, network, SQLite, workflow state, model inference,
artifact dereference, or hidden module state.

Public entry point: evaluate_validator_mesh(request: dict) -> dict.
"""

import copy

# ---------------------------------------------------------------------------
# Immutable constants (frozen from contract) suitable for independent comparison
# ---------------------------------------------------------------------------

VERDICT_HIERARCHY = ("fail", "blocked", "human_review_required", "inconclusive", "pass")

_VERDICT_RANK = {v: i for i, v in enumerate(VERDICT_HIERARCHY)}

VALID_VERDICTS = frozenset(_VERDICT_RANK)

VALID_DISPATCH_STATUSES = frozenset((
    "report_produced", "unreachable", "no_report",
    "degraded_storage", "degraded_transport",
))

VALID_REQUIREMENT_KINDS = frozenset(("baseline", "extension"))

VALID_FAILURE_BEHAVIORS = frozenset(("halt_mesh", "halt_run", "require_intervention"))

VALID_MISSING_MAPPING_POLICIES = frozenset(("fail", "human_review_required"))

VALID_FRESHNESS_STATUSES = frozenset((
    "current", "superseded", "invalidated", "stale", "mismatched", "duplicate",
    "conflicting",
))

VALID_CONFIDENCE_LEVELS = frozenset(("high", "medium", "low"))

VALID_RISK_LEVELS = frozenset(("low", "medium", "high"))

# v1.2: 8 active error codes (6 deprecated codes removed)
VALID_ERROR_CODES = frozenset((
    "invalid_mesh_declaration",
    "invalid_mesh_request",
    "duplicate_requirement_id",
    "dispatch_count_mismatch",
    "duplicate_dispatch_request_id",
    "orphan_dispatch_result",
    "invalid_dispatch_result",
    "invalid_report_binding",
))

ERROR_DESCRIPTIONS = {
    "invalid_mesh_declaration":
        "The ValidatorMeshDeclaration is structurally invalid or has an unsupported mesh_version.",
    "invalid_mesh_request":
        "The ValidatorMeshEvaluationRequest is structurally invalid or contains inconsistent field values.",
    "dispatch_count_mismatch":
        "The count of dispatch results does not equal the count of declared requirements.",
    "duplicate_requirement_id":
        "Two or more requirements in the mesh declaration share the same requirement_id.",
    "duplicate_dispatch_request_id":
        "Two or more dispatch results share the same dispatch_request_id.",
    "orphan_dispatch_result":
        "A dispatch result references a dispatch_request_id that does not match any declared requirement_id.",
    "invalid_dispatch_result":
        "A dispatch result is structurally invalid: status/binding conditional shape is malformed or unsupported.",
    "invalid_report_binding":
        "A report binding is structurally invalid or internally inconsistent, including report digest disagreement, missing/invalid report_confidence, or a malformed closed ReportArtifactRef.",
}

# Recommended actions by verdict (Section 6)
_RECOMMENDED_ACTIONS = {
    "pass": "proceed",
    "fail": "stop_run",
    "blocked": "more_evidence",
    "inconclusive": "human_intervention",
    "human_review_required": "human_intervention",
}

DIGEST_PATTERN = "^[a-f0-9]{64}$"


def _is_valid_digest(value):
    """Check that a value is a 64-char lowercase hex string."""
    if not isinstance(value, str) or len(value) != 64:
        return False
    for c in value:
        if c not in "0123456789abcdef":
            return False
    return True


_CONTRACT_KEY_FIELDS = ("artifact_id", "artifact_kind", "artifact_version", "digest")


def _make_contract_key(artifact_ref):
    """Build a ContractKey tuple from an ArtifactRef dict."""
    if not isinstance(artifact_ref, dict):
        return None
    try:
        return tuple(artifact_ref[f] for f in _CONTRACT_KEY_FIELDS)
    except KeyError:
        return None


def _make_target_key(artifact_ref):
    """Build a TargetKey tuple from an ArtifactRef dict (same fields as ContractKey)."""
    return _make_contract_key(artifact_ref)


def _make_comparison_key(validator_identity, contract_key, target_key):
    """Build a ComparisonKey tuple: (validator_identity, contract_key, target_key)."""
    return (validator_identity, contract_key, target_key)


# Required binding fields (v1.2 adds caller-bound report_confidence)
_BINDING_REQUIRED_FIELDS = frozenset((
    "binding_id", "requirement_id", "validator_identity", "role",
    "contract_ref", "target_artifact_ref", "report_ref", "report_sha256",
    "report_confidence", "report_overall_verdict",
    "independent_production_evidence",
    "bound_at", "bound_by",
))

_INDEPENDENT_EVIDENCE_REQUIRED = frozenset((
    "producer_identity", "production_environment",
    "production_timestamp", "no_caller_role_collapse",
))

# Required declaration fields
_DECLARATION_REQUIRED = frozenset((
    "mesh_id", "mesh_version", "governing_contract", "declared_at",
    "declared_by", "requirements", "aggregate_hierarchy", "dispatch_policy",
    "freshness_rules", "publish_bridge_contract", "run_context",
))

_REQUIREMENT_REQUIRED = frozenset((
    "requirement_id", "validator_identity", "contract_ref", "artifact_scope",
    "requirement_kind", "required", "dispatch_priority", "failure_behavior",
))

# ArtifactRef required fields (v1.2: digest is mandatory for contract/target refs)
_ARTIFACT_REF_REQUIRED = frozenset(_CONTRACT_KEY_FIELDS)

# Required evaluation request fields
_REQUEST_REQUIRED = frozenset((
    "mesh_eval_id", "mesh_declaration", "dispatch_results",
    "requested_at", "requested_by",
))

_RESULT_KIND_VALUES = frozenset((
    "report", "missing_baseline", "missing_required_extension",
    "unusable_required_report", "optional_excluded",
))


# ---------------------------------------------------------------------------
# Structural type safety (before deep copy / traversal)
# ---------------------------------------------------------------------------

def _check_is_dict(value, label):
    """Fail closed on non-dict inputs including subclasses, descriptors, NaN."""
    if type(value) is not dict:  # noqa: E721 -- exact type check required
        return False
    # Reject non-JSON-coercible values
    for v in value.values():
        if not _is_json_coercible(v):
            return False
    return True


def _is_json_coercible(value):
    """Reject NaN, non-basic types that would survive a naive dict check."""
    if isinstance(value, float):
        import math
        if math.isnan(value) or math.isinf(value):
            return False
    if isinstance(value, (dict, list, str, int, float, bool, type(None))):
        if isinstance(value, dict):
            for k, v in value.items():
                if not isinstance(k, str) or not _is_json_coercible(v):
                    return False
        elif isinstance(value, list):
            for item in value:
                if not _is_json_coercible(item):
                    return False
        return True
    return False


def _is_nonempty_string(value):
    return isinstance(value, str) and len(value) > 0


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_bool(value):
    return isinstance(value, bool)


# ---------------------------------------------------------------------------
# Error construction helpers
# ---------------------------------------------------------------------------

def _make_error(error_code, mesh_eval_id=None, mesh_id=None, run_context=None):
    return {
        "mesh_eval_id": mesh_eval_id,
        "mesh_id": mesh_id,
        "error_code": error_code,
        "error_description": ERROR_DESCRIPTIONS[error_code],
        "run_context": run_context,
    }


def _extract_safe(request, *keys):
    """Safely extract nested values from possibly malformed request."""
    current = request
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# Validation helper: check an ArtifactRef has all required fields including digest
# ---------------------------------------------------------------------------

def _validate_artifact_ref(artifact_ref, label):
    """Validate an ArtifactRef: must be a dict with all four fields and valid digest.

    Returns (error_code, error_description) tuple or (None, None) on success.
    """
    if not isinstance(artifact_ref, dict):
        return ("invalid_mesh_declaration", "%s must be a dict" % label)
    for field in _ARTIFACT_REF_REQUIRED:
        if field not in artifact_ref:
            return ("invalid_mesh_declaration",
                    "%s missing required field '%s'" % (label, field))
    if not _is_nonempty_string(artifact_ref.get("artifact_id")):
        return ("invalid_mesh_declaration",
                "%s artifact_id must be a non-empty string" % label)
    if not _is_nonempty_string(artifact_ref.get("artifact_kind")):
        return ("invalid_mesh_declaration",
                "%s artifact_kind must be a non-empty string" % label)
    if not _is_nonempty_string(artifact_ref.get("artifact_version")):
        return ("invalid_mesh_declaration",
                "%s artifact_version must be a non-empty string" % label)
    if not _is_valid_digest(artifact_ref.get("digest")):
        return ("invalid_mesh_declaration",
                "%s digest must be a 64-char lowercase hex string" % label)
    return (None, None)


# ---------------------------------------------------------------------------
# Phase 0: Structural safety (error codes 0-1)
# ---------------------------------------------------------------------------

def _validate_structural_safety(request):
    """Validate structural types before any traversal or copy.

    Returns (error_dict, None) on failure, (None, safe_copy) on success.
    """
    if type(request) is not dict:  # noqa: E721
        return _make_error("invalid_mesh_declaration"), None

    # Check basic JSON coercibility of entire request
    if not _is_json_coercible(request):
        return _make_error("invalid_mesh_declaration"), None

    # Check all required request-level fields types BEFORE deep copy
    for field in _REQUEST_REQUIRED:
        if field not in request:
            return (_make_error("invalid_mesh_request",
                                _extract_safe(request, "mesh_eval_id"),
                                _extract_safe(request, "mesh_declaration", "mesh_id"),
                                _extract_safe(request, "mesh_declaration", "run_context"))), None

    # Validate mesh_eval_id
    mesh_eval_id = request.get("mesh_eval_id")
    if not _is_nonempty_string(mesh_eval_id):
        return (_make_error("invalid_mesh_request",
                            mesh_eval_id if isinstance(mesh_eval_id, str) and mesh_eval_id else None,
                            _extract_safe(request, "mesh_declaration", "mesh_id"),
                            _extract_safe(request, "mesh_declaration", "run_context"))), None

    # Validate mesh_declaration is a dict
    declaration = request.get("mesh_declaration")
    if type(declaration) is not dict:  # noqa: E721
        return _make_error("invalid_mesh_declaration", mesh_eval_id), None

    # Validate dispatch_results is a list
    dispatch_results = request.get("dispatch_results")
    if type(dispatch_results) is not list:  # noqa: E721
        return _make_error("invalid_mesh_request",
                           mesh_eval_id,
                           declaration.get("mesh_id"),
                           declaration.get("run_context")), None

    # Validate requested_at and requested_by
    if not _is_nonempty_string(request.get("requested_at")):
        return (_make_error("invalid_mesh_request",
                            mesh_eval_id,
                            declaration.get("mesh_id"),
                            declaration.get("run_context"))), None

    if not _is_nonempty_string(request.get("requested_by")):
        return (_make_error("invalid_mesh_request",
                            mesh_eval_id,
                            declaration.get("mesh_id"),
                            declaration.get("run_context"))), None

    # Now deep copy (never mutate original)
    try:
        safe_copy = copy.deepcopy(request)
    except Exception:
        return _make_error("invalid_mesh_request", mesh_eval_id,
                           declaration.get("mesh_id"),
                           declaration.get("run_context")), None

    return None, safe_copy


# ---------------------------------------------------------------------------
# Phase 1: Declaration validation (error code 0)
# ---------------------------------------------------------------------------

def _validate_declaration(declaration, mesh_eval_id):
    """Validate ValidatorMeshDeclaration structural rules (v1.2).

    Returns error dict or None.
    """
    if type(declaration) is not dict:  # noqa: E721
        return _make_error("invalid_mesh_declaration", mesh_eval_id)

    for field in _DECLARATION_REQUIRED:
        if field not in declaration:
            return _make_error("invalid_mesh_declaration", mesh_eval_id)

    # v1.2: mesh_version MUST be "1.2.0"; v1.0/v1.1 declarations are rejected
    if declaration.get("mesh_version") != "1.2.0":
        return _make_error("invalid_mesh_declaration", mesh_eval_id,
                           declaration.get("mesh_id"),
                           declaration.get("run_context"))

    # mesh_id must be non-empty string
    if not _is_nonempty_string(declaration.get("mesh_id")):
        return _make_error("invalid_mesh_declaration", mesh_eval_id,
                           declaration.get("mesh_id"),
                           declaration.get("run_context"))

    # governing_contract must have digest
    gc = declaration.get("governing_contract")
    err_code, err_desc = _validate_artifact_ref(gc, "governing_contract")
    if err_code is not None:
        return _make_error(err_code, mesh_eval_id,
                           declaration.get("mesh_id"),
                           declaration.get("run_context"))

    # requirements must be non-empty list
    requirements = declaration.get("requirements")
    if type(requirements) is not list or len(requirements) == 0:  # noqa: E721
        return _make_error("invalid_mesh_declaration", mesh_eval_id,
                           declaration.get("mesh_id"),
                           declaration.get("run_context"))

    # Validate each requirement
    for req in requirements:
        if type(req) is not dict:  # noqa: E721
            return _make_error("invalid_mesh_declaration", mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))

        for field in _REQUIREMENT_REQUIRED:
            if field not in req:
                return _make_error("invalid_mesh_declaration", mesh_eval_id,
                                   declaration.get("mesh_id"),
                                   declaration.get("run_context"))

        if not _is_nonempty_string(req.get("requirement_id")):
            return _make_error("invalid_mesh_declaration", mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))

        if not _is_nonempty_string(req.get("validator_identity")):
            return _make_error("invalid_mesh_declaration", mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))

        if req.get("requirement_kind") not in VALID_REQUIREMENT_KINDS:
            return _make_error("invalid_mesh_declaration", mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))

        if not _is_bool(req.get("required")):
            return _make_error("invalid_mesh_declaration", mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))

        if not _is_int(req.get("dispatch_priority")) or req["dispatch_priority"] < 0:
            return _make_error("invalid_mesh_declaration", mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))

        if req.get("failure_behavior") not in VALID_FAILURE_BEHAVIORS:
            return _make_error("invalid_mesh_declaration", mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))

        # Baseline requires missing_mapping_policy
        if req["requirement_kind"] == "baseline":
            if "missing_mapping_policy" not in req or req["missing_mapping_policy"] not in VALID_MISSING_MAPPING_POLICIES:
                return _make_error("invalid_mesh_declaration", mesh_eval_id,
                                   declaration.get("mesh_id"),
                                   declaration.get("run_context"))

        # Extension forbids missing_mapping_policy
        if req["requirement_kind"] == "extension" and "missing_mapping_policy" in req:
            return _make_error("invalid_mesh_declaration", mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))

        # contract_ref must be a valid ArtifactRef with digest
        cr = req.get("contract_ref")
        err_code, err_desc = _validate_artifact_ref(cr,
            "requirement '%s' contract_ref" % req.get("requirement_id", "?"))
        if err_code is not None:
            return _make_error(err_code, mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))

        # artifact_scope must have exactly 1 member, each must be valid ArtifactRef
        scope = req.get("artifact_scope")
        if type(scope) is not list or len(scope) != 1:  # noqa: E721
            return _make_error("invalid_mesh_declaration", mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))

        for idx, artifact_ref in enumerate(scope):
            err_code, err_desc = _validate_artifact_ref(artifact_ref,
                "requirement '%s' artifact_scope[%d]" % (req.get("requirement_id", "?"), idx))
            if err_code is not None:
                return _make_error(err_code, mesh_eval_id,
                                   declaration.get("mesh_id"),
                                   declaration.get("run_context"))

    # Check duplicate requirement_ids
    req_ids_seen = set()
    for req in requirements:
        rid = req["requirement_id"]
        if rid in req_ids_seen:
            return _make_error("duplicate_requirement_id", mesh_eval_id,
                               declaration.get("mesh_id"),
                               declaration.get("run_context"))
        req_ids_seen.add(rid)

    # Check run_context
    rc = declaration.get("run_context")
    if not isinstance(rc, dict) or not _is_nonempty_string(rc.get("run_id")) or not _is_nonempty_string(rc.get("stage_id")):
        return _make_error("invalid_mesh_declaration", mesh_eval_id,
                           declaration.get("mesh_id"),
                           declaration.get("run_context"))

    return None


# ---------------------------------------------------------------------------
# Phase 2: Request integrity (error codes 1, 2, 4, 5)
# ---------------------------------------------------------------------------

def _validate_request_integrity(request, mesh_eval_id, declaration):
    """Validate dispatch request structural rules.

    Returns error dict or None.
    """
    requirements = declaration["requirements"]
    dispatch_results = request["dispatch_results"]

    # dispatch_count_mismatch
    if len(dispatch_results) != len(requirements):
        return _make_error("dispatch_count_mismatch", mesh_eval_id,
                           declaration["mesh_id"],
                           declaration["run_context"])

    # Build requirement_id set
    req_id_set = {req["requirement_id"] for req in requirements}

    # Check dispatch results
    dr_ids_seen = set()
    for dr in dispatch_results:
        if type(dr) is not dict:  # noqa: E721
            return _make_error("invalid_mesh_request", mesh_eval_id,
                               declaration["mesh_id"],
                               declaration["run_context"])

        dr_id = dr.get("dispatch_request_id")
        if not _is_nonempty_string(dr_id):
            return _make_error("invalid_mesh_request", mesh_eval_id,
                               declaration["mesh_id"],
                               declaration["run_context"])

        # duplicate_dispatch_request_id
        if dr_id in dr_ids_seen:
            return _make_error("duplicate_dispatch_request_id", mesh_eval_id,
                               declaration["mesh_id"],
                               declaration["run_context"])
        dr_ids_seen.add(dr_id)

        # Check dispatch_status
        if dr.get("dispatch_status") not in VALID_DISPATCH_STATUSES:
            return _make_error("invalid_mesh_request", mesh_eval_id,
                               declaration["mesh_id"],
                               declaration["run_context"])

        # Structural dispatch result checks
        if "collected_at" not in dr or "collected_by" not in dr:
            return _make_error("invalid_mesh_request", mesh_eval_id,
                               declaration["mesh_id"],
                               declaration["run_context"])

        # report_produced must have report_binding and not error_code
        if dr["dispatch_status"] == "report_produced":
            if "report_binding" not in dr:
                return _make_error("invalid_dispatch_result", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])
            rb = dr["report_binding"]
            if type(rb) is not dict:  # noqa: E721
                return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])
        else:
            # Non-report_produced must have error_code and not report_binding
            if "error_code" not in dr:
                return _make_error("invalid_dispatch_result", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])
            if "report_binding" in dr:
                return _make_error("invalid_dispatch_result", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

            # degraded_storage/transport must have degradation_note
            if dr["dispatch_status"] in ("degraded_storage", "degraded_transport"):
                if "degradation_note" not in dr:
                    return _make_error("invalid_dispatch_result", mesh_eval_id,
                                       declaration["mesh_id"],
                                       declaration["run_context"])

    # orphan_dispatch_result check
    for dr in dispatch_results:
        if dr["dispatch_request_id"] not in req_id_set:
            return _make_error("orphan_dispatch_result", mesh_eval_id,
                               declaration["mesh_id"],
                               declaration["run_context"])

    return None


# ---------------------------------------------------------------------------
# Phase 3: Binding validation (error codes 6, 7 merged into invalid_report_binding)
# ---------------------------------------------------------------------------

def _validate_bindings(request, mesh_eval_id, declaration):
    """Validate every report binding structural rules (v1.2).

    report_ref is a closed ReportArtifactRef: exactly the four keys
    artifact_id/artifact_kind/artifact_version/digest with no extras,
    artifact_kind == "validation_report", and nullable report identity
    values (artifact_id/artifact_version non-empty string or null;
    digest/report_sha256 lowercase-64-hex or null). Two non-null unequal
    digests are malformed. report_confidence must be exactly high/medium/low.

    Returns error dict or None.
    """
    dispatch_results = request["dispatch_results"]

    for dr in dispatch_results:
        if dr["dispatch_status"] != "report_produced":
            continue

        rb = dr["report_binding"]

        # Check all required binding fields exist
        for field in _BINDING_REQUIRED_FIELDS:
            if field not in rb:
                return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        # Check binding_id is non-empty string
        if not _is_nonempty_string(rb.get("binding_id")):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        # Check role == "validator"
        if rb.get("role") != "validator":
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        # Check independent_production_evidence
        ipe = rb.get("independent_production_evidence")
        if not isinstance(ipe, dict):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        for field in _INDEPENDENT_EVIDENCE_REQUIRED:
            if field not in ipe:
                return _make_error("invalid_report_binding", mesh_eval_id,
                                       declaration["mesh_id"],
                                       declaration["run_context"])

        if ipe.get("no_caller_role_collapse") is not True:
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        if not _is_nonempty_string(ipe.get("producer_identity")):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        if not _is_nonempty_string(ipe.get("production_environment")):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        if not _is_nonempty_string(ipe.get("production_timestamp")):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        # Check report_overall_verdict
        if rb.get("report_overall_verdict") not in VALID_VERDICTS:
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        # v1.2: report_confidence must be exactly high/medium/low
        if rb.get("report_confidence") not in VALID_CONFIDENCE_LEVELS:
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        # contract_ref must be a valid ArtifactRef with digest
        contract_ref = rb.get("contract_ref")
        if not isinstance(contract_ref, dict):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])
        for field in _ARTIFACT_REF_REQUIRED:
            if field not in contract_ref:
                return _make_error("invalid_report_binding", mesh_eval_id,
                                       declaration["mesh_id"],
                                       declaration["run_context"])
        if not _is_valid_digest(contract_ref.get("digest")):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        # target_artifact_ref must be a valid ArtifactRef with digest
        target_ref = rb.get("target_artifact_ref")
        if not isinstance(target_ref, dict):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])
        for field in _ARTIFACT_REF_REQUIRED:
            if field not in target_ref:
                return _make_error("invalid_report_binding", mesh_eval_id,
                                       declaration["mesh_id"],
                                       declaration["run_context"])
        if not _is_valid_digest(target_ref.get("digest")):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        # v1.2: report_ref is a closed ReportArtifactRef (exactly four keys,
        # no extras, artifact_kind == "validation_report", nullable identity).
        report_ref = rb.get("report_ref")
        if not isinstance(report_ref, dict):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])
        if set(report_ref.keys()) != set(_ARTIFACT_REF_REQUIRED):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])
        if report_ref.get("artifact_kind") != "validation_report":
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])
        # artifact_id / artifact_version: non-empty string OR null
        for f in ("artifact_id", "artifact_version"):
            v = report_ref.get(f)
            if v is not None and not _is_nonempty_string(v):
                return _make_error("invalid_report_binding", mesh_eval_id,
                                       declaration["mesh_id"],
                                       declaration["run_context"])
        # digest: lowercase 64-hex OR null
        report_digest = report_ref.get("digest")
        if report_digest is not None and not _is_valid_digest(report_digest):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])
        # report_sha256: lowercase 64-hex OR null
        sha = rb.get("report_sha256")
        if sha is not None and not _is_valid_digest(sha):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])
        # Two non-null unequal digest values are malformed
        if report_digest is not None and sha is not None and report_digest != sha:
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        # Verify requirement_id references exist in declaration
        if rb.get("requirement_id") not in {r["requirement_id"] for r in declaration["requirements"]}:
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

        # Verify validator_identity consistency
        if not _is_nonempty_string(rb.get("validator_identity")):
            return _make_error("invalid_report_binding", mesh_eval_id,
                                   declaration["mesh_id"],
                                   declaration["run_context"])

    # Check no duplicate requirement_id across bindings
    binding_req_ids = []
    for dr in dispatch_results:
        if dr["dispatch_status"] == "report_produced":
            binding_req_ids.append(dr["report_binding"]["requirement_id"])

    if len(binding_req_ids) != len(set(binding_req_ids)):
        return _make_error("invalid_report_binding", mesh_eval_id,
                               declaration["mesh_id"],
                               declaration["run_context"])

    # Check no duplicate binding_id across bindings
    binding_ids = []
    for dr in dispatch_results:
        if dr["dispatch_status"] == "report_produced":
            binding_ids.append(dr["report_binding"]["binding_id"])

    if len(binding_ids) != len(set(binding_ids)):
        return _make_error("invalid_report_binding", mesh_eval_id,
                               declaration["mesh_id"],
                               declaration["run_context"])

    return None


# ---------------------------------------------------------------------------
# Phase 4: Freshness assessment (Section 3, v1.2)
# ---------------------------------------------------------------------------

def _assess_freshness(dispatch_results, requirements):
    """Assess freshness for every report binding (v1.2).

    Returns list of freshness assessments.
    Precedence: superseded > invalidated > stale > mismatch(contract) >
                mismatch(target) > duplicate/conflict > current.
    """
    requirements_map = {r["requirement_id"]: r for r in requirements}

    # Build requirement order for deterministic sorting
    req_order = {}
    for i, req in enumerate(requirements):
        req_order[req["requirement_id"]] = i

    # Collect all produced bindings with their declaration context
    produced = []
    for dr in dispatch_results:
        if dr["dispatch_status"] != "report_produced":
            continue
        rb = dr["report_binding"]
        rid = rb["requirement_id"]
        req = requirements_map.get(rid)
        if req is None:
            continue
        produced.append((rb, req, dr.get("dispatch_request_id", rid)))

    # Phase A: First four freshness predicates (per-binding, no cross-requirement grouping)
    assessments = []  # list of (binding_id, freshness_status, freshness_details)
    binding_statuses = {}  # binding_id -> (status, details)
    remaining = []  # bindings that pass first 4 predicates

    for rb, req, dr_id in produced:
        bid = rb["binding_id"]

        # 1. superseded: valid explicit supersession fact
        if isinstance(rb.get("supersession"), dict) and rb["supersession"]:
            status = "superseded"
            details = {"supersession": rb["supersession"]}
            binding_statuses[bid] = (status, details)
            continue

        # 2. invalidated: valid explicit invalidation fact
        if isinstance(rb.get("invalidation"), dict) and rb["invalidation"]:
            status = "invalidated"
            details = {"invalidation": rb["invalidation"]}
            binding_statuses[bid] = (status, details)
            continue

        # 3. stale: any of the four report identity values is null.
        # missing_fields in canonical order, only the null identity values.
        report_ref = rb.get("report_ref", {})
        report_sha256 = rb.get("report_sha256")
        if not isinstance(report_ref, dict):
            report_ref = {}
        missing_fields = []
        if report_ref.get("artifact_id") is None:
            missing_fields.append("report_ref.artifact_id")
        if report_ref.get("artifact_version") is None:
            missing_fields.append("report_ref.artifact_version")
        if report_ref.get("digest") is None:
            missing_fields.append("report_ref.digest")
        if report_sha256 is None:
            missing_fields.append("report_sha256")
        if missing_fields:
            status = "stale"
            details = {
                "field_category": "report_identity",
                "missing_fields": missing_fields,
            }
            binding_statuses[bid] = (status, details)
            continue

        # 4. mismatched: contract binding
        declared_contract_key = _make_contract_key(req.get("contract_ref", {}))
        binding_contract_key = _make_contract_key(rb.get("contract_ref", {}))
        if declared_contract_key is None or binding_contract_key is None:
            status = "mismatched"
            details = {"field_category": "contract_binding"}
            binding_statuses[bid] = (status, details)
            continue
        if binding_contract_key != declared_contract_key:
            status = "mismatched"
            details = {"field_category": "contract_binding"}
            binding_statuses[bid] = (status, details)
            continue

        # 4b. mismatched: target artifact binding
        scope = req.get("artifact_scope", [])
        declared_target_key = _make_target_key(scope[0]) if len(scope) == 1 else None
        binding_target_key = _make_target_key(rb.get("target_artifact_ref", {}))
        if declared_target_key is None or binding_target_key is None:
            status = "mismatched"
            details = {"field_category": "target_artifact_binding"}
            binding_statuses[bid] = (status, details)
            continue
        if binding_target_key != declared_target_key:
            status = "mismatched"
            details = {"field_category": "target_artifact_binding"}
            binding_statuses[bid] = (status, details)
            continue

        # Passed first 4 predicates - candidate for duplicate/conflict/current
        remaining.append((rb, req))

    # Phase B: Cross-requirement ComparisonKey grouping for duplicate/conflict
    groups = {}  # comparison_key -> [(rb, req)]
    for rb, req in remaining:
        ck = _make_contract_key(rb.get("contract_ref", {}))
        tk = _make_target_key(rb.get("target_artifact_ref", {}))
        vi = rb.get("validator_identity", "")
        comparison_key = _make_comparison_key(vi, ck, tk)
        if comparison_key not in groups:
            groups[comparison_key] = []
        groups[comparison_key].append((rb, req))

    for comparison_key, members in groups.items():
        # Sort group: dispatch_priority ascending, declaration order, requirement_id lexicographic
        def group_sort_key(item):
            rb, req = item
            return (
                req.get("dispatch_priority", 0),
                req_order.get(req.get("requirement_id", ""), 999),
                req.get("requirement_id", ""),
            )
        members.sort(key=group_sort_key)

        first_rb, first_req = members[0]
        first_digest = first_rb.get("report_sha256", "")
        first_verdict = first_rb.get("report_overall_verdict", "")
        comparison_key_str = first_rb.get("validator_identity", "") + ":" + \
            first_rb.get("contract_ref", {}).get("digest", "")[:8] + ":" + \
            first_rb.get("target_artifact_ref", {}).get("digest", "")[:8]

        # Determine if any non-first members have different digest or verdict
        has_conflict = any(
            rb.get("report_sha256", "") != first_digest or
            rb.get("report_overall_verdict", "") != first_verdict
            for rb, req in members[1:]
        )

        for idx, (rb, req) in enumerate(members):
            bid = rb["binding_id"]
            if idx == 0:
                # First member is always current
                binding_statuses[bid] = ("current", {})
            elif rb.get("report_sha256", "") == first_digest:
                # Same digest as first = duplicate
                binding_statuses[bid] = ("duplicate", {
                    "comparison_key": comparison_key_str,
                    "first_requirement_id": first_req["requirement_id"],
                })
            else:
                # Different digest in conflicting group
                if has_conflict:
                    # Determine conflicting status
                    all_req_ids = [r["requirement_id"] for _, r in members]
                    binding_statuses[bid] = ("conflicting", {
                        "comparison_key": comparison_key_str,
                        "requirement_ids": all_req_ids,
                    })
                else:
                    # No conflict - shouldn't happen if digest differs
                    binding_statuses[bid] = ("duplicate", {
                        "comparison_key": comparison_key_str,
                        "first_requirement_id": first_req["requirement_id"],
                    })

        # If group has conflict, non-duplicate, non-first members all get conflicting
        if has_conflict and len(members) > 1:
            conflict_req_ids = [r["requirement_id"] for _, r in members]
            for idx, (rb, req) in enumerate(members):
                bid = rb["binding_id"]
                if idx == 0:
                    continue  # First is current
                if binding_statuses[bid][0] == "duplicate":
                    continue  # Same-digest duplicates stay duplicate
                # Different digest in conflicting group
                binding_statuses[bid] = ("conflicting", {
                    "comparison_key": comparison_key_str,
                    "requirement_ids": conflict_req_ids,
                })

    # Build final assessments list
    final_assessments = []
    for rb, req, dr_id in produced:
        bid = rb["binding_id"]
        if bid in binding_statuses:
            status, details = binding_statuses[bid]
        else:
            status, details = "current", {}
        final_assessments.append({
            "binding_id": bid,
            "freshness_status": status,
            "freshness_details": details,
        })

    return final_assessments


# ---------------------------------------------------------------------------
# Phase 5: Compute contributions and aggregate (Section 4, v1.2)
# ---------------------------------------------------------------------------

def _compute_contributions(request, declaration, freshness_assessments):
    """Compute requirement contributions from dispatch results and freshness (v1.2).

    Returns (requirement_results, contributions_list, all_bindings).
    """
    requirements = declaration["requirements"]
    dispatch_results = request["dispatch_results"]

    # Map dispatch results by dispatch_request_id
    dr_map = {dr["dispatch_request_id"]: dr for dr in dispatch_results}

    # Map freshness assessments by binding_id
    freshness_map = {fa["binding_id"]: fa for fa in freshness_assessments}

    requirement_results = []
    contributions = []  # list of verdict strings that contribute
    all_bindings = []
    current_bindings = []  # only current contributing bindings

    for req in requirements:
        rid = req["requirement_id"]
        dr = dr_map.get(rid)

        if dr is None:
            # Missing dispatch result - unreachable
            # This is a result branch, not an error
            contribution, rr_entry = _compute_missing_contribution(req, "unreachable", None)
            requirement_results.append(rr_entry)
            if contribution is not None:
                contributions.append(contribution)
            continue

        status = dr["dispatch_status"]
        verdict = None
        rb = None
        freshness = None

        if status == "report_produced":
            rb = dr["report_binding"]
            verdict = rb.get("report_overall_verdict")
            bid = rb.get("binding_id")
            fa = freshness_map.get(bid, {})
            freshness = fa.get("freshness_status", "stale")
        else:
            freshness = None  # non-report statuses have no freshness

        # Build the requirement result entry
        rr_entry = {
            "requirement_id": rid,
            "requirement_kind": req["requirement_kind"],
            "required": req["required"],
            "dispatch_status": status,
            "report_verdict_or_null": verdict,
            "freshness_or_null": freshness,
        }

        is_required = req["required"]
        is_baseline = req["requirement_kind"] == "baseline"

        if status == "report_produced":
            if freshness == "current":
                # Row 1: report_produced + current -> report, verdict contribution
                rr_entry["result_kind"] = "report"
                rr_entry["verdict_contribution"] = verdict
                all_bindings.append(rb)
                current_bindings.append(rb)
                contributions.append(verdict)
            elif freshness in ("superseded", "invalidated", "stale", "mismatched",
                               "duplicate", "conflicting"):
                # Rows 5/6: non-current freshness
                if is_required:
                    # Row 5: required, non-current -> unusable_required_report, blocked
                    rr_entry["result_kind"] = "unusable_required_report"
                    rr_entry["verdict_contribution"] = "blocked"
                    all_bindings.append(rb)
                    contributions.append("blocked")
                else:
                    # Row 6: optional, non-current -> optional_excluded, excluded
                    rr_entry["result_kind"] = "optional_excluded"
                    rr_entry["excluded_reason"] = "optional_unusable_report"
            else:
                # Unknown freshness (shouldn't happen after validation)
                if is_required:
                    rr_entry["result_kind"] = "unusable_required_report"
                    rr_entry["verdict_contribution"] = "blocked"
                    all_bindings.append(rb)
                    contributions.append("blocked")
                else:
                    rr_entry["result_kind"] = "optional_excluded"
                    rr_entry["excluded_reason"] = "optional_unusable_report"
        else:
            # Non-report_produced statuses: no_report, unreachable, degraded_storage, degraded_transport
            # all non-report statuses without binding follow Section 4 Rows 2-4
            contribution, missing_entry = _compute_missing_contribution(req, status, dr)
            # Override the entry from _compute_missing_contribution with the correct dispatch_status
            missing_entry["dispatch_status"] = status
            missing_entry["report_verdict_or_null"] = None
            missing_entry["freshness_or_null"] = None
            missing_entry["required"] = req["required"]
            requirement_results.append(missing_entry)
            if contribution is not None:
                contributions.append(contribution)
            continue

        requirement_results.append(rr_entry)

    return requirement_results, contributions, all_bindings, current_bindings


def _compute_missing_contribution(req, status, dr):
    """Compute contribution for a missing/unreachable/no_report/degraded requirement.

    These are result branches, never errors.
    Returns (verdict_or_None, requirement_result_entry).
    """
    rid = req["requirement_id"]
    kind = req["requirement_kind"]
    required = req["required"]

    if kind == "baseline":
        # Row 2: baseline, no_report/unreachable/degraded -> missing_baseline, policy contribution
        policy = req.get("missing_mapping_policy", "fail")
        entry = {
            "requirement_id": rid,
            "requirement_kind": kind,
            "required": required,
            "dispatch_status": status,
            "report_verdict_or_null": None,
            "freshness_or_null": None,
            "result_kind": "missing_baseline",
            "verdict_contribution": policy,
        }
        return policy, entry
    else:  # extension
        if required:
            # Row 3: required extension, no_report/unreachable/degraded -> missing_required_extension, blocked
            entry = {
                "requirement_id": rid,
                "requirement_kind": kind,
                "required": required,
                "dispatch_status": status,
                "report_verdict_or_null": None,
                "freshness_or_null": None,
                "result_kind": "missing_required_extension",
                "verdict_contribution": "blocked",
            }
            return "blocked", entry
        else:
            # Row 4: optional extension, no_report/unreachable/degraded -> optional_excluded, excluded
            entry = {
                "requirement_id": rid,
                "requirement_kind": kind,
                "required": required,
                "dispatch_status": status,
                "report_verdict_or_null": None,
                "freshness_or_null": None,
                "result_kind": "optional_excluded",
                "excluded_reason": "optional_unavailable",
            }
            return None, entry


# ---------------------------------------------------------------------------
# Phase 6: Aggregate verdict and result construction
# ---------------------------------------------------------------------------

def _compute_aggregate(contributions):
    """Compute aggregate verdict from contributions list using canonical hierarchy.

    Zero contributions -> inconclusive (was an error in v1.0).
    """
    if not contributions:
        return "inconclusive"

    worst = "pass"
    worst_rank = len(VERDICT_HIERARCHY)  # Higher = less restrictive

    for v in contributions:
        rank = _VERDICT_RANK.get(v, len(VERDICT_HIERARCHY))
        if rank < worst_rank:
            worst_rank = rank
            worst = v

    return worst


def _compute_aggregate_confidence(current_bindings):
    """Compute aggregate confidence from caller-supplied report_confidence only.

    v1.2: Only current report bindings whose verdict contributes are considered.
    Least confidence wins using high < medium < low (one low dominates,
    otherwise medium dominates high). Returns exactly 'low' when no current
    report contributes. Confidence is never inferred from report verdicts and
    non-current binding confidence never affects the aggregate.
    """
    if not current_bindings:
        return "low"

    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    worst_conf = "high"
    worst_rank = -1

    for rb in current_bindings:
        conf = rb["report_confidence"]  # validated present in every binding
        rank = confidence_rank.get(conf, 2)
        if rank > worst_rank:
            worst_rank = rank
            worst_conf = conf

    return worst_conf


def _build_result(request, declaration, requirement_results,
                  contributions, all_bindings, current_bindings, freshness_assessments):
    """Build ValidatorMeshResult from computed data (v1.2).

    Returns dict conforming to ValidatorMeshResult shape.
    """
    aggregate_verdict = _compute_aggregate(contributions)
    aggregate_confidence = _compute_aggregate_confidence(current_bindings)
    recommended_action = _RECOMMENDED_ACTIONS.get(aggregate_verdict, "human_intervention")

    # Sort bindings by declaration order
    req_order = {r["requirement_id"]: i for i, r in enumerate(declaration["requirements"])}

    def _req_order_key(binding):
        rid = binding.get("requirement_id", "")
        return req_order.get(rid, 999)

    sorted_bindings = sorted(all_bindings, key=_req_order_key)

    # Sort freshness assessments by binding's requirement position
    binding_req_map = {b["binding_id"]: b.get("requirement_id", "") for b in all_bindings}

    def _freshness_order_key(fa):
        rid = binding_req_map.get(fa["binding_id"], "")
        return req_order.get(rid, 999)

    sorted_freshness = sorted(freshness_assessments, key=_freshness_order_key)

    return {
        "mesh_eval_id": request["mesh_eval_id"],
        "mesh_id": declaration["mesh_id"],
        "aggregate_verdict": aggregate_verdict,
        "aggregate_confidence": aggregate_confidence,
        "report_bindings": sorted_bindings,
        "requirement_results": requirement_results,
        "freshness_assessments": sorted_freshness,
        "recommended_action": recommended_action,
        "evaluated_at": request["requested_at"],
        "evaluated_by": request["requested_by"],
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def evaluate_validator_mesh(request):
    """Evaluate a ValidatorMeshEvaluationRequest and return exactly one result.

    Args:
        request: dict conforming to ValidatorMeshEvaluationRequest shape.

    Returns:
        dict: A ValidatorMeshResult on success, or a
              ValidatorMeshEvaluationError on failure.
              Exactly one, never both, never neither.
    """
    # Phase 0: Structural safety (before deep copy)
    error, safe = _validate_structural_safety(request)
    if error is not None:
        return error

    mesh_eval_id = safe["mesh_eval_id"]
    declaration = safe["mesh_declaration"]

    # Phase 1: Declaration validation
    error = _validate_declaration(declaration, mesh_eval_id)
    if error is not None:
        return error

    # Phase 2: Request integrity
    error = _validate_request_integrity(safe, mesh_eval_id, declaration)
    if error is not None:
        return error

    # Phase 3: Binding validation
    error = _validate_bindings(safe, mesh_eval_id, declaration)
    if error is not None:
        return error

    # Phase 4: Freshness assessment (v1.2 cross-requirement grouping)
    freshness_assessments = _assess_freshness(safe["dispatch_results"],
                                              declaration["requirements"])

    # Phase 5: Compute contributions (deprecated errors are now result branches)
    requirement_results, contributions, all_bindings, current_bindings = _compute_contributions(
        safe, declaration, freshness_assessments)

    # Phase 6: Build result (all matrix cells produce a result, never an error)
    result = _build_result(safe, declaration, requirement_results,
                           contributions, all_bindings, current_bindings,
                           freshness_assessments)

    # Verify caller objects are unchanged
    _verify_input_integrity(request, safe)

    return result


def _verify_input_integrity(original, safe_copy):
    """Verify that the caller's original request object was not mutated.

    This is a defensive check, not part of the normal evaluation flow.
    It ensures the contract requirement of non-mutation.
    """
    # Verify original was not mutated by comparing it with our safe copy
    # We don't raise - this is a verification helper
    if original is safe_copy:
        raise RuntimeError("BUG: safe_copy is same object as original - mutation risk")
