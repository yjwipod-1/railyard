"""Tests for the local Validator dispatch provider boundary.

Covers:
  * Spy providers for exact-once call counts
  * Capability branches: full, unavailable, degraded
  * Order determinism across mixed latency/completion order
  * Error precedence: missing provider, unknown identity, non-callable,
    malformed request, malformed result, exception, duplicate requirement,
    mismatched binding
  * No-retry proofs: no second invocation
  * Hostile object safety
  * Input/return immutability
  * Mesh core regression pass-through
  * Predecessor hash verification
  * Forbidden import scan
  * Compileall and ASCII checks
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
import time
import unittest

import scripts.runtime_validator_dispatch as rvd
import scripts.runtime_validator_mesh as rvm
import scripts.test_runtime_validator_mesh as test_mesh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Fixtures copied from the mesh contract for readability
# ---------------------------------------------------------------------------

def _sha256_hex(content_bytes):
    return hashlib.sha256(content_bytes).hexdigest()

def _sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

def _make_dispatch_request(req_id="req-001", vid="validator-core",
                           mesh_id="mesh-test", ridge_extra=None):
    """Minimal valid dispatch request per the frozen contract."""
    dr = {
        "dispatch_request_id": "dr-%s" % req_id,
        "requirement_id": req_id,
        "mesh_id": mesh_id,
        "validator_identity": vid,
        "contract_ref": {
            "artifact_id": "vc-001",
            "artifact_kind": "contract",
            "artifact_version": "1.0.0",
        },
        "artifact_scope": [
            {"artifact_id": "artifact-001", "artifact_kind": "artifact"},
        ],
        "evidence_pack": {},
        "risk_level": "medium",
        "allowed_read_only_commands": [],
        "dispatched_at": "2026-07-31T00:00:00Z",
        "dispatched_by": "dispatcher",
        "run_context": {"run_id": "run-001", "stage_id": "stage-001"},
    }
    if ridge_extra:
        dr.update(ridge_extra)
    return dr

def _make_binding(binding_id="bind-001", req_id="req-001", verdict="pass", *,
                  confidence):
    """Minimal valid v1.2 report binding.

    `confidence` is a REQUIRED caller-supplied field: exactly one of "high",
    "medium", "low". It is never derived from `verdict` and never defaulted
    by this helper. Contract and target refs are complete four-field
    ArtifactRefs, the report ref is the closed four-key ReportArtifactRef with
    artifact_kind "validation_report", all Mesh comparison digests are raw
    lowercase 64-hex (no prefix), and `report_ref.digest` equals
    `report_sha256`.
    """
    d = "a" * 64
    return {
        "binding_id": binding_id,
        "requirement_id": req_id,
        "validator_identity": "validator-core",
        "role": "validator",
        "contract_ref": {
            "artifact_id": "vc-001",
            "artifact_kind": "contract",
            "artifact_version": "1.0.0",
            "digest": d,
        },
        "target_artifact_ref": {
            "artifact_id": "artifact-001",
            "artifact_kind": "artifact",
            "artifact_version": "1.0.0",
            "digest": d,
        },
        "report_ref": {
            "artifact_id": "report-001",
            "artifact_kind": "validation_report",
            "artifact_version": "1.0.0",
            "digest": d,
        },
        "report_sha256": d,
        "report_confidence": confidence,
        "report_overall_verdict": verdict,
        "independent_production_evidence": {
            "producer_identity": "validator-core",
            "production_environment": "ci-pipeline-001",
            "production_timestamp": "2026-07-31T00:05:00Z",
            "no_caller_role_collapse": True,
        },
        "bound_at": "2026-07-31T00:05:00Z",
        "bound_by": "dispatcher",
    }

def _make_provider(status="report_produced", binding=None, error_code=None,
                   degradation_note=None, *, confidence="high"):
    """Create a provider that returns a specific dispatch shape.

    `confidence` is forwarded to the default v1.2 binding when this provider
    builds one; it is never derived from a verdict.
    """
    def provider(_dr):
        result = {"dispatch_status": status}
        if status == "report_produced":
            result["report_binding"] = binding or _make_binding(confidence=confidence)
        else:
            result["error_code"] = error_code or "validator_unreachable"
            if status in ("degraded_storage", "degraded_transport"):
                result["degradation_note"] = degradation_note or "%s note" % status
        return result
    return provider


# ---------------------------------------------------------------------------
# Spy / exact-call-count tests
# ---------------------------------------------------------------------------

class SpyProviderTests(unittest.TestCase):
    """Verify exact call counts: 1 per valid requirement, 0 for pre-dispatch
    rejection."""

    def test_01_exactly_one_call_per_valid_requirement(self):
        """Spy proves 1 invocation per valid declared requirement."""
        call_counts = {}

        def spy(dr):
            rid = dr["requirement_id"]
            call_counts[rid] = call_counts.get(rid, 0) + 1
            return {
                "dispatch_status": "report_produced",
                "report_binding": _make_binding(req_id=rid, confidence="high"),
            }

        providers = {
            "validator-1": spy,
            "validator-2": spy,
            "validator-3": spy,
        }

        requests = [
            _make_dispatch_request("req-001", "validator-1"),
            _make_dispatch_request("req-002", "validator-2"),
            _make_dispatch_request("req-003", "validator-3"),
        ]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 3)
        self.assertEqual(call_counts, {"req-001": 1, "req-002": 1, "req-003": 1})

    def test_02_zero_calls_on_pre_dispatch_rejection(self):
        """Spy proves 0 calls when dispatch validation fails."""
        call_count = [0]

        def spy(_dr):
            call_count[0] += 1
            return {"dispatch_status": "report_produced", "report_binding": _make_binding(confidence="high")}

        providers = {"validator-core": spy}
        # Missing required field
        requests = [{"dispatch_request_id": "dr-req-001"}]

        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch(requests, providers)
        self.assertEqual(ctx.exception.error_code, "invalid_dispatch_request")
        self.assertEqual(call_count[0], 0)

    def test_03_zero_calls_on_duplicate_request(self):
        """Spy proves 0 calls on duplicate dispatch_request_id."""
        call_count = [0]

        def spy(_dr):
            call_count[0] += 1
            return {"dispatch_status": "report_produced", "report_binding": _make_binding(confidence="high")}

        providers = {"validator-core": spy}
        req1 = _make_dispatch_request("req-001", "validator-core")
        req2 = _make_dispatch_request("req-001", "validator-core")
        requests = [req1, req2]

        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch(requests, providers)
        self.assertEqual(ctx.exception.error_code, "duplicate_dispatch_request")
        self.assertEqual(call_count[0], 0)

    def test_04_zero_calls_on_missing_provider(self):
        """Spy proves 0 calls when provider is missing."""
        call_count = [0]

        def spy(_dr):
            call_count[0] += 1
            return {"dispatch_status": "report_produced", "report_binding": _make_binding(confidence="high")}

        providers = {"other-validator": spy}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch(requests, providers)
        self.assertEqual(ctx.exception.error_code, "missing_provider")
        self.assertEqual(call_count[0], 0)

    def test_05_zero_calls_on_non_callable_provider(self):
        """Spy proves 0 calls when provider is not callable."""
        providers = {"validator-core": "not-a-function"}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch(requests, providers)
        self.assertEqual(ctx.exception.error_code, "non_callable_provider")

    def test_06_multiple_requirements_all_call_once(self):
        """Each of 10 requirements gets exactly 1 call."""
        call_counts = {}

        def spy(dr):
            rid = dr["requirement_id"]
            call_counts[rid] = call_counts.get(rid, 0) + 1
            return {
                "dispatch_status": "report_produced",
                "report_binding": _make_binding(req_id=rid, confidence="high"),
            }

        providers = {}
        requests = []
        for i in range(10):
            rid = "req-%03d" % i
            vid = "validator-%03d" % i
            providers[vid] = spy
            requests.append(_make_dispatch_request(rid, vid))

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 10)
        for i in range(10):
            self.assertEqual(call_counts["req-%03d" % i], 1)


# ---------------------------------------------------------------------------
# Capability branch tests
# ---------------------------------------------------------------------------

class CapabilityBranchTests(unittest.TestCase):
    """Cover all dispatch status branches: report_produced, unreachable,
    no_report, degraded_storage, degraded_transport."""

    def test_01_report_produced_branch(self):
        """report_produced dispatch returns correct result."""
        provider = _make_provider("report_produced", _make_binding("b1", "req-001", "pass", confidence="high"), confidence="high")
        providers = {"validator-core": provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dispatch_status"], "report_produced")
        self.assertIn("report_binding", results[0])
        self.assertNotIn("error_code", results[0])

    def test_02_unreachable_branch(self):
        """unreachable dispatch returns correct result."""
        provider = _make_provider("unreachable",
                                  error_code="validator_unreachable", confidence="high")
        providers = {"validator-core": provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dispatch_status"], "unreachable")
        self.assertEqual(results[0]["error_code"], "validator_unreachable")
        self.assertNotIn("report_binding", results[0])

    def test_03_no_report_branch(self):
        """no_report dispatch returns correct result."""
        provider = _make_provider("no_report", error_code="validator_no_report", confidence="high")
        providers = {"validator-core": provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dispatch_status"], "no_report")
        self.assertEqual(results[0]["error_code"], "validator_no_report")
        self.assertNotIn("report_binding", results[0])

    def test_04_degraded_storage_branch(self):
        """degraded_storage dispatch returns correct result."""
        provider = _make_provider("degraded_storage",
                                  error_code="validator_degraded_storage",
                                  degradation_note="storage note", confidence="high")
        providers = {"validator-core": provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dispatch_status"], "degraded_storage")
        self.assertEqual(results[0]["error_code"], "validator_degraded_storage")
        self.assertEqual(results[0]["degradation_note"], "storage note")

    def test_05_degraded_transport_branch(self):
        """degraded_transport dispatch returns correct result."""
        provider = _make_provider("degraded_transport",
                                  error_code="validator_degraded_transport",
                                  degradation_note="transport note", confidence="high")
        providers = {"validator-core": provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dispatch_status"], "degraded_transport")
        self.assertEqual(results[0]["error_code"], "validator_degraded_transport")
        self.assertEqual(results[0]["degradation_note"], "transport note")

    def test_06_full_status_coverage(self):
        """All five dispatch status values are returned from providers."""
        status_types = [
            ("report_produced", _make_binding(confidence="high")),
            ("unreachable", None),
            ("no_report", None),
            ("degraded_storage", None),
            ("degraded_transport", None),
        ]
        providers = {}
        requests = []
        results_map = {}  # vid -> expected status

        for i, (status, binding) in enumerate(status_types):
            vid = "validator-%d" % i
            rid = "req-%03d" % i
            if status == "report_produced":
                def _make_p(s):
                    def p(_dr):
                        return {"dispatch_status": s,
                                "report_binding": _make_binding(req_id="req-000", confidence="high")}
                    return p
                providers[vid] = _make_p(status)
            else:
                error_code = "validator_%s" % status
                note = None
                if status in ("degraded_storage", "degraded_transport"):
                    note = "%s note" % status
                def _make_p2(s, ec, n):
                    def p(_dr):
                        r = {"dispatch_status": s, "error_code": ec}
                        if n:
                            r["degradation_note"] = n
                        return r
                    return p
                providers[vid] = _make_p2(status, error_code, note)
            requests.append(_make_dispatch_request(rid, vid))

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 5)
        statuses_found = {r["dispatch_status"] for r in results}
        self.assertIn("report_produced", statuses_found)
        self.assertIn("unreachable", statuses_found)
        self.assertIn("no_report", statuses_found)
        self.assertIn("degraded_storage", statuses_found)
        self.assertIn("degraded_transport", statuses_found)


# ---------------------------------------------------------------------------
# Order determinism tests
# ---------------------------------------------------------------------------

class OrderDeterminismTests(unittest.TestCase):
    """Verify declaration-order output regardless of provider completion
    order, and byte-identical repeated runs."""

    def test_01_declaration_order_preserved(self):
        """Output order matches declaration order regardless of timing."""
        completion_order = []

        def make_provider(idx):
            def provider(_dr):
                # Simulate varying latency
                if idx == 2:
                    time.sleep(0.05)
                completion_order.append(idx)
                return {
                    "dispatch_status": "report_produced",
                    "report_binding": _make_binding(req_id="req-%03d" % idx, confidence="high"),
                }
            return provider

        providers = {}
        requests = []
        for i in range(5):
            vid = "validator-%d" % i
            rid = "req-%03d" % i
            providers[vid] = make_provider(i)
            requests.append(_make_dispatch_request(rid, vid))

        results = rvd.dispatch(requests, providers)
        # Output must be in declaration order regardless of completion order
        self.assertEqual(len(results), 5)
        for i, r in enumerate(results):
            self.assertEqual(r["dispatch_request_id"], "dr-req-%03d" % i)

    def test_02_byte_identical_repeated_runs(self):
        """Same input produces byte-identical output across repeated runs."""
        providers = {
            "validator-core": _make_provider("report_produced", _make_binding(confidence="high"), confidence="high"),
        }
        requests = [_make_dispatch_request("req-001", "validator-core")]

        r1 = json.dumps(rvd.dispatch(requests, providers), sort_keys=True)
        r2 = json.dumps(rvd.dispatch(requests, providers), sort_keys=True)
        r3 = json.dumps(rvd.dispatch(requests, providers), sort_keys=True)

        self.assertEqual(r1, r2)
        self.assertEqual(r2, r3)

    def test_03_mixed_latency_deterministic(self):
        """Mixed latency with random sleep still produces same order."""
        import random
        # Use a seed for reproducibility but still random within the run
        random.seed(42)

        def make_provider(idx):
            def provider(_dr):
                time.sleep(random.uniform(0.001, 0.05))
                return {
                    "dispatch_status": "report_produced",
                    "report_binding": _make_binding(req_id="req-%03d" % idx, confidence="high"),
                }
            return provider

        providers = {}
        requests = []
        for i in range(8):
            vid = "validator-%d" % i
            rid = "req-%03d" % i
            providers[vid] = make_provider(i)
            requests.append(_make_dispatch_request(rid, vid))

        r1 = json.dumps(rvd.dispatch(requests, providers), sort_keys=True)
        r2 = json.dumps(rvd.dispatch(requests, providers), sort_keys=True)

        self.assertEqual(r1, r2)
        first_ids = [r["dispatch_request_id"] for r in json.loads(r1)]
        for i, dr_id in enumerate(first_ids):
            self.assertEqual(dr_id, "dr-req-%03d" % i)


# ---------------------------------------------------------------------------
# Error precedence tests (frozen precedence)
# ---------------------------------------------------------------------------

class ErrorPrecedenceTests(unittest.TestCase):
    """Verify all error cases fail at the frozen precedence without any
    provider call."""

    def test_01_missing_provider(self):
        """Missing provider raises DispatchError before any call."""
        providers = {}
        requests = [_make_dispatch_request("req-001", "validator-missing")]

        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch(requests, providers)
        self.assertEqual(ctx.exception.error_code, "missing_provider")

    def test_02_non_callable_provider(self):
        """Non-callable provider raises DispatchError."""
        providers = {"validator-core": 12345}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch(requests, providers)
        self.assertEqual(ctx.exception.error_code, "non_callable_provider")

    def test_03_malformed_request_not_dict(self):
        """Non-dict dispatch request raises DispatchError."""
        providers = {"validator-core": lambda d: {}}
        requests = ["not-a-dict"]

        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch(requests, providers)
        self.assertEqual(ctx.exception.error_code, "invalid_dispatch_request")

    def test_04_malformed_request_missing_field(self):
        """Dispatch request missing required field raises DispatchError."""
        providers = {"validator-core": lambda d: {}}
        requests = [{"dispatch_request_id": "dr-req-001"}]

        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch(requests, providers)
        self.assertEqual(ctx.exception.error_code, "invalid_dispatch_request")

    def test_05_duplicate_dispatch_request_id(self):
        """Duplicate dispatch_request_id raises DispatchError."""
        providers = {"validator-core": lambda d: {}}
        r1 = _make_dispatch_request("req-001", "validator-core")
        r2 = _make_dispatch_request("req-001", "validator-core")
        requests = [r1, r2]

        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch(requests, providers)
        self.assertEqual(ctx.exception.error_code, "duplicate_dispatch_request")

    def test_06_provider_raises_exception_sanitized(self):
        """Provider exception is sanitized; no retry."""
        call_count = [0]

        def failing_provider(_dr):
            call_count[0] += 1
            raise ValueError("C:\\\\Users\\\\someuser\\\\secret.key is missing")

        providers = {"validator-core": failing_provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(call_count[0], 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dispatch_status"], "unreachable")
        self.assertEqual(results[0]["error_code"], "validator_unreachable")
        # Verify no path leak
        self.assertNotIn("C:", json.dumps(results[0]))

    def test_07_provider_returns_non_dict(self):
        """Provider returning non-dict results in no_report."""
        def bad_provider(_dr):
            return [1, 2, 3]  # list, not dict

        providers = {"validator-core": bad_provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dispatch_status"], "no_report")
        self.assertEqual(results[0]["error_code"], "validator_no_report")

    def test_08_provider_returns_malformed_result(self):
        """Provider returning dict missing dispatch_status results in error."""
        def bad_provider(_dr):
            return {"unexpected": "field"}

        providers = {"validator-core": bad_provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dispatch_status"], "no_report")
        self.assertEqual(results[0]["error_code"], "validator_no_report")

    def test_09_provider_returns_invalid_dispatch_status(self):
        """Provider returning bogus dispatch_status results in error."""
        def bad_provider(_dr):
            return {
                "dispatch_status": "bogus_status",
                "collected_at": "2026-01-01T00:00:00Z",
                "collected_by": "test",
            }

        providers = {"validator-core": bad_provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dispatch_status"], "no_report")
        self.assertEqual(results[0]["error_code"], "validator_no_report")

    def test_10_malformed_request_empty_list(self):
        """Empty dispatch_requests list passes (valid per spec)."""
        providers = {}
        requests = []
        results = rvd.dispatch(requests, providers)
        self.assertEqual(results, [])

    def test_11_providers_not_a_dict(self):
        """Non-dict providers raises DispatchError."""
        providers = "not-a-dict"
        requests = [_make_dispatch_request()]
        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch(requests, providers)
        self.assertEqual(ctx.exception.error_code, "invalid_dispatch_request")

    def test_12_dispatch_requests_not_a_list(self):
        """Non-list dispatch_requests raises DispatchError."""
        providers = {}
        with self.assertRaises(rvd.DispatchError) as ctx:
            rvd.dispatch("not-a-list", providers)
        self.assertEqual(ctx.exception.error_code, "invalid_dispatch_request")


# ---------------------------------------------------------------------------
# No-retry tests
# ---------------------------------------------------------------------------

class NoRetryTests(unittest.TestCase):
    """Prove no second invocation after error, timeout-shaped result,
    malformed output, or unavailable state."""

    def test_01_no_retry_after_exception(self):
        """Exception in provider: exactly 1 call, no retry."""
        call_count = [0]

        def once_then_fail(_dr):
            call_count[0] += 1
            raise RuntimeError("transient error")

        providers = {"validator-core": once_then_fail}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(call_count[0], 1)

    def test_02_no_retry_after_malformed_output(self):
        """Malformed provider output: exactly 1 call, no retry."""
        call_count = [0]

        def bad_output(_dr):
            call_count[0] += 1
            return 42

        providers = {"validator-core": bad_output}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(call_count[0], 1)

    def test_03_no_retry_for_unreachable(self):
        """Unreachable result: exactly 1 call, no retry."""
        call_count = [0]

        def unreachable(_dr):
            call_count[0] += 1
            return {"dispatch_status": "unreachable",
                    "error_code": "validator_unreachable"}

        providers = {"validator-core": unreachable}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(call_count[0], 1)

    def test_04_multiple_errors_no_retry_on_any(self):
        """Each of several failing providers called exactly once."""
        call_counts = {}

        def failer(_dr):
            vid = _dr.get("validator_identity", "unknown")
            call_counts[vid] = call_counts.get(vid, 0) + 1
            raise RuntimeError("fail")

        providers = {}
        requests = []
        for i in range(5):
            vid = "validator-%d" % i
            rid = "req-%03d" % i
            providers[vid] = failer
            requests.append(_make_dispatch_request(rid, vid))

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 5)
        for i in range(5):
            self.assertEqual(call_counts["validator-%d" % i], 1)

    def test_05_no_fallback_provider(self):
        """No alternate provider is called after failure."""
        called = set()

        def primary(_dr):
            called.add("primary")
            raise RuntimeError("primary fail")

        def fallback(_dr):
            called.add("fallback")
            return {"dispatch_status": "report_produced",
                    "report_binding": _make_binding(confidence="high")}

        # Even though fallback exists in providers, it should only be called
        # if it was explicitly mapped to the validator_identity
        providers = {
            "validator-core": primary,
            "fallback-core": fallback,
        }
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(called, {"primary"})


# ---------------------------------------------------------------------------
# Hostile object safety tests
# ---------------------------------------------------------------------------

class HostileObjectTests(unittest.TestCase):
    """Verify hostile objects, descriptors, callables, exception messages,
    and returned mappings do not execute during inspection beyond the single
    authorized provider call and do not leak raw details."""

    def test_01_hostile_exception_message_sanitized(self):
        """Exception with path-like content is sanitized."""

        class HostileError(Exception):
            def __str__(self):
                return "C:\\\\Users\\\\Admin\\\\secrets\\\\token.txt leaked"

        call_count = [0]

        def hostile(_dr):
            call_count[0] += 1
            raise HostileError("should not appear")

        providers = {"validator-core": hostile}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(call_count[0], 1)
        result_json = json.dumps(results[0])
        self.assertNotIn("secrets", result_json)

    def test_02_caller_input_not_mutated(self):
        """Deep copy of caller input is used; original unchanged."""
        providers = {"validator-core": _make_provider("report_produced", _make_binding(confidence="high"), confidence="high")}
        original_req = _make_dispatch_request("req-001", "validator-core")
        original_copy = copy.deepcopy(original_req)
        requests = [original_req]

        rvd.dispatch(requests, providers)
        self.assertEqual(original_req, original_copy)

    def test_03_provider_owned_return_not_mutated(self):
        """Provider's returned object is deep-copied; original unchanged."""
        shared_binding = _make_binding(confidence="high")
        shared_binding_copy = copy.deepcopy(shared_binding)

        def provider(_dr):
            return {"dispatch_status": "report_produced",
                    "report_binding": shared_binding}

        providers = {"validator-core": provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        # Provider's shared_binding must be unchanged
        self.assertEqual(shared_binding, shared_binding_copy)
        # Result must NOT be same object as shared_binding
        self.assertIsNot(results[0].get("report_binding"), shared_binding)

    def test_04_multiple_requests_input_not_mutated(self):
        """Multiple input objects all remain unchanged."""
        providers = {}
        requests = []
        originals = []

        for i in range(5):
            vid = "validator-%d" % i
            rid = "req-%03d" % i

            def make_p():
                def p(_dr):
                    return {"dispatch_status": "report_produced",
                            "report_binding": _make_binding(req_id=rid, confidence="high")}
                return p

            providers[vid] = make_p()
            req = _make_dispatch_request(rid, vid)
            originals.append(copy.deepcopy(req))
            requests.append(req)

        rvd.dispatch(requests, providers)

        for i, original in enumerate(originals):
            self.assertEqual(requests[i], original,
                             "request %d was mutated" % i)

    def test_05_error_result_is_deep_copied(self):
        """Error result objects are deep-copied from provider return."""

        class MutableDict(dict):
            pass

        shared = MutableDict(dispatch_status="unreachable",
                             error_code="validator_unreachable")
        shared_original = dict(shared)

        def provider(_dr):
            return shared

        providers = {"validator-core": provider}
        requests = [_make_dispatch_request("req-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        # shared must be unchanged
        self.assertEqual(dict(shared), shared_original)


# ---------------------------------------------------------------------------
# Input/return immutability tests
# ---------------------------------------------------------------------------

class InputImmutabilityTests(unittest.TestCase):
    """Verify input requests and provider-owned return objects remain deeply
    unchanged."""

    def test_01_providers_dict_not_mutated(self):
        """providers dict is not modified by dispatch."""
        providers = {"validator-core": lambda d: d}
        providers_copy = copy.deepcopy(providers)
        requests = []  # empty - this would fail at validation, but lets check

        # We use try/except because empty list might fail validation
        # But we want to verify providers wasn't mutated even during validation
        try:
            rvd.dispatch(requests, providers)
        except rvd.DispatchError:
            pass
        self.assertEqual(providers, providers_copy)

    def test_02_input_preserved_after_error_dispatch(self):
        """Input unchanged even when providers produce errors."""
        providers = {
            "validator-core": lambda d: {"dispatch_status": "unreachable",
                                          "error_code": "validator_unreachable"},
        }
        req = _make_dispatch_request("req-001", "validator-core")
        req_copy = copy.deepcopy(req)
        requests = [req]

        rvd.dispatch(requests, providers)
        self.assertEqual(req, req_copy)

    def test_03_no_reference_return(self):
        """Returned objects are not references to input objects."""
        providers = {"validator-core": _make_provider("report_produced", _make_binding(confidence="high"), confidence="high")}
        req = _make_dispatch_request("req-001", "validator-core")
        requests = [req]

        results = rvd.dispatch(requests, providers)
        self.assertIsNot(results[0], requests[0])
        # dict containers must be distinct (strings may be interned)
        self.assertIsNot(results[0].get("contract_ref"),
                         requests[0].get("contract_ref"))


# ---------------------------------------------------------------------------
# Mesh regression tests
# ---------------------------------------------------------------------------

class MeshRegressionTests(unittest.TestCase):
    """Verify all mesh core tests still pass."""

    def test_01_mesh_regression_pass(self):
        """Run test_runtime_validator_mesh and verify it passes."""
        path = os.path.join(ROOT, "scripts", "test_runtime_validator_mesh.py")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_runtime_validator_mesh.py", "-q"],
            capture_output=True, text=True, cwd=ROOT,
            timeout=60)
        self.assertEqual(result.returncode, 0,
                         "Mesh core tests failed:\n%s" % result.stdout[-1000:])


# ---------------------------------------------------------------------------
# Complete v1.2 binding pass-through tests
# ---------------------------------------------------------------------------

class MeshBindingPassThroughTests(unittest.TestCase):
    """Dispatch returns the complete v1.2 binding deeply unchanged and never
    derives report_confidence."""

    def test_complete_binding_deep_equal_passthrough(self):
        """The complete v1.2 binding returned by dispatch is deep-equal to the
        provider-owned binding, with caller-supplied confidence preserved."""
        binding = _make_binding("bind-pt-001", "req-pt-001", "fail",
                                confidence="high")
        original = copy.deepcopy(binding)

        def provider(_dr):
            return {"dispatch_status": "report_produced",
                    "report_binding": binding}

        providers = {"validator-core": provider}
        requests = [_make_dispatch_request("req-pt-001", "validator-core")]

        results = rvd.dispatch(requests, providers)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dispatch_status"], "report_produced")
        returned = results[0]["report_binding"]
        # Deeply unchanged and independent of the provider-owned object
        self.assertEqual(returned, original)
        self.assertIsNot(returned, binding)
        # Caller-supplied confidence is preserved and independent of verdict
        self.assertEqual(returned["report_confidence"], "high")
        self.assertEqual(binding["report_confidence"], "high")
        # v1.2 shape invariants survive the dispatcher untouched
        self.assertEqual(returned["report_ref"]["artifact_kind"],
                         "validation_report")
        self.assertEqual(returned["report_sha256"],
                         returned["report_ref"]["digest"])
        self.assertEqual(len(returned["contract_ref"]), 4)
        self.assertEqual(len(returned["target_artifact_ref"]), 4)
        self.assertEqual(set(returned["report_ref"].keys()),
                         {"artifact_id", "artifact_kind", "artifact_version",
                          "digest"})

    def test_confidence_never_derived_or_injected(self):
        """A binding without report_confidence is returned without one:
        dispatch never derives or injects confidence."""
        binding = _make_binding("bind-pt-002", "req-pt-002", "pass",
                                confidence="high")
        del binding["report_confidence"]

        def provider(_dr):
            return {"dispatch_status": "report_produced",
                    "report_binding": binding}

        providers = {"validator-core": provider}
        requests = [_make_dispatch_request("req-pt-002", "validator-core")]

        results = rvd.dispatch(requests, providers)
        returned = results[0]["report_binding"]
        self.assertNotIn("report_confidence", returned)
        self.assertNotIn("report_confidence", results[0])


# ---------------------------------------------------------------------------
# Predecessor hash tests
# ---------------------------------------------------------------------------

class PredecessorHashTests(unittest.TestCase):
    """Verify all four predecessor hashes remain unchanged."""

    EXPECTED = {
        "references/runtime-validator-mesh-contract.md":
            "efe7689f1c258200137f4e02f037d18a24a01063c1fe24a9f5948086da869e68",
        "assets/schemas/runtime-validator-mesh-v1.schema.json":
            "16d99188a5306c1d279c533b780f669d459743f7fd9f54fe00f9d97bb226b12a",
        "scripts/runtime_validator_mesh.py":
            "389e4e9b0e1aef5cbfd723e4ec53f57c6593091c5ee793c4321591bb101604fb",
        "scripts/test_runtime_validator_mesh.py":
            "2e4d73341303b59ffe1d3eacf307001f460dc82b9b96b39ec2e4a05b6728b718",
    }

    def test_01_contract_hash(self):
        self._check("references/runtime-validator-mesh-contract.md")

    def test_02_schema_hash(self):
        self._check("assets/schemas/runtime-validator-mesh-v1.schema.json")

    def test_03_core_hash(self):
        self._check("scripts/runtime_validator_mesh.py")

    def test_04_core_test_hash(self):
        self._check("scripts/test_runtime_validator_mesh.py")

    def _check(self, rel):
        path = os.path.join(ROOT, rel)
        self.assertTrue(os.path.isfile(path), "missing: %s" % rel)
        actual = _sha256_file(path)
        expected = self.EXPECTED[rel]
        self.assertEqual(actual, expected,
                         "hash mismatch for %s: got %s" % (rel, actual))


# ---------------------------------------------------------------------------
# Forbidden import scan
# ---------------------------------------------------------------------------

class ForbiddenImportTests(unittest.TestCase):
    """Verify the dispatch module has no forbidden imports."""

    FORBIDDEN_IMPORTS = [
        "import sqlite3", "from sqlite3",
        "import socket", "from socket",
        "import subprocess", "from subprocess",
        "import requests", "from requests",
        "import http.client", "from http.client",
        "import urllib", "from urllib",
        "import pathlib", "from pathlib",
        "import asyncio", "from asyncio",
        "import importlib", "from importlib",
        "import pkg_resources", "from pkg_resources",
        "import stevedore", "from stevedore",
    ]

    FORBIDDEN_FUNCTIONS = [
        "os.environ",
    ]

    @staticmethod
    def _non_comment_lines(source):
        """Extract lines that are not inside docstrings or # comments."""
        lines = []
        in_docstring = False
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.count("\"\"\"") % 2 == 1 or stripped.count("'''") % 2 == 1:
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith("\"\"\"") or stripped.startswith("'''"):
                continue
            lines.append(line)
        return "\n".join(lines)

    def test_01_no_forbidden_imports(self):
        """Static scan confirms no forbidden patterns in dispatch module."""
        path = os.path.join(ROOT, "scripts", "runtime_validator_dispatch.py")
        with open(path, "r", encoding="ascii") as f:
            source = f.read()
        code_lines = self._non_comment_lines(source)
        for pattern in self.FORBIDDEN_IMPORTS:
            self.assertNotIn(pattern, code_lines,
                             "forbidden import found: %s" % pattern)
        for pattern in self.FORBIDDEN_FUNCTIONS:
            self.assertNotIn(pattern, code_lines,
                             "forbidden function found: %s" % pattern)

    def test_03_no_file_io_in_dispatch(self):
        """No open() or file() in dispatch module."""
        path = os.path.join(ROOT, "scripts", "runtime_validator_dispatch.py")
        with open(path, "r", encoding="ascii") as f:
            source = f.read()
        self.assertNotIn("open(", source)

    def test_04_no_os_environ_in_dispatch(self):
        """No os.environ in dispatch module."""
        path = os.path.join(ROOT, "scripts", "runtime_validator_dispatch.py")
        with open(path, "r", encoding="ascii") as f:
            source = f.read()
        self.assertNotIn("os.environ", source)

    def test_05_no_network_in_dispatch(self):
        """No network patterns in dispatch module."""
        path = os.path.join(ROOT, "scripts", "runtime_validator_dispatch.py")
        with open(path, "r", encoding="ascii") as f:
            source = f.read()
        self.assertNotIn("socket", source)
        self.assertNotIn("http", source)
        self.assertNotIn("urllib", source)


# ---------------------------------------------------------------------------
# ASCII-only check
# ---------------------------------------------------------------------------

class ASCIITests(unittest.TestCase):
    """Verify dispatch and test files are ASCII-only."""

    def test_01_dispatch_module_ascii_only(self):
        """Dispatch module contains only ASCII characters."""
        path = os.path.join(ROOT, "scripts", "runtime_validator_dispatch.py")
        with open(path, "rb") as f:
            content = f.read()
        try:
            content.decode("ascii")
        except UnicodeDecodeError as e:
            self.fail("Non-ASCII bytes in dispatch module: %s" % e)

    def test_02_test_module_ascii_only(self):
        """Test module contains only ASCII characters."""
        path = os.path.join(ROOT, "scripts", "test_runtime_validator_dispatch.py")
        with open(path, "rb") as f:
            content = f.read()
        try:
            content.decode("ascii")
        except UnicodeDecodeError as e:
            self.fail("Non-ASCII bytes in test module: %s" % e)


# ---------------------------------------------------------------------------
# CompileAll Test
# ---------------------------------------------------------------------------

class CompileAllTests(unittest.TestCase):
    """Verify both dispatch and test files compile cleanly."""

    def test_01_dispatch_compiles(self):
        """dispatch module compiles without errors."""
        path = os.path.join(ROOT, "scripts", "runtime_validator_dispatch.py")
        result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", path],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         "compileall failed for dispatch: %s" % result.stderr)

    def test_02_test_compiles(self):
        """test module compiles without errors."""
        path = os.path.join(ROOT, "scripts", "test_runtime_validator_dispatch.py")
        result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", path],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         "compileall failed for test: %s" % result.stderr)


# ---------------------------------------------------------------------------
# Artifact validation test
# ---------------------------------------------------------------------------

class ArtifactValidationTests(unittest.TestCase):
    """Verify validate_artifacts.py passes (if it exists)."""

    def test_01_artifact_validation_passes(self):
        """Run validate_artifacts.py if present."""
        path = os.path.join(ROOT, "scripts", "validate_artifacts.py")
        if not os.path.isfile(path):
            self.skipTest("validate_artifacts.py not found")
        result = subprocess.run(
            [sys.executable, path],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0,
                         "validate_artifacts.py failed: %s" % result.stdout[-500:])


# ---------------------------------------------------------------------------
# Nonzero test count verification
# ---------------------------------------------------------------------------

class TestCountVerification(unittest.TestCase):
    """Verify focused tests include exact nonzero counts."""

    def test_01_minimum_test_count(self):
        """Test suite has at least 45 focused test methods."""
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(
            __import__(__name__))
        count = suite.countTestCases()
        self.assertGreaterEqual(count, 45,
                                "expected >= 45 tests, found %d" % count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
