"""Local Validator dispatch provider boundary v1.0.0.

Dispatch each declared requirement exactly once through an explicitly supplied
provider capability. Return typed dispatch results in declaration order.

No module scan, entry-point discovery, network service discovery, global registry,
environment lookup, automatic retry, backoff, alternate provider, implicit default
provider, cached report, synthesized report, aggregation, gate evaluation,
runtime events, persistence, or dynamic plugin discovery.

Public entry point: dispatch(dispatch_requests, providers) -> list of dict.
"""

import copy

# ---------------------------------------------------------------------------
# Immutable constants (frozen from mesh contract)
# ---------------------------------------------------------------------------

VALID_DISPATCH_STATUSES = frozenset((
    "report_produced", "unreachable", "no_report",
    "degraded_storage", "degraded_transport",
))

VALID_RISK_LEVELS = frozenset(("low", "medium", "high"))

_DISPATCH_REQUEST_REQUIRED = frozenset((
    "dispatch_request_id", "requirement_id", "mesh_id", "validator_identity",
    "contract_ref", "artifact_scope", "evidence_pack", "risk_level",
    "allowed_read_only_commands", "dispatched_at", "dispatched_by", "run_context",
))

_DISPATCH_RESULT_REQUIRED = frozenset((
    "dispatch_request_id", "dispatch_status", "collected_at", "collected_by",
))


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class DispatchError(Exception):
    """Pre-dispatch validation failure. Raised before any provider call."""

    def __init__(self, error_code, message, dispatch_request_id=None):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.dispatch_request_id = dispatch_request_id

    def __eq__(self, other):
        if not isinstance(other, DispatchError):
            return NotImplemented
        return (self.error_code == other.error_code
                and self.message == other.message
                and self.dispatch_request_id == other.dispatch_request_id)

    def __hash__(self):
        return hash((self.error_code, self.message, self.dispatch_request_id))


# ---------------------------------------------------------------------------
# Type-checking helpers
# ---------------------------------------------------------------------------

def _is_nonempty_string(value):
    return isinstance(value, str) and len(value) > 0

def _check_is_dict(value):
    return type(value) is dict  # exact type, no subclasses

def _is_list(value):
    return type(value) is list  # noqa: E721

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)

def _is_bool(value):
    return isinstance(value, bool)

def _is_json_coercible(value):
    """Reject NaN, Inf, non-basic types."""
    if isinstance(value, float):
        import math
        try:
            if math.isnan(value) or math.isinf(value):
                return False
        except (TypeError, ValueError):
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


# ---------------------------------------------------------------------------
# Validation before any provider call
# ---------------------------------------------------------------------------

def _validate_dispatch_request(dr):
    """Validate a single dispatch request structurally. Raises DispatchError."""
    if type(dr) is not dict:  # noqa: E721
        raise DispatchError(
            "invalid_dispatch_request",
            "dispatch request is not a dict",
            None,
        )

    for field in _DISPATCH_REQUEST_REQUIRED:
        if field not in dr:
            raise DispatchError(
                "invalid_dispatch_request",
                "missing required field: %s" % field,
                dr.get("dispatch_request_id"),
            )

    dr_id = dr["dispatch_request_id"]
    if not _is_nonempty_string(dr_id):
        raise DispatchError(
            "invalid_dispatch_request",
            "dispatch_request_id must be a non-empty string",
            None,
        )

    if not _is_nonempty_string(dr.get("requirement_id")):
        raise DispatchError(
            "invalid_dispatch_request",
            "requirement_id must be a non-empty string",
            dr_id,
        )

    if not _is_nonempty_string(dr.get("mesh_id")):
        raise DispatchError(
            "invalid_dispatch_request",
            "mesh_id must be a non-empty string",
            dr_id,
        )

    if not _is_nonempty_string(dr.get("validator_identity")):
        raise DispatchError(
            "invalid_dispatch_request",
            "validator_identity must be a non-empty string",
            dr_id,
        )

    # contract_ref
    cr = dr.get("contract_ref")
    if not isinstance(cr, dict) or "artifact_id" not in cr or "artifact_kind" not in cr:
        raise DispatchError(
            "invalid_dispatch_request",
            "contract_ref must have artifact_id and artifact_kind",
            dr_id,
        )

    # artifact_scope
    scope = dr.get("artifact_scope")
    if not _is_list(scope) or len(scope) == 0:
        raise DispatchError(
            "invalid_dispatch_request",
            "artifact_scope must be a non-empty list",
            dr_id,
        )

    # risk_level
    if dr.get("risk_level") not in VALID_RISK_LEVELS:
        raise DispatchError(
            "invalid_dispatch_request",
            "risk_level must be low, medium, or high",
            dr_id,
        )

    # evidence_pack
    if not _check_is_dict(dr.get("evidence_pack")):
        raise DispatchError(
            "invalid_dispatch_request",
            "evidence_pack must be a dict",
            dr_id,
        )

    # allowed_read_only_commands
    commands = dr.get("allowed_read_only_commands")
    if not _is_list(commands):
        raise DispatchError(
            "invalid_dispatch_request",
            "allowed_read_only_commands must be a list",
            dr_id,
        )

    # dispatched_at
    if not _is_nonempty_string(dr.get("dispatched_at")):
        raise DispatchError(
            "invalid_dispatch_request",
            "dispatched_at must be a non-empty string",
            dr_id,
        )

    # dispatched_by
    if not _is_nonempty_string(dr.get("dispatched_by")):
        raise DispatchError(
            "invalid_dispatch_request",
            "dispatched_by must be a non-empty string",
            dr_id,
        )

    # run_context
    rc = dr.get("run_context")
    if not _check_is_dict(rc):
        raise DispatchError(
            "invalid_dispatch_request",
            "run_context must be a dict",
            dr_id,
        )
    if not _is_nonempty_string(rc.get("run_id")) or not _is_nonempty_string(rc.get("stage_id")):
        raise DispatchError(
            "invalid_dispatch_request",
            "run_context must have non-empty run_id and stage_id",
            dr_id,
        )


def _validate_dispatch_result(dr):
    """Validate a dispatch result structurally. Raises DispatchError."""
    if type(dr) is not dict:  # noqa: E721
        raise DispatchError(
            "invalid_dispatch_result",
            "dispatch result is not a dict",
            None,
        )

    for field in _DISPATCH_RESULT_REQUIRED:
        if field not in dr:
            raise DispatchError(
                "invalid_dispatch_result",
                "missing required field: %s" % field,
                dr.get("dispatch_request_id"),
            )

    if not _is_nonempty_string(dr.get("dispatch_request_id")):
        raise DispatchError(
            "invalid_dispatch_result",
            "dispatch_request_id must be non-empty string",
            None,
        )

    status = dr.get("dispatch_status")
    if status not in VALID_DISPATCH_STATUSES:
        raise DispatchError(
            "invalid_dispatch_result",
            "invalid dispatch_status: %s" % status,
            dr.get("dispatch_request_id"),
        )

    if not _is_nonempty_string(dr.get("collected_at")):
        raise DispatchError(
            "invalid_dispatch_result",
            "collected_at must be non-empty string",
            dr.get("dispatch_request_id"),
        )

    if not _is_nonempty_string(dr.get("collected_by")):
        raise DispatchError(
            "invalid_dispatch_result",
            "collected_by must be non-empty string",
            dr.get("dispatch_request_id"),
        )

    if status == "report_produced":
        if "report_binding" not in dr:
            raise DispatchError(
                "invalid_dispatch_result",
                "report_produced requires report_binding",
                dr.get("dispatch_request_id"),
            )
        if "error_code" in dr:
            raise DispatchError(
                "invalid_dispatch_result",
                "report_produced must not have error_code",
                dr.get("dispatch_request_id"),
            )
    else:
        if "error_code" not in dr:
            raise DispatchError(
                "invalid_dispatch_result",
                "non-report_produced requires error_code",
                dr.get("dispatch_request_id"),
            )
        if "report_binding" in dr:
            raise DispatchError(
                "invalid_dispatch_result",
                "non-report_produced must not have report_binding",
                dr.get("dispatch_request_id"),
            )

    if status in ("degraded_storage", "degraded_transport"):
        if "degradation_note" not in dr or not _is_nonempty_string(dr.get("degradation_note")):
            raise DispatchError(
                "invalid_dispatch_result",
                "degraded status requires degradation_note",
                dr.get("dispatch_request_id"),
            )


# ---------------------------------------------------------------------------
# Provider helper
# ---------------------------------------------------------------------------

def _sanitize_error_message(exc):
    """Sanitize an exception message for the structured error surface."""
    msg = str(exc)
    if len(msg) > 500:
        msg = msg[:500]
    return msg


def _invoke_provider(provider, dr_copy):
    """Invoke the provider exactly once. Returns (dispatch_result_dict, None)
    or (None, DispatchError). Never retries or falls back."""
    try:
        provider_return = provider(dr_copy)
    except DispatchError:
        raise  # re-raise our validation errors
    except Exception as exc:
        msg = _sanitize_error_message(exc)
        result = {
            "dispatch_request_id": dr_copy.get("dispatch_request_id"),
            "dispatch_status": "unreachable",
            "error_code": "validator_unreachable",
            "collected_at": dr_copy.get("dispatched_at", ""),
            "collected_by": dr_copy.get("dispatched_by", "dispatcher"),
        }
        _validate_dispatch_result(result)
        return copy.deepcopy(result)

    if not _check_is_dict(provider_return):
        result = {
            "dispatch_request_id": dr_copy.get("dispatch_request_id"),
            "dispatch_status": "no_report",
            "error_code": "validator_no_report",
            "collected_at": dr_copy.get("dispatched_at", ""),
            "collected_by": dr_copy.get("dispatched_by", "dispatcher"),
        }
        _validate_dispatch_result(result)
        return copy.deepcopy(result)

    provider_return = copy.deepcopy(provider_return)

    try:
        result = {
            "dispatch_request_id": dr_copy["dispatch_request_id"],
            "dispatch_status": provider_return.get("dispatch_status", "no_report"),
            "collected_at": provider_return.get(
                "collected_at", dr_copy.get("dispatched_at", "")),
            "collected_by": provider_return.get(
                "collected_by", dr_copy.get("dispatched_by", "dispatcher")),
        }

        for key in ("report_binding", "error_code", "degradation_note"):
            if key in provider_return:
                result[key] = provider_return[key]

        # Validate the constructed result
        result_copy = copy.deepcopy(result)
        _validate_dispatch_result(result_copy)
        return result_copy
    except DispatchError:
        # Provider returned a structurally invalid result, convert to no_report
        result = {
            "dispatch_request_id": dr_copy.get("dispatch_request_id"),
            "dispatch_status": "no_report",
            "error_code": "validator_no_report",
            "collected_at": dr_copy.get("dispatched_at", ""),
            "collected_by": dr_copy.get("dispatched_by", "dispatcher"),
        }
        _validate_dispatch_result(result)
        return copy.deepcopy(result)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def dispatch(dispatch_requests, providers):
    """Dispatch each requirement exactly once through the supplied provider.

    Args:
        dispatch_requests: list of ValidatorDispatchRequest dicts.
            Order defines the output order.
        providers: dict mapping validator_identity to provider callable.
            Each provider receives (dispatch_request: dict) and returns
            a dict with at minimum dispatch_status.  Returned dict may
            include report_binding, error_code, degradation_note,
            collected_at, collected_by.

    Returns:
        list of ValidatorDispatchResult dicts in declaration order.

    Raises:
        DispatchError: pre-dispatch validation failure (no provider called).
            Error codes:
            - invalid_dispatch_request: structural request failure
            - duplicate_dispatch_request: duplicate dispatch_request_id
            - missing_provider: no provider for validator_identity
            - non_callable_provider: provider is not callable
    """
    # Validate input types
    if not _is_list(dispatch_requests):
        raise DispatchError(
            "invalid_dispatch_request",
            "dispatch_requests must be a list",
            None,
        )

    if type(providers) is not dict:  # noqa: E721
        raise DispatchError(
            "invalid_dispatch_request",
            "providers must be a dict",
            None,
        )

    # Check for duplicate dispatch_request_ids
    seen_ids = set()
    for dr in dispatch_requests:
        if type(dr) is not dict:  # noqa: E721
            raise DispatchError(
                "invalid_dispatch_request",
                "each dispatch request must be a dict",
                None,
            )
        dr_id = dr.get("dispatch_request_id")
        if not _is_nonempty_string(dr_id):
            raise DispatchError(
                "invalid_dispatch_request",
                "each dispatch request must have a non-empty dispatch_request_id",
                None,
            )
        if dr_id in seen_ids:
            raise DispatchError(
                "duplicate_dispatch_request",
                "duplicate dispatch_request_id: %s" % dr_id,
                dr_id,
            )
        seen_ids.add(dr_id)

    # Phase 1: Validate all dispatch requests before any provider call
    for dr in dispatch_requests:
        _validate_dispatch_request(dr)

    # Phase 2: Verify providers exist and are callable
    for dr in dispatch_requests:
        vid = dr["validator_identity"]
        if vid not in providers:
            raise DispatchError(
                "missing_provider",
                "no provider for validator_identity: %s" % vid,
                dr["dispatch_request_id"],
            )
        if not callable(providers[vid]):
            raise DispatchError(
                "non_callable_provider",
                "provider for %s is not callable" % vid,
                dr["dispatch_request_id"],
            )

    # Phase 3: Dispatch each requirement exactly once
    results = []
    for dr in dispatch_requests:
        dr_copy = copy.deepcopy(dr)
        provider = providers[dr_copy["validator_identity"]]
        result = _invoke_provider(provider, dr_copy)
        results.append(result)

    return results
