"""
Runtime Adapter - stdlib-only in-process adapter implementing the Runtime Adapter
Contract v1.0.0 over a RuntimeStateSidecar-compatible facade.

Wraps a sidecar-compatible object and exposes a single ``process(request) -> dict``
entry point that validates AdapterRequest envelopes, enforces capability states
including degraded_scope constraints, and delegates exactly once to the sidecar.
Every response is an AdapterResponse dict.

The adapter performs NO SQLite access, lifecycle mutation, GateDecision evaluation,
Validator evidence production, Knowledge materialization, provider-specific field
injection, retry, timestamp/ID/path/key generation, or implicit defaulting.
"""

from __future__ import annotations

import copy
import re
import math
from typing import Any

from scripts.runtime_state_sidecar import RuntimeStateSidecarError

__version__ = "1.0.0"

_SUPPORTED_VERSION = "1.0.0"

_FROZEN_OPERATIONS = [
    "create_run", "append_event", "read_event", "read_events",
    "get_run", "get_stage", "evidence_snapshot", "export_evidence",
]

_VALID_CAPABILITY_STATES = frozenset({
    "full", "degraded_transport", "degraded_scope", "unavailable",
})

_VALID_TOP_LEVEL_FIELDS = frozenset({
    "protocol_version", "request_id", "operation", "payload", "context",
})

_VALID_CONTEXT_FIELDS = frozenset({
    "trigger_artifact", "correlation_id", "executor_identity",
})

_VALID_ARTIFACT_REF_FIELDS = frozenset({
    "artifact_id", "artifact_kind", "artifact_version", "locator", "digest",
})

_PAYLOAD_FIELDS = frozenset({"operation", "params"})

_READ_WRITE_PARAM_SETS = {
    "read_event":    frozenset({"run_id", "event_order"}),
    "read_events":   frozenset({"run_id"}),
    "get_run":       frozenset({"run_id"}),
    "get_stage":     frozenset({"run_id", "stage_id"}),
    "evidence_snapshot": frozenset({"run_id"}),
    "export_evidence":   frozenset({"run_id", "export_id", "exported_at"}),
}

_OBJECT_DATA_OPS = frozenset({
    "create_run", "append_event", "get_run", "get_stage",
    "evidence_snapshot", "export_evidence",
})
_ARRAY_DATA_OPS = frozenset({"read_events"})
_NULLABLE_DATA_OPS = frozenset({"read_event"})

_ALL_CAP_FIELDS = frozenset({"capability", "state", "degradation_note", "scope_constraints"})

_PATH_RE = re.compile(r'(?:[A-Za-z]:[/\\][^\s,;:"]*|/(?:home|tmp|var|etc|usr|opt|dev|root)/[^\s,;:"]*)')

_INTERNAL_ERROR_MSG = "unexpected adapter error"


class RuntimeAdapter:
    """stdlib-only local in-process adapter implementing the Runtime Adapter
    Contract v1.0.0.
    """

    def __init__(
        self,
        sidecar: Any,
        capability_declaration: list[dict[str, Any]],
    ):
        # --- safe facade inspection (no property/descriptor execution) ---
        sidecar_type = type(sidecar)
        for op in _FROZEN_OPERATIONS:
            # Use class __dict__ to avoid descriptor invocation
            cls_attr = None
            for klass in sidecar_type.__mro__:
                if op in klass.__dict__:
                    cls_attr = klass.__dict__[op]
                    break
            if cls_attr is None or not callable(cls_attr):
                raise TypeError(f"sidecar must expose callable method '{op}'")
        self._sidecar = sidecar

        # --- capability_declaration: exact type checks before deepcopy ---
        if type(capability_declaration) is not list:
            raise TypeError("capability_declaration must be a list")
        if len(capability_declaration) != 8:
            raise ValueError(f"capability_declaration must contain exactly 8 entries, got {len(capability_declaration)}")

        caps: dict[str, dict[str, Any]] = {}
        seen = set()
        for idx, entry in enumerate(capability_declaration):
            if type(entry) is not dict:
                raise TypeError(f"capability_declaration[{idx}] must be a dict")
            # Reject extra fields
            extra = set(entry.keys()) - _ALL_CAP_FIELDS
            if extra:
                raise ValueError(f"capability_declaration[{idx}]: extra fields: {sorted(extra)}")

            cap = entry.get("capability")
            if not isinstance(cap, str) or cap not in _FROZEN_OPERATIONS:
                raise ValueError(f"capability_declaration[{idx}]: unknown or missing capability '{cap}'")
            if cap in seen:
                raise ValueError(f"capability_declaration: duplicate capability '{cap}'")
            seen.add(cap)

            state = entry.get("state")
            if state not in _VALID_CAPABILITY_STATES:
                raise ValueError(f"capability_declaration[{idx}]: invalid state '{state}'")

            has_note = "degradation_note" in entry
            has_sc = "scope_constraints" in entry

            if state == "full":
                if has_note:
                    raise ValueError(f"capability_declaration[{idx}]: full forbids degradation_note")
                if has_sc:
                    raise ValueError(f"capability_declaration[{idx}]: full forbids scope_constraints")

            elif state in ("degraded_transport", "unavailable"):
                if not has_note or not isinstance(entry["degradation_note"], str) or not entry["degradation_note"]:
                    raise ValueError(f"capability_declaration[{idx}]: {state} requires non-empty degradation_note")
                if has_sc:
                    raise ValueError(f"capability_declaration[{idx}]: {state} forbids scope_constraints")

            elif state == "degraded_scope":
                if not has_note or not isinstance(entry["degradation_note"], str) or not entry["degradation_note"]:
                    raise ValueError(f"capability_declaration[{idx}]: degraded_scope requires non-empty degradation_note")
                sc = entry.get("scope_constraints")
                if type(sc) is not dict:
                    raise ValueError(f"capability_declaration[{idx}]: degraded_scope requires scope_constraints")
                pal = sc.get("param_allowlist")
                if type(pal) is not dict or len(pal) == 0:
                    raise ValueError(f"capability_declaration[{idx}]: scope_constraints.param_allowlist must be non-empty object")
                for k, v in pal.items():
                    if not isinstance(k, str) or not k:
                        raise ValueError(f"capability_declaration[{idx}]: param_allowlist key must be non-empty string")
                    if type(v) is not list or len(v) == 0:
                        raise ValueError(f"capability_declaration[{idx}]: param_allowlist['{k}'] must be non-empty array")
                    # Check for numeric duplicates: 1 and 1.0 are equal
                    seen_nums = {}
                    for item in v:
                        _validate_json_scalar(item, f"param_allowlist['{k}']")
                        if isinstance(item, (int, float)) and not isinstance(item, bool):
                            nv = float(item)
                            if nv in seen_nums:
                                raise ValueError(
                                    f"capability_declaration[{idx}]: param_allowlist['{k}'] duplicate numeric value {item}"
                                )
                            seen_nums[nv] = item
                    # Check for exact-duplicate non-numeric values
                    non_num = [x for x in v if not isinstance(x, (int, float)) or isinstance(x, bool)]
                    if len(non_num) != len({_json_canonical(x) for x in non_num}):
                        raise ValueError(f"capability_declaration[{idx}]: param_allowlist['{k}'] values must be unique")

            caps[cap] = copy.deepcopy(entry)

        missing = set(_FROZEN_OPERATIONS) - seen
        if missing:
            raise ValueError(f"capability_declaration missing required operations: {sorted(missing)}")
        self._capabilities = caps

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, request: Any) -> dict:
        """Validate, authorise, delegate exactly once, return AdapterResponse."""
        # Exact type check: must be built-in dict, not subclass
        if type(request) is not dict:
            return _malformed_null_echo("request is not a JSON object")

        # Recursively verify exact built-in JSON types before deep copy
        try:
            _check_exact_json_types(request)
        except (TypeError, ValueError) as e:
            return _malformed_null_echo(f"request contains non-JSON value: {e}")

        req = copy.deepcopy(request)

        # === Precedence 1: MALFORMED_ENVELOPE ===
        extra = set(req.keys()) - _VALID_TOP_LEVEL_FIELDS
        if extra:
            return _malformed_null_echo(f"extra top-level fields: {sorted(extra)}")

        protocol_version = req.get("protocol_version")
        request_id_raw = req.get("request_id")
        operation_raw = req.get("operation")
        payload_raw = req.get("payload")
        context_raw = req.get("context")

        if not isinstance(protocol_version, str) or not protocol_version:
            return _malformed_null_echo("protocol_version missing or invalid")
        if not isinstance(request_id_raw, str) or not request_id_raw:
            return _malformed_echo_op(operation_raw, "request_id missing or invalid")
        if not isinstance(operation_raw, str) or not operation_raw:
            return _malformed_echo_rid(request_id_raw, "operation missing or invalid")
        if type(payload_raw) is not dict:
            return _malformed_echo_both(request_id_raw, operation_raw, "payload must be an object")

        # Validate context if present
        if context_raw is not None:
            if type(context_raw) is not dict:
                return _malformed_echo_both(request_id_raw, operation_raw, "context must be an object")
            ctx_extra = set(context_raw.keys()) - _VALID_CONTEXT_FIELDS
            if ctx_extra:
                return _malformed_echo_both(request_id_raw, operation_raw, f"extra context fields: {sorted(ctx_extra)}")
            ta = context_raw.get("trigger_artifact")
            if ta is not None:
                if type(ta) is not dict:
                    return _malformed_echo_both(request_id_raw, operation_raw, "context.trigger_artifact must be an object")
                ta_extra = set(ta.keys()) - _VALID_ARTIFACT_REF_FIELDS
                if ta_extra:
                    return _malformed_echo_both(request_id_raw, operation_raw, f"extra trigger_artifact fields: {sorted(ta_extra)}")
                if not isinstance(ta.get("artifact_id"), str) or not ta["artifact_id"]:
                    return _malformed_echo_both(request_id_raw, operation_raw, "trigger_artifact.artifact_id must be non-empty string")
                if not isinstance(ta.get("artifact_kind"), str) or not ta["artifact_kind"]:
                    return _malformed_echo_both(request_id_raw, operation_raw, "trigger_artifact.artifact_kind must be non-empty string")
            cid = context_raw.get("correlation_id")
            if cid is not None and not isinstance(cid, str):
                return _malformed_echo_both(request_id_raw, operation_raw, "context.correlation_id must be a string")
            ei = context_raw.get("executor_identity")
            if ei is not None and not isinstance(ei, str):
                return _malformed_echo_both(request_id_raw, operation_raw, "context.executor_identity must be a string")

        request_id = request_id_raw
        operation = operation_raw
        payload = payload_raw

        # === Precedence 2: UNSUPPORTED_VERSION ===
        if protocol_version != _SUPPORTED_VERSION:
            return _error_response(
                "UNSUPPORTED_VERSION",
                {"reason": f"unsupported protocol version: {protocol_version}", "supported": _SUPPORTED_VERSION},
                request_id=request_id, operation=operation, capability_state="unavailable",
            )

        # === Precedence 3: UNKNOWN_OPERATION ===
        if operation not in _FROZEN_OPERATIONS:
            return _error_response(
                "UNKNOWN_OPERATION",
                {"reason": f"unknown operation: {operation}", "valid_operations": _FROZEN_OPERATIONS},
                request_id=request_id, operation=operation, capability_state="unavailable",
            )

        # === Precedence 4: INVALID_PAYLOAD ===
        cap_state = self._capabilities.get(operation, {}).get("state", "full")

        payload_extra = set(payload.keys()) - _PAYLOAD_FIELDS
        if payload_extra:
            return _error_response(
                "INVALID_PAYLOAD",
                {"reason": f"extra payload fields: {sorted(payload_extra)}"},
                request_id=request_id, operation=operation, capability_state=cap_state,
            )

        payload_op = payload.get("operation")
        params = payload.get("params", {})

        if payload_op != operation:
            return _error_response(
                "INVALID_PAYLOAD",
                {"reason": f"payload.operation '{payload_op}' does not match outer operation '{operation}'"},
                request_id=request_id, operation=operation, capability_state=cap_state,
            )
        if type(params) is not dict:
            return _error_response(
                "INVALID_PAYLOAD",
                {"reason": "params must be an object"},
                request_id=request_id, operation=operation, capability_state=cap_state,
            )

        # --- operation-specific param validation ---
        if operation in ("create_run", "append_event"):
            run_id = params.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                return _error_response(
                    "INVALID_PAYLOAD",
                    {"reason": "params.run_id must be a non-empty string"},
                    request_id=request_id, operation=operation, capability_state=cap_state,
                )
        elif operation in _READ_WRITE_PARAM_SETS:
            required = _READ_WRITE_PARAM_SETS[operation]
            missing = required - set(params.keys())
            if missing:
                return _error_response(
                    "INVALID_PAYLOAD",
                    {"reason": f"missing required params: {sorted(missing)}"},
                    request_id=request_id, operation=operation, capability_state=cap_state,
                )
            extra = set(params.keys()) - required
            if extra:
                return _error_response(
                    "INVALID_PAYLOAD",
                    {"reason": f"extra params: {sorted(extra)}"},
                    request_id=request_id, operation=operation, capability_state=cap_state,
                )
            for key in required:
                val = params[key]
                if key == "event_order":
                    if type(val) is not int or val <= 0:
                        return _error_response(
                            "INVALID_PAYLOAD",
                            {"reason": "event_order must be a positive integer (bool is invalid)"},
                            request_id=request_id, operation=operation, capability_state=cap_state,
                        )
                elif not isinstance(val, str) or not val:
                    return _error_response(
                        "INVALID_PAYLOAD",
                        {"reason": f"{key} must be a non-empty string"},
                        request_id=request_id, operation=operation, capability_state=cap_state,
                    )

        # === Precedence 5: CAPABILITY_UNAVAILABLE ===
        if cap_state == "unavailable":
            return _error_response(
                "CAPABILITY_UNAVAILABLE",
                {"reason": f"capability '{operation}' is unavailable", "capability_state": cap_state},
                request_id=request_id, operation=operation, capability_state=cap_state,
            )

        # === Precedence 6: CAPABILITY_DEGRADED_SCOPE ===
        if cap_state == "degraded_scope":
            scope_result = _check_scope(self._capabilities[operation], operation, params)
            if scope_result is not None:
                return _error_response(
                    "CAPABILITY_DEGRADED_SCOPE",
                    scope_result,
                    request_id=request_id, operation=operation, capability_state=cap_state,
                )

        # === Precedence 7 & 8: DELEGATE EXACTLY ONCE ===
        try:
            data = self._delegate(operation, params)
        except RuntimeStateSidecarError as e:
            return _error_response(
                "DELEGATED_ERROR",
                {"code": e.code, "detail": e.detail},
                request_id=request_id, operation=operation, capability_state=cap_state,
            )
        except Exception:
            return _error_response(
                "INTERNAL_ERROR",
                {"internal": {"exception_type": "RuntimeError", "message": _INTERNAL_ERROR_MSG}},
                request_id=request_id, operation=operation, capability_state=cap_state,
            )

        # --- validate response data shape BEFORE emitting success ---
        if operation in _OBJECT_DATA_OPS and not isinstance(data, dict):
            return _error_response(
                "INTERNAL_ERROR",
                {"internal": {"exception_type": "TypeError", "message": _INTERNAL_ERROR_MSG}},
                request_id=request_id, operation=operation, capability_state=cap_state,
            )
        if operation in _ARRAY_DATA_OPS and not isinstance(data, list):
            return _error_response(
                "INTERNAL_ERROR",
                {"internal": {"exception_type": "TypeError", "message": _INTERNAL_ERROR_MSG}},
                request_id=request_id, operation=operation, capability_state=cap_state,
            )
        if operation in _NULLABLE_DATA_OPS:
            if not (isinstance(data, dict) or data is None):
                return _error_response(
                    "INTERNAL_ERROR",
                    {"internal": {"exception_type": "TypeError", "message": _INTERNAL_ERROR_MSG}},
                    request_id=request_id, operation=operation, capability_state=cap_state,
                )

        return _success_response(request_id, operation, data, cap_state)

    # ------------------------------------------------------------------
    # Delegation
    # ------------------------------------------------------------------

    def _delegate(self, operation: str, params: dict) -> Any:
        sidecar = self._sidecar
        if operation == "create_run":
            return sidecar.create_run(params)
        if operation == "append_event":
            return sidecar.append_event(params)
        if operation == "read_event":
            return sidecar.read_event(params["run_id"], params["event_order"])
        if operation == "read_events":
            return sidecar.read_events(params["run_id"])
        if operation == "get_run":
            return sidecar.get_run(params["run_id"])
        if operation == "get_stage":
            return sidecar.get_stage(params["run_id"], params["stage_id"])
        if operation == "evidence_snapshot":
            return sidecar.evidence_snapshot(params["run_id"])
        if operation == "export_evidence":
            return sidecar.export_evidence(
                params["run_id"], export_id=params["export_id"], exported_at=params["exported_at"]
            )
        raise RuntimeError(f"unreachable: unknown operation {operation}")


# ---------------------------------------------------------------------------
# exact JSON type guard (no subclass/custom container)
# ---------------------------------------------------------------------------

def _check_exact_json_types(obj: Any, path: str = "request"):
    """Raise TypeError if obj uses non-built-in dict/list types, or contains tuples/custom types."""
    if obj is None:
        return
    if isinstance(obj, bool):
        return
    if type(obj) is int:
        return
    if type(obj) is float:
        if math.isnan(obj) or math.isinf(obj):
            raise ValueError(f"{path}: NaN/Infinity not allowed")
        return
    if type(obj) is str:
        return
    if type(obj) is dict:
        for k, v in obj.items():
            if type(k) is not str:
                raise TypeError(f"{path}: dict key must be str, got {type(k).__name__}")
            _check_exact_json_types(v, f"{path}.{k}")
        return
    if type(obj) is list:
        for i, v in enumerate(obj):
            _check_exact_json_types(v, f"{path}[{i}]")
        return
    raise TypeError(f"{path}: non-JSON type {type(obj).__name__}")


# ---------------------------------------------------------------------------
# scope_constraints
# ---------------------------------------------------------------------------

def _check_scope(cap_entry: dict, operation: str, params: dict) -> dict | None:
    sc = cap_entry.get("scope_constraints")
    if not sc:
        return {"reason": f"degraded_scope for '{operation}' has no scope_constraints"}
    pal = sc.get("param_allowlist", {})
    for field, allowed in pal.items():
        if field not in params:
            return {"reason": f"constrained param '{field}' missing from request", "param_allowlist": pal}
        actual = params[field]
        matched = False
        for candidate in allowed:
            if _type_sensitive_eq(actual, candidate):
                matched = True
                break
        if not matched:
            return {"reason": f"constrained param '{field}' value not in allowlist", "param_allowlist": pal}
    return None


def _type_sensitive_eq(a: Any, b: Any) -> bool:
    """Type-sensitive equality: bool!=number, int~=float, null!=anything."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if not (isinstance(a, bool) or isinstance(b, bool)):
            return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if type(a) is not type(b):
        return False
    return a == b


def _validate_json_scalar(value: Any, label: str):
    """Raise ValueError if value is not a plain JSON scalar."""
    if value is None:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise ValueError(f"{label}: NaN/Infinity not allowed")
        return
    if isinstance(value, str):
        return
    raise ValueError(f"{label}: non-scalar value type {type(value).__name__}")


def _json_canonical(value: Any) -> Any:
    """Return a canonical representation for uniqueness checks."""
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value)
    if isinstance(value, str):
        return ("str", value)
    return ("other", id(value))


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def _success_response(request_id: str, operation: str, data: Any, capability_state: str) -> dict:
    return {
        "protocol_version": _SUPPORTED_VERSION,
        "request_id": request_id,
        "status": "success",
        "operation": operation,
        "data": data,
        "capability_state": capability_state,
    }


def _error_response(code: str, detail: dict, request_id, operation, capability_state: str) -> dict:
    return {
        "protocol_version": _SUPPORTED_VERSION,
        "request_id": request_id,
        "status": "error",
        "operation": operation,
        "error": {
            "code": code,
            "detail": detail,
            "request_id": request_id,
            "operation": operation,
        },
        "capability_state": capability_state,
    }


# ---------------------------------------------------------------------------
# MALFORMED_ENVELOPE null-echo helpers
# ---------------------------------------------------------------------------

def _malformed_null_echo(reason: str) -> dict:
    return _error_response("MALFORMED_ENVELOPE", {"reason": reason},
                           request_id=None, operation=None, capability_state="unavailable")


def _malformed_echo_op(op_raw: Any, reason: str) -> dict:
    return _error_response("MALFORMED_ENVELOPE", {"reason": reason},
                           request_id=None,
                           operation=op_raw if isinstance(op_raw, str) and op_raw else None,
                           capability_state="unavailable")


def _malformed_echo_rid(rid_raw: Any, reason: str) -> dict:
    return _error_response("MALFORMED_ENVELOPE", {"reason": reason},
                           request_id=rid_raw if isinstance(rid_raw, str) and rid_raw else None,
                           operation=None, capability_state="unavailable")


def _malformed_echo_both(rid_raw: Any, op_raw: Any, reason: str) -> dict:
    return _error_response("MALFORMED_ENVELOPE", {"reason": reason},
                           request_id=rid_raw if isinstance(rid_raw, str) and rid_raw else None,
                           operation=op_raw if isinstance(op_raw, str) and op_raw else None,
                           capability_state="unavailable")
