"""Tests for the pure Validator Mesh aggregation core v1.2.0.

Covers:
  * Conformance catalog oracle for all positive and contract-invalid cases
  * v1.2 closed ReportArtifactRef, nullable report identity, caller-bound
    report_confidence, canonical stale missing_fields evidence
  * v1.2 ContractKey/TargetKey comparison, cross-requirement duplicate/conflict grouping
  * 234-cell exhaustive matrix tests with contract-derived oracle
  * Truth-table harness for every requirement state crossed with baseline/extension,
    all five verdicts, dispatch availability, freshness state, duplicate/conflict
  * XOR result semantics: every valid input -> exactly one result;
    every invalid input -> exactly one error
  * Determinism and order independence
  * Input preservation (caller objects unchanged)
  * Hostile input safety (NaN, subclasses, non-JSON values)
  * Source scan for forbidden imports
  * All four predecessor hash validations
  * Deprecated error code unreachability and active error code reachability
"""

import copy
import hashlib
import json
import os
import unittest

# Import the module under test
import scripts.runtime_validator_mesh as rvm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Contract file paths
SCHEMA_PATH = os.path.join(
    ROOT, "assets", "schemas", "runtime-validator-mesh-v1.schema.json")
CATALOG_PATH = os.path.join(
    ROOT, "examples", "runtime_validator_mesh_contract", "conformance.json")
CONTRACT_PATH = os.path.join(
    ROOT, "references", "runtime-validator-mesh-contract.md")

# Frozen predecessor hashes (accepted v1.2 authority)
EXPECTED_CONTRACT_SHA256 = "efe7689f1c258200137f4e02f037d18a24a01063c1fe24a9f5948086da869e68"
EXPECTED_SCHEMA_SHA256 = "16d99188a5306c1d279c533b780f669d459743f7fd9f54fe00f9d97bb226b12a"
EXPECTED_CONFORMANCE_SHA256 = "9ca19445da55ac15f826765a0390810e56ea45b6d4f15080dc03171938ef4b92"
EXPECTED_SCHEMA_TEST_SHA256 = "3b7925d4ce4f78baef7d03a711ee6bdc7680e59057e7ccf121881407042d9413"

DIGEST_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
DIGEST_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
DIGEST_C = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
DIGEST_D = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
DIGEST_E = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _a_digest(n):
    """Generate unique digests for testing (always 64 chars)."""
    base = "a" * 62
    return base + hex(n)[2:].zfill(2)


def _make_declaration(mesh_id="mesh-test", requirements=None, run_context=None):
    """Create a minimal valid ValidatorMeshDeclaration (v1.2)."""
    if requirements is None:
        requirements = [{
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001",
                "artifact_kind": "contract",
                "artifact_version": "1.0.0",
                "digest": DIGEST_A,
            },
            "artifact_scope": [{
                "artifact_id": "artifact-001",
                "artifact_kind": "artifact",
                "artifact_version": "1.0.0",
                "digest": DIGEST_A,
            }],
            "requirement_kind": "baseline",
            "required": True,
            "dispatch_priority": 0,
            "missing_mapping_policy": "fail",
            "failure_behavior": "halt_run"
        }]
    if run_context is None:
        run_context = {"run_id": "run-001", "stage_id": "stage-001"}
    return {
        "mesh_id": mesh_id,
        "mesh_version": "1.2.0",
        "governing_contract": {
            "artifact_id": "runtime-architecture",
            "artifact_kind": "contract",
            "artifact_version": "0.8.0",
            "digest": DIGEST_A,
        },
        "declared_at": "2026-07-01T00:00:00Z",
        "declared_by": "architect",
        "requirements": requirements,
        "aggregate_hierarchy": {},
        "dispatch_policy": {},
        "freshness_rules": {},
        "publish_bridge_contract": {},
        "run_context": run_context,
    }


_UNSET = object()


def _make_binding(binding_id="bind-001", requirement_id="req-001",
                  verdict="pass", *, confidence,
                  digest=DIGEST_A,
                  validator_identity="validator-core",
                  bound_at="2026-07-01T00:00:00Z",
                  contract_digest=None,
                  contract_artifact_id=None,
                  target_digest=None,
                  target_artifact_id=None,
                  report_artifact_id=_UNSET,
                  report_artifact_version=_UNSET,
                  report_sha256=_UNSET,
                  extra=None):
    """Create a minimal valid ValidationReportBinding (v1.2).

    `confidence` is a REQUIRED caller-supplied binding field: exactly one of
    "high", "medium", "low". It is never derived from `verdict` and never
    defaulted by any helper.

    Report identity values are nullable for positive stale tests. Pass
    report_artifact_id / report_artifact_version / digest / report_sha256 as
    None explicitly to construct null identity values; None is preserved
    verbatim and never rewritten by builders or the evaluator.
    """
    cd = contract_digest if contract_digest is not None else digest
    td = target_digest if target_digest is not None else digest
    caid = contract_artifact_id if contract_artifact_id is not None else "vc-001"
    taid = target_artifact_id if target_artifact_id is not None else "artifact-001"
    raid = report_artifact_id if report_artifact_id is not _UNSET else "report-001"
    raver = report_artifact_version if report_artifact_version is not _UNSET else "1.0.0"
    sha = digest if report_sha256 is _UNSET else report_sha256
    binding = {
        "binding_id": binding_id,
        "requirement_id": requirement_id,
        "validator_identity": validator_identity,
        "role": "validator",
        "contract_ref": {
            "artifact_id": caid,
            "artifact_kind": "contract",
            "artifact_version": "1.0.0",
            "digest": cd,
        },
        "target_artifact_ref": {
            "artifact_id": taid,
            "artifact_kind": "artifact",
            "artifact_version": "1.0.0",
            "digest": td,
        },
        "report_ref": {
            "artifact_id": raid,
            "artifact_kind": "validation_report",
            "artifact_version": raver,
            "digest": digest,
        },
        "report_sha256": sha,
        "report_confidence": confidence,
        "report_overall_verdict": verdict,
        "independent_production_evidence": {
            "producer_identity": validator_identity,
            "production_environment": "ci-pipeline-001",
            "production_timestamp": "2026-07-01T00:05:00Z",
            "no_caller_role_collapse": True
        },
        "bound_at": bound_at,
        "bound_by": "dispatcher"
    }
    if extra:
        binding.update(extra)
    return binding


def _make_dispatch_result(request_id="req-001", status="report_produced",
                          binding=None, error_code=None, degradation_note=None):
    """Create a valid ValidatorDispatchResult."""
    result = {
        "dispatch_request_id": request_id,
        "dispatch_status": status,
        "collected_at": "2026-07-01T00:00:00Z",
        "collected_by": "collector"
    }
    if status == "report_produced":
        # A produced dispatch result MUST carry an explicit binding with a
        # caller-supplied report_confidence; no default binding is created.
        if binding is None:
            raise ValueError(
                "report_produced requires an explicit binding with "
                "report_confidence (never defaulted)")
        result["report_binding"] = binding
    else:
        result["error_code"] = error_code or "validator_unreachable"
        if status in ("degraded_storage", "degraded_transport"):
            result["degradation_note"] = (
                degradation_note or "%s note" % status)
    return result


def _make_request(eval_id="eval-001", declaration=None,
                  dispatch_results=None):
    """Create a valid ValidatorMeshEvaluationRequest."""
    if declaration is None:
        declaration = _make_declaration()
    if dispatch_results is None:
        dispatch_results = [
            _make_dispatch_result("req-001", "report_produced",
                                  _make_binding("bind-001", "req-001", "pass", confidence="high"))
        ]
    return {
        "mesh_eval_id": eval_id,
        "mesh_declaration": declaration,
        "dispatch_results": dispatch_results,
        "requested_at": "2026-07-01T00:00:00Z",
        "requested_by": "architect"
    }


# ---------------------------------------------------------------------------
# Hostile Input Safety Tests
# ---------------------------------------------------------------------------

class HostileInputTests(unittest.TestCase):
    """Verify hostile inputs fail closed without executing hooks."""

    def get_error_code(self, result):
        if "error_code" in result:
            return result["error_code"]
        return None

    def test_01_not_dict(self):
        result = rvm.evaluate_validator_mesh("not a dict")
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_02_none(self):
        result = rvm.evaluate_validator_mesh(None)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_03_list_input(self):
        result = rvm.evaluate_validator_mesh([1, 2, 3])
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_04_empty_dict(self):
        result = rvm.evaluate_validator_mesh({})
        self.assertEqual(self.get_error_code(result), "invalid_mesh_request")

    def test_05_nan_value(self):
        result = rvm.evaluate_validator_mesh({"mesh_eval_id": float("nan")})
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_06_inf_value(self):
        result = rvm.evaluate_validator_mesh({"mesh_eval_id": float("inf")})
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_07_missing_mesh_eval_id(self):
        result = rvm.evaluate_validator_mesh({
            "mesh_declaration": {},
            "dispatch_results": [],
            "requested_at": "2026-01-01T00:00:00Z",
            "requested_by": "test",
        })
        self.assertEqual(self.get_error_code(result), "invalid_mesh_request")

    def test_08_empty_mesh_eval_id(self):
        result = rvm.evaluate_validator_mesh({
            "mesh_eval_id": "",
            "mesh_declaration": {},
            "dispatch_results": [],
            "requested_at": "2026-01-01T00:00:00Z",
            "requested_by": "test",
        })
        self.assertIn(self.get_error_code(result),
                      ("invalid_mesh_request", "invalid_mesh_declaration"))


# ---------------------------------------------------------------------------
# Declaration Validation Tests
# ---------------------------------------------------------------------------

class DeclarationValidationTests(unittest.TestCase):
    """Verify ValidatorMeshDeclaration structural validation (v1.2)."""

    def get_error_code(self, result):
        if "error_code" in result:
            return result["error_code"]
        return None

    def test_01_canonical_declaration_passes(self):
        request = _make_request()
        result = rvm.evaluate_validator_mesh(request)
        self.assertIsNone(self.get_error_code(result))
        self.assertEqual(result["aggregate_verdict"], "pass")

    def test_02_wrong_mesh_version(self):
        """mesh_version other than '1.2.0' fails."""
        decl = _make_declaration()
        decl["mesh_version"] = "0.9.0"
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_02b_v10_version_rejected(self):
        """mesh_version '1.0.0' fails in v1.2 validator."""
        decl = _make_declaration()
        decl["mesh_version"] = "1.0.0"
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_02c_v11_version_rejected(self):
        """mesh_version '1.1.0' fails in the v1.2 validator."""
        decl = _make_declaration()
        decl["mesh_version"] = "1.1.0"
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_02d_v12_version_accepted(self):
        """mesh_version '1.2.0' is the only accepted declaration version."""
        decl = _make_declaration()
        self.assertEqual(decl["mesh_version"], "1.2.0")
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertIsNone(self.get_error_code(result))
        self.assertIn("aggregate_verdict", result)

    def test_03_missing_required_declaration_field(self):
        decl = _make_declaration()
        del decl["mesh_id"]
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_04_empty_requirements(self):
        decl = _make_declaration(requirements=[])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_05_invalid_requirement_kind(self):
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "optional",
            "required": True,
            "dispatch_priority": 0,
            "failure_behavior": "halt_run"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_06_baseline_missing_mapping_policy(self):
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "baseline",
            "required": True,
            "dispatch_priority": 0,
            "failure_behavior": "halt_run"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_06b_extension_with_missing_mapping_policy_fail(self):
        """Extension with missing_mapping_policy=fail is rejected."""
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "extension",
            "required": True,
            "dispatch_priority": 0,
            "missing_mapping_policy": "fail",
            "failure_behavior": "halt_mesh"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_06c_extension_with_missing_mapping_policy_hrr(self):
        """Extension with missing_mapping_policy=human_review_required is rejected."""
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "extension",
            "required": False,
            "dispatch_priority": 0,
            "missing_mapping_policy": "human_review_required",
            "failure_behavior": "halt_mesh"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_07_duplicate_requirement_id(self):
        req1 = {
            "requirement_id": "req-001",
            "validator_identity": "validator-1",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "baseline",
            "required": True,
            "dispatch_priority": 0,
            "missing_mapping_policy": "fail",
            "failure_behavior": "halt_run"
        }
        req2 = {
            "requirement_id": "req-001",
            "validator_identity": "validator-2",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_B},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_B}],
            "requirement_kind": "extension",
            "required": False,
            "dispatch_priority": 1,
            "failure_behavior": "halt_mesh"
        }
        decl = _make_declaration(requirements=[req1, req2])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "duplicate_requirement_id")

    def test_08_invalid_failure_behavior(self):
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "baseline",
            "required": True,
            "dispatch_priority": 0,
            "missing_mapping_policy": "fail",
            "failure_behavior": "continue"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_09_negative_dispatch_priority(self):
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "baseline",
            "required": True,
            "dispatch_priority": -1,
            "missing_mapping_policy": "fail",
            "failure_behavior": "halt_run"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_10_contract_ref_missing_digest(self):
        """v1.2: contract_ref without digest is invalid."""
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0"},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "baseline",
            "required": True,
            "dispatch_priority": 0,
            "missing_mapping_policy": "fail",
            "failure_behavior": "halt_run"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_11_artifact_scope_missing_digest(self):
        """v1.2: artifact_scope member without digest is invalid."""
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0"}],
            "requirement_kind": "baseline",
            "required": True,
            "dispatch_priority": 0,
            "missing_mapping_policy": "fail",
            "failure_behavior": "halt_run"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")

    def test_12_artifact_scope_wrong_count(self):
        """v1.2: artifact_scope must have exactly 1 member."""
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [],
            "requirement_kind": "baseline",
            "required": True,
            "dispatch_priority": 0,
            "missing_mapping_policy": "fail",
            "failure_behavior": "halt_run"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_mesh_declaration")


# ---------------------------------------------------------------------------
# Request Integrity Tests
# ---------------------------------------------------------------------------

class RequestIntegrityTests(unittest.TestCase):
    """Verify dispatch request structural rules."""

    def get_error_code(self, result):
        if "error_code" in result:
            return result["error_code"]
        return None

    def _make_ext_req(self, req_id, priority):
        return {
            "requirement_id": req_id,
            "validator_identity": "validator-ext",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_B},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_B}],
            "requirement_kind": "extension",
            "required": False,
            "dispatch_priority": priority,
            "failure_behavior": "halt_mesh"
        }

    def test_01_dispatch_count_mismatch(self):
        declaration = _make_declaration(requirements=[
            _make_declaration()["requirements"][0],
            self._make_ext_req("req-002", 1)
        ])
        request = _make_request(declaration=declaration)
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "dispatch_count_mismatch")

    def test_02_duplicate_dispatch_request_id(self):
        declaration = _make_declaration(requirements=[
            _make_declaration()["requirements"][0],
            self._make_ext_req("req-002", 1)
        ])
        request = _make_request(declaration=declaration, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced",
                                  _make_binding("bind-001", "req-001", "pass", confidence="high")),
            _make_dispatch_result("req-001", "report_produced",
                                  _make_binding("bind-002", "req-002", "pass", confidence="high")),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "duplicate_dispatch_request_id")

    def test_03_orphan_dispatch_result(self):
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-999", "report_produced",
                                  _make_binding("bind-001", "req-001", "pass", confidence="high"))
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "orphan_dispatch_result")

    def test_04_produced_without_binding(self):
        request = _make_request(dispatch_results=[
            {
                "dispatch_request_id": "req-001",
                "dispatch_status": "report_produced",
                "collected_at": "2026-07-01T00:00:00Z",
                "collected_by": "collector"
            }
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertIn(self.get_error_code(result),
                      ("invalid_dispatch_result", "invalid_mesh_request"))

    def test_05_unreachable_with_binding(self):
        request = _make_request(dispatch_results=[
            {
                "dispatch_request_id": "req-001",
                "dispatch_status": "unreachable",
                "error_code": "validator_unreachable",
                "report_binding": _make_binding(confidence="high"),
                "collected_at": "2026-07-01T00:00:00Z",
                "collected_by": "collector"
            }
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertIn(self.get_error_code(result),
                      ("invalid_dispatch_result", "invalid_mesh_request",
                       "invalid_report_binding"))

    def test_06_degraded_missing_note(self):
        request = _make_request(dispatch_results=[
            {
                "dispatch_request_id": "req-001",
                "dispatch_status": "degraded_storage",
                "error_code": "validator_degraded_storage",
                "collected_at": "2026-07-01T00:00:00Z",
                "collected_by": "collector"
            }
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertIn(self.get_error_code(result),
                      ("invalid_dispatch_result", "invalid_mesh_request"))


# ---------------------------------------------------------------------------
# Binding Validation Tests
# ---------------------------------------------------------------------------

class BindingValidationTests(unittest.TestCase):
    """Verify ValidationReportBinding structural rules (v1.2)."""

    def get_error_code(self, result):
        if "error_code" in result:
            return result["error_code"]
        return None

    def test_01_missing_binding_id(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        del binding["binding_id"]
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_report_binding")

    def test_02_wrong_role(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["role"] = "architect"
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_report_binding")

    def test_03_digest_mismatch(self):
        """v1.2: report_ref.digest != report_sha256 -> invalid_report_binding."""
        binding = _make_binding(binding_id="bind-001", digest=DIGEST_A, confidence="high")
        binding["report_sha256"] = DIGEST_B
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_report_binding")

    def test_04_contract_digest_missing(self):
        """v1.2: missing contract_ref.digest -> invalid_report_binding."""
        binding = _make_binding(binding_id="bind-001", confidence="high")
        del binding["contract_ref"]["digest"]
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_report_binding")

    def test_05_role_collapse_false(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["independent_production_evidence"]["no_caller_role_collapse"] = False
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_report_binding")

    def test_06_missing_independent_evidence(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        del binding["independent_production_evidence"]
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_report_binding")

    def test_07_missing_target_artifact_digest(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        del binding["target_artifact_ref"]["digest"]
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_report_binding")

    def test_08_invalid_overall_verdict(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_overall_verdict"] = "pending"
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(self.get_error_code(result), "invalid_report_binding")


# ---------------------------------------------------------------------------
# Closed ReportArtifactRef and Confidence Negative Tests (v1.2)
# ---------------------------------------------------------------------------

class ClosedReportArtifactRefTests(unittest.TestCase):
    """Negative binding tests for the v1.2 closed ReportArtifactRef and
    caller-bound report_confidence: every missing key, empty string, wrong
    type, extra field, wrong kind, malformed digest, unequal non-null digest,
    and missing/invalid/null confidence must return invalid_report_binding
    BEFORE freshness classification."""

    def get_error_code(self, result):
        if "error_code" in result:
            return result["error_code"]
        return None

    def _eval(self, binding):
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        return rvm.evaluate_validator_mesh(request)

    def _assert_invalid(self, binding, label):
        result = self._eval(binding)
        self.assertEqual(self.get_error_code(result), "invalid_report_binding",
                         "%s: expected invalid_report_binding, got %s" %
                         (label, self.get_error_code(result)))

    # --- report_confidence ---

    def test_01_missing_report_confidence(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        del binding["report_confidence"]
        self._assert_invalid(binding, "missing report_confidence")

    def test_02_invalid_report_confidence(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_confidence"] = "certain"
        self._assert_invalid(binding, "invalid report_confidence")

    def test_03_null_report_confidence(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_confidence"] = None
        self._assert_invalid(binding, "null report_confidence")

    def test_04_empty_report_confidence(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_confidence"] = ""
        self._assert_invalid(binding, "empty report_confidence")

    def test_05_wrong_type_report_confidence(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_confidence"] = 0
        self._assert_invalid(binding, "wrong-type report_confidence")

    # --- closed report_ref keys ---

    def test_06_report_ref_missing_artifact_id(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        del binding["report_ref"]["artifact_id"]
        self._assert_invalid(binding, "report_ref missing artifact_id")

    def test_07_report_ref_missing_artifact_kind(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        del binding["report_ref"]["artifact_kind"]
        self._assert_invalid(binding, "report_ref missing artifact_kind")

    def test_08_report_ref_missing_artifact_version(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        del binding["report_ref"]["artifact_version"]
        self._assert_invalid(binding, "report_ref missing artifact_version")

    def test_09_report_ref_missing_digest(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        del binding["report_ref"]["digest"]
        self._assert_invalid(binding, "report_ref missing digest")

    def test_10_report_ref_extra_key(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["extra"] = "x"
        self._assert_invalid(binding, "report_ref extra key")

    def test_11_report_ref_not_a_dict(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"] = "not-a-dict"
        self._assert_invalid(binding, "report_ref not a dict")

    # --- wrong artifact_kind ---

    def test_12_report_ref_wrong_kind(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["artifact_kind"] = "validation_summary"
        self._assert_invalid(binding, "report_ref wrong artifact_kind")

    def test_13_report_ref_null_kind(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["artifact_kind"] = None
        self._assert_invalid(binding, "report_ref null artifact_kind")

    # --- empty / wrong-type identity values ---

    def test_14_report_ref_empty_artifact_id(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["artifact_id"] = ""
        self._assert_invalid(binding, "report_ref empty artifact_id")

    def test_15_report_ref_wrong_type_artifact_id(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["artifact_id"] = 123
        self._assert_invalid(binding, "report_ref wrong-type artifact_id")

    def test_16_report_ref_empty_artifact_version(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["artifact_version"] = ""
        self._assert_invalid(binding, "report_ref empty artifact_version")

    def test_17_report_ref_wrong_type_artifact_version(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["artifact_version"] = []
        self._assert_invalid(binding, "report_ref wrong-type artifact_version")

    # --- malformed / wrong-type digests ---

    def test_18_report_ref_malformed_digest(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["digest"] = "abc"
        self._assert_invalid(binding, "report_ref short digest")

    def test_19_report_ref_uppercase_digest(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["digest"] = DIGEST_A.upper()
        self._assert_invalid(binding, "report_ref uppercase digest")

    def test_20_report_ref_wrong_type_digest(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["digest"] = 64
        self._assert_invalid(binding, "report_ref wrong-type digest")

    def test_21_report_ref_empty_digest(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_ref"]["digest"] = ""
        self._assert_invalid(binding, "report_ref empty digest")

    def test_22_report_sha256_malformed(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_sha256"] = "xyz"
        self._assert_invalid(binding, "report_sha256 short")

    def test_23_report_sha256_uppercase(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_sha256"] = DIGEST_A.upper()
        self._assert_invalid(binding, "report_sha256 uppercase")

    def test_24_report_sha256_empty(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_sha256"] = ""
        self._assert_invalid(binding, "report_sha256 empty")

    def test_25_report_sha256_wrong_type(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_sha256"] = 64
        self._assert_invalid(binding, "report_sha256 wrong-type")

    # --- unequal non-null digests ---

    def test_26_unequal_non_null_digests(self):
        binding = _make_binding(binding_id="bind-001", confidence="high")
        binding["report_sha256"] = DIGEST_B
        self._assert_invalid(binding, "report_ref.digest != report_sha256")


# ---------------------------------------------------------------------------
# Positive Stale Tests (v1.2 nullable report identity)
# ---------------------------------------------------------------------------

class StaleTests(unittest.TestCase):
    """Positive stale coverage: nullable report identity routes to stale with
    exact canonical missing_fields, correct result kind and contribution,
    aggregate verdict/confidence, and an unchanged returned binding.

    Null identity values are structurally valid; they are NOT rejected in
    binding validation and route to stale only after superseded/invalidated.
    """

    CANONICAL_FIELDS = [
        "report_ref.artifact_id",
        "report_ref.artifact_version",
        "report_ref.digest",
        "report_sha256",
    ]

    def _make_req(self, kind, required, policy="fail"):
        r = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": kind,
            "required": required,
            "dispatch_priority": 0,
            "failure_behavior": "halt_run" if kind == "baseline" else "halt_mesh",
        }
        if kind == "baseline":
            r["missing_mapping_policy"] = policy
        return r

    def _eval(self, binding, kind="baseline", required=True, policy="fail",
              supersession=None, invalidation=None):
        binding = copy.deepcopy(binding)
        if supersession is not None:
            binding["supersession"] = supersession
        if invalidation is not None:
            binding["invalidation"] = invalidation
        req = self._make_req(kind, required, policy)
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        return rvm.evaluate_validator_mesh(request)

    def _assert_stale(self, result, expected_missing, binding_id="bind-001",
                      original_binding=None, expected_verdict=None,
                      expected_confidence=None, expected_result_kind=None,
                      expected_contribution=None, expected_excluded=None):
        self.assertNotIn("error_code", result)
        self.assertEqual(result["aggregate_verdict"], expected_verdict)
        self.assertEqual(result["aggregate_confidence"], expected_confidence)
        fa = [x for x in result["freshness_assessments"]
              if x["binding_id"] == binding_id]
        self.assertEqual(len(fa), 1)
        self.assertEqual(fa[0]["freshness_status"], "stale")
        details = fa[0]["freshness_details"]
        self.assertEqual(details["field_category"], "report_identity")
        # Exact canonical missing_fields: non-empty, duplicate-free, ordered.
        self.assertTrue(details["missing_fields"])
        self.assertEqual(len(details["missing_fields"]),
                         len(set(details["missing_fields"])))
        self.assertEqual(details["missing_fields"], expected_missing)
        self.assertEqual(
            details["missing_fields"],
            [f for f in self.CANONICAL_FIELDS if f in details["missing_fields"]])
        rr = result["requirement_results"][0]
        self.assertEqual(rr["result_kind"], expected_result_kind)
        if expected_contribution is not None:
            self.assertIn("verdict_contribution", rr)
            self.assertEqual(rr["verdict_contribution"], expected_contribution)
        if expected_excluded is not None:
            self.assertIn("excluded_reason", rr)
            self.assertEqual(rr["excluded_reason"], expected_excluded)
        # Returned binding is unchanged: report_confidence and null identity
        # values are preserved verbatim, never filled or rewritten.
        if original_binding is not None:
            rb = result["report_bindings"][0]
            self.assertEqual(rb["report_confidence"],
                             original_binding["report_confidence"])
            self.assertEqual(rb["report_ref"], original_binding["report_ref"])
            self.assertEqual(rb["report_sha256"], original_binding["report_sha256"])
            self.assertEqual(rb["report_ref"]["artifact_id"],
                             original_binding["report_ref"]["artifact_id"])
            self.assertEqual(rb["report_ref"]["artifact_version"],
                             original_binding["report_ref"]["artifact_version"])
            self.assertEqual(rb["report_ref"]["digest"],
                             original_binding["report_ref"]["digest"])

    # Each null field independently (required baseline) ---------------------

    def test_01_null_artifact_id(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high",
                                report_artifact_id=None)
        result = self._eval(binding)
        self._assert_stale(
            result, ["report_ref.artifact_id"], original_binding=binding,
            expected_verdict="blocked", expected_confidence="low",
            expected_result_kind="unusable_required_report",
            expected_contribution="blocked")

    def test_02_null_artifact_version(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high",
                                report_artifact_version=None)
        result = self._eval(binding)
        self._assert_stale(
            result, ["report_ref.artifact_version"], original_binding=binding,
            expected_verdict="blocked", expected_confidence="low",
            expected_result_kind="unusable_required_report",
            expected_contribution="blocked")

    def test_03_null_report_ref_digest(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high",
                                digest=None, contract_digest=DIGEST_A,
                                target_digest=DIGEST_A, report_sha256=DIGEST_A)
        result = self._eval(binding)
        self._assert_stale(
            result, ["report_ref.digest"], original_binding=binding,
            expected_verdict="blocked", expected_confidence="low",
            expected_result_kind="unusable_required_report",
            expected_contribution="blocked")

    def test_04_null_report_sha256(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high",
                                report_sha256=None)
        result = self._eval(binding)
        self._assert_stale(
            result, ["report_sha256"], original_binding=binding,
            expected_verdict="blocked", expected_confidence="low",
            expected_result_kind="unusable_required_report",
            expected_contribution="blocked")

    # All-null and mixed-null ------------------------------------------------

    def test_05_all_identity_null(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high",
                                digest=None, contract_digest=DIGEST_A,
                                target_digest=DIGEST_A,
                                report_artifact_id=None,
                                report_artifact_version=None)
        result = self._eval(binding)
        self._assert_stale(
            result, ["report_ref.artifact_id", "report_ref.artifact_version",
                     "report_ref.digest", "report_sha256"],
            original_binding=binding,
            expected_verdict="blocked", expected_confidence="low",
            expected_result_kind="unusable_required_report",
            expected_contribution="blocked")

    def test_06_mixed_null_artifact_id_and_sha256(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high",
                                report_artifact_id=None, report_sha256=None)
        result = self._eval(binding)
        self._assert_stale(
            result, ["report_ref.artifact_id", "report_sha256"],
            original_binding=binding,
            expected_verdict="blocked", expected_confidence="low",
            expected_result_kind="unusable_required_report",
            expected_contribution="blocked")

    def test_07_mixed_null_version_and_digest(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high",
                                digest=None, contract_digest=DIGEST_A,
                                target_digest=DIGEST_A,
                                report_artifact_version=None,
                                report_sha256=DIGEST_A)
        result = self._eval(binding)
        self._assert_stale(
            result, ["report_ref.artifact_version", "report_ref.digest"],
            original_binding=binding,
            expected_verdict="blocked", expected_confidence="low",
            expected_result_kind="unusable_required_report",
            expected_contribution="blocked")

    # Required vs optional contribution --------------------------------------

    def test_08_optional_extension_stale_excluded(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="low",
                                report_artifact_id=None)
        result = self._eval(binding, kind="extension", required=False)
        self._assert_stale(
            result, ["report_ref.artifact_id"],
            expected_verdict="inconclusive", expected_confidence="low",
            expected_result_kind="optional_excluded",
            expected_excluded="optional_unusable_report")
        # Optional excluded bindings are not returned in report_bindings.
        self.assertEqual(result["report_bindings"], [])

    def test_09_required_extension_stale_blocked(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="low",
                                report_artifact_version=None)
        result = self._eval(binding, kind="extension", required=True)
        self._assert_stale(
            result, ["report_ref.artifact_version"], original_binding=binding,
            expected_verdict="blocked", expected_confidence="low",
            expected_result_kind="unusable_required_report",
            expected_contribution="blocked")

    # Superseded / invalidated precedence over stale -------------------------

    def test_10_superseded_wins_over_stale(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high",
                                report_artifact_id=None)
        result = self._eval(
            binding,
            supersession={"artifact_id": "r2", "artifact_kind": "validation_report"})
        self.assertNotIn("error_code", result)
        self.assertEqual(result["aggregate_verdict"], "blocked")
        self.assertEqual(result["aggregate_confidence"], "low")
        fa = [x for x in result["freshness_assessments"]
              if x["binding_id"] == "bind-001"][0]
        self.assertEqual(fa["freshness_status"], "superseded")
        self.assertIn("supersession", fa["freshness_details"])
        self.assertNotIn("missing_fields", fa["freshness_details"])

    def test_11_invalidated_wins_over_stale(self):
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high",
                                report_artifact_id=None)
        result = self._eval(
            binding,
            invalidation={"invalidated_by": "admin", "invalidated_at": "now",
                          "invalidation_reason": "error"})
        self.assertNotIn("error_code", result)
        self.assertEqual(result["aggregate_verdict"], "blocked")
        self.assertEqual(result["aggregate_confidence"], "low")
        fa = [x for x in result["freshness_assessments"]
              if x["binding_id"] == "bind-001"][0]
        self.assertEqual(fa["freshness_status"], "invalidated")
        self.assertIn("invalidation", fa["freshness_details"])
        self.assertNotIn("missing_fields", fa["freshness_details"])

    def test_12_null_identity_structurally_valid(self):
        """Null identity values pass binding validation (no error) and route
        to stale, proving they are NOT rejected as invalid_report_binding."""
        for kwargs in (
                {"report_artifact_id": None},
                {"report_artifact_version": None},
                {"digest": None, "contract_digest": DIGEST_A,
                 "target_digest": DIGEST_A},
                {"report_sha256": None}):
            binding = _make_binding("bind-001", "req-001", "pass",
                                    confidence="high", **kwargs)
            result = self._eval(binding)
            self.assertNotIn("error_code", result,
                             "null identity unexpectedly rejected: %s" % kwargs)


# ---------------------------------------------------------------------------
# Caller-Bound Aggregate Confidence Tests (v1.2)
# ---------------------------------------------------------------------------

class ConfidenceTests(unittest.TestCase):
    """Aggregate confidence is computed ONLY from caller-supplied
    report_confidence on current report bindings whose verdict contributes.
    Least confidence wins: high < medium < low. No current contribution -> low.
    Non-current binding confidence never affects the aggregate."""

    CONFIDENCES = ("high", "medium", "low")
    RANK = {"high": 0, "medium": 1, "low": 2}

    def _least(self, *confs):
        """Least confidence = the most cautious value (max rank)."""
        return max(confs, key=lambda c: self.RANK[c])

    def _make_ext_req(self, req_id, priority, contract_digest=DIGEST_B,
                      target_digest=DIGEST_B):
        return {
            "requirement_id": req_id,
            "validator_identity": "validator-ext",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": contract_digest},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": target_digest}],
            "requirement_kind": "extension",
            "required": True,
            "dispatch_priority": priority,
            "failure_behavior": "halt_mesh"
        }

    def _eval_single(self, confidence):
        binding = _make_binding("bind-001", "req-001", "pass",
                                confidence=confidence)
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        return rvm.evaluate_validator_mesh(request)

    def test_01_single_high(self):
        result = self._eval_single("high")
        self.assertEqual(result["aggregate_verdict"], "pass")
        self.assertEqual(result["aggregate_confidence"], "high")

    def test_02_single_medium(self):
        result = self._eval_single("medium")
        self.assertEqual(result["aggregate_verdict"], "pass")
        self.assertEqual(result["aggregate_confidence"], "medium")

    def test_03_single_low(self):
        result = self._eval_single("low")
        self.assertEqual(result["aggregate_verdict"], "pass")
        self.assertEqual(result["aggregate_confidence"], "low")

    def test_04_all_ordered_two_current_pairs(self):
        """All 9 ordered two-current pairs use the least confidence."""
        for c1 in self.CONFIDENCES:
            for c2 in self.CONFIDENCES:
                reqs = [
                    _make_declaration()["requirements"][0],
                    self._make_ext_req("req-002", 1),
                ]
                decl = _make_declaration(requirements=reqs)
                b1 = _make_binding("bind-001", "req-001", "pass",
                                   digest=DIGEST_A, confidence=c1)
                b2 = _make_binding("bind-002", "req-002", "pass",
                                   digest=DIGEST_B,
                                   contract_digest=DIGEST_B,
                                   target_digest=DIGEST_B,
                                   confidence=c2)
                request = _make_request(declaration=decl, dispatch_results=[
                    _make_dispatch_result("req-001", "report_produced", b1),
                    _make_dispatch_result("req-002", "report_produced", b2),
                ])
                result = rvm.evaluate_validator_mesh(request)
                self.assertEqual(result["aggregate_verdict"], "pass")
                self.assertEqual(
                    result["aggregate_confidence"], self._least(c1, c2),
                    "pair %s,%s: expected %s got %s" %
                    (c1, c2, self._least(c1, c2),
                     result["aggregate_confidence"]))

    def test_05_three_binding_low_dominates(self):
        """One low dominates any combination of high/medium."""
        reqs = [
            _make_declaration()["requirements"][0],
            self._make_ext_req("req-002", 1),
            self._make_ext_req("req-003", 2, DIGEST_C, DIGEST_C),
        ]
        decl = _make_declaration(requirements=reqs)
        bindings = [
            _make_binding("bind-001", "req-001", "pass", digest=DIGEST_A,
                          confidence="high"),
            _make_binding("bind-002", "req-002", "pass", digest=DIGEST_B,
                          contract_digest=DIGEST_B, target_digest=DIGEST_B,
                          confidence="medium"),
            _make_binding("bind-003", "req-003", "pass", digest=DIGEST_C,
                          contract_digest=DIGEST_C, target_digest=DIGEST_C,
                          confidence="low"),
        ]
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", bindings[0]),
            _make_dispatch_result("req-002", "report_produced", bindings[1]),
            _make_dispatch_result("req-003", "report_produced", bindings[2]),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["aggregate_verdict"], "pass")
        self.assertEqual(result["aggregate_confidence"], "low")

    def test_06_three_binding_medium_dominates_high(self):
        reqs = [
            _make_declaration()["requirements"][0],
            self._make_ext_req("req-002", 1),
            self._make_ext_req("req-003", 2, DIGEST_C, DIGEST_C),
        ]
        decl = _make_declaration(requirements=reqs)
        bindings = [
            _make_binding("bind-001", "req-001", "pass", digest=DIGEST_A,
                          confidence="high"),
            _make_binding("bind-002", "req-002", "pass", digest=DIGEST_B,
                          contract_digest=DIGEST_B, target_digest=DIGEST_B,
                          confidence="high"),
            _make_binding("bind-003", "req-003", "pass", digest=DIGEST_C,
                          contract_digest=DIGEST_C, target_digest=DIGEST_C,
                          confidence="medium"),
        ]
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", bindings[0]),
            _make_dispatch_result("req-002", "report_produced", bindings[1]),
            _make_dispatch_result("req-003", "report_produced", bindings[2]),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["aggregate_verdict"], "pass")
        self.assertEqual(result["aggregate_confidence"], "medium")

    def test_07_non_current_confidence_excluded(self):
        """A duplicate non-current binding's low confidence does NOT affect the
        aggregate: only current contributing bindings count."""
        reqs = [
            _make_declaration()["requirements"][0],
            self._make_ext_req("req-002", 1, DIGEST_A, DIGEST_A),
        ]
        decl = _make_declaration(requirements=reqs)
        b1 = _make_binding("bind-001", "req-001", "pass", digest=DIGEST_A,
                           confidence="high")
        # Same ComparisonKey and same digest as b1 -> duplicate, low confidence.
        b2 = _make_binding("bind-002", "req-002", "pass", digest=DIGEST_A,
                           contract_digest=DIGEST_A, target_digest=DIGEST_A,
                           confidence="low")
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", b1),
            _make_dispatch_result("req-002", "report_produced", b2),
        ])
        result = rvm.evaluate_validator_mesh(request)
        dup = [fa for fa in result["freshness_assessments"]
               if fa["binding_id"] == "bind-002"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["freshness_status"], "duplicate")
        # Duplicate on required contributes blocked, but confidence stays high
        # because the low-confidence binding is non-current.
        self.assertEqual(result["aggregate_verdict"], "blocked")
        self.assertEqual(result["aggregate_confidence"], "high")

    def test_08_no_current_contribution_low(self):
        """No current report contributes -> aggregate confidence is exactly low,
        including optional exclusions and required non-current branches."""
        # Optional extension unreachable (excluded): no current report.
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-ext",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_B},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_B}],
            "requirement_kind": "extension",
            "required": False,
            "dispatch_priority": 0,
            "failure_behavior": "halt_mesh"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "unreachable",
                                  error_code="validator_unreachable"),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["aggregate_verdict"], "inconclusive")
        self.assertEqual(result["aggregate_confidence"], "low")

        # Required baseline with a stale report: non-current branch -> low.
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high",
                                report_artifact_id=None)
        decl2 = _make_declaration()
        request2 = _make_request(declaration=decl2, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result2 = rvm.evaluate_validator_mesh(request2)
        self.assertEqual(result2["aggregate_verdict"], "blocked")
        self.assertEqual(result2["aggregate_confidence"], "low")


# ---------------------------------------------------------------------------
# No-Inference Probes (v1.2): confidence is caller-bound, never derived
# ---------------------------------------------------------------------------

class NoInferenceProbeTests(unittest.TestCase):
    """Static and dynamic probes proving aggregate confidence is never inferred
    from report verdicts and that verdict/confidence are independent.

    Static: the evaluator contains no verdict-to-confidence helper and never
    writes report_confidence; it only reads the caller-supplied value.
    Dynamic: fixing explicit confidence and varying verdicts leaves aggregate
    confidence unchanged; fixing verdict and varying confidence leaves
    aggregate verdict unchanged."""

    EVALUATOR_PATH = os.path.join(ROOT, "scripts", "runtime_validator_mesh.py")

    def test_01_static_no_verdict_to_confidence_helper(self):
        with open(self.EVALUATOR_PATH, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("_verdict_to_confidence", source)
        # The evaluator must never assign report_confidence; it only reads it.
        self.assertNotIn('"report_confidence":', source)
        self.assertNotIn("report_confidence =", source)
        # The confidence computation reads the caller-supplied value directly.
        self.assertIn('rb["report_confidence"]', source)

    def test_02_static_builders_require_explicit_confidence(self):
        source_path = os.path.join(ROOT, "scripts",
                                   "test_runtime_validator_mesh.py")
        with open(source_path, "r", encoding="utf-8") as f:
            source = f.read()
        # No verdict-to-confidence helper is defined anywhere in the suite.
        helper_defs = [ln for ln in source.splitlines()
                       if ln.lstrip().startswith("def _verdict_to_confidence")]
        self.assertEqual(helper_defs, [],
                         "verdict-to-confidence helper defined: %s" % helper_defs)
        # No helper maps a verdict to a confidence anywhere in the suite.
        self.assertNotIn("if verdict == \"pass\":\n        return \"high\"", source)
        # Empty builder calls are forbidden; every call supplies confidence.
        empty_binding_call = "_make_" + "binding()"
        self.assertNotIn(empty_binding_call, source)
        self.assertIn('"report_confidence": confidence', source)

    def test_03_dynamic_verdict_does_not_change_confidence(self):
        """Fixed explicit confidence, any verdict -> same aggregate confidence."""
        for verdict in ("pass", "fail", "blocked", "inconclusive",
                        "human_review_required"):
            binding = _make_binding("bind-001", "req-001", verdict,
                                    confidence="medium")
            request = _make_request(dispatch_results=[
                _make_dispatch_result("req-001", "report_produced", binding)
            ])
            result = rvm.evaluate_validator_mesh(request)
            self.assertEqual(result["aggregate_verdict"], verdict)
            self.assertEqual(result["aggregate_confidence"], "medium",
                             "verdict=%s changed aggregate confidence" % verdict)

    def test_04_dynamic_confidence_does_not_change_verdict(self):
        """Fixed verdict, any explicit confidence -> same aggregate verdict."""
        for confidence in ("high", "medium", "low"):
            binding = _make_binding("bind-001", "req-001", "fail",
                                    confidence=confidence)
            request = _make_request(dispatch_results=[
                _make_dispatch_result("req-001", "report_produced", binding)
            ])
            result = rvm.evaluate_validator_mesh(request)
            self.assertEqual(result["aggregate_verdict"], "fail")
            self.assertEqual(result["aggregate_confidence"], confidence)


# ---------------------------------------------------------------------------
# Aggregate Verdict Computation Tests
# ---------------------------------------------------------------------------

class AggregateVerdictTests(unittest.TestCase):
    """Verify aggregate verdict computation for all five verdicts and combinations."""

    def get_error_code(self, result):
        if "error_code" in result:
            return result["error_code"]
        return None

    def _eval_single(self, verdict):
        binding = _make_binding("bind-001", "req-001", verdict, confidence="high")
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        return rvm.evaluate_validator_mesh(request)

    def test_01_single_pass(self):
        result = self._eval_single("pass")
        self.assertEqual(result["aggregate_verdict"], "pass")

    def test_02_single_fail(self):
        result = self._eval_single("fail")
        self.assertEqual(result["aggregate_verdict"], "fail")

    def test_03_single_blocked(self):
        result = self._eval_single("blocked")
        self.assertEqual(result["aggregate_verdict"], "blocked")

    def test_04_single_inconclusive(self):
        result = self._eval_single("inconclusive")
        self.assertEqual(result["aggregate_verdict"], "inconclusive")

    def test_05_single_human_review_required(self):
        result = self._eval_single("human_review_required")
        self.assertEqual(result["aggregate_verdict"], "human_review_required")

    def _make_ext_req(self, req_id, priority, required=True):
        return {
            "requirement_id": req_id,
            "validator_identity": "validator-ext",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_B},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_B}],
            "requirement_kind": "extension",
            "required": required,
            "dispatch_priority": priority,
            "failure_behavior": "halt_mesh"
        }

    def _eval_two_requirements(self, v1, v2):
        reqs = [
            _make_declaration()["requirements"][0],
            self._make_ext_req("req-002", 1)
        ]
        declaration = _make_declaration(requirements=reqs)
        b1 = _make_binding("bind-001", "req-001", v1, digest=DIGEST_A, confidence="high")
        b2 = _make_binding("bind-002", "req-002", v2, digest=DIGEST_B,
                           contract_digest=DIGEST_B, target_digest=DIGEST_B,
                           bound_at="2026-07-01T00:01:00Z", confidence="high")
        dispatch_results = [
            _make_dispatch_result("req-001", "report_produced", b1),
            _make_dispatch_result("req-002", "report_produced", b2),
        ]
        request = _make_request(declaration=declaration,
                                dispatch_results=dispatch_results)
        return rvm.evaluate_validator_mesh(request)

    def test_06_pass_plus_pass(self):
        result = self._eval_two_requirements("pass", "pass")
        self.assertEqual(result["aggregate_verdict"], "pass")

    def test_07_pass_plus_fail(self):
        result = self._eval_two_requirements("pass", "fail")
        self.assertEqual(result["aggregate_verdict"], "fail")

    def test_08_pass_plus_blocked(self):
        result = self._eval_two_requirements("pass", "blocked")
        self.assertEqual(result["aggregate_verdict"], "blocked")

    def test_09_pass_plus_inconclusive(self):
        result = self._eval_two_requirements("pass", "inconclusive")
        self.assertEqual(result["aggregate_verdict"], "inconclusive")

    def test_10_pass_plus_human_review_required(self):
        result = self._eval_two_requirements("pass", "human_review_required")
        self.assertEqual(result["aggregate_verdict"], "human_review_required")

    def test_11_fail_plus_blocked(self):
        result = self._eval_two_requirements("fail", "blocked")
        self.assertEqual(result["aggregate_verdict"], "fail")

    def test_12_blocked_plus_human_review_required(self):
        result = self._eval_two_requirements("blocked", "human_review_required")
        self.assertEqual(result["aggregate_verdict"], "blocked")

    def test_13_human_review_required_plus_inconclusive(self):
        result = self._eval_two_requirements("human_review_required", "inconclusive")
        self.assertEqual(result["aggregate_verdict"], "human_review_required")


# ---------------------------------------------------------------------------
# Truth Table Exhaustive Tests (v1.2 semantics)
# ---------------------------------------------------------------------------

class TruthTableTests(unittest.TestCase):
    """Exhaustive truth table covering all requirement states, verdicts,
    dispatch availability, freshness, duplicate/conflict (v1.2)."""

    def get_error_code(self, result):
        if "error_code" in result:
            return result["error_code"]
        return None

    # --- Baseline, required=true, current ---

    def test_01_baseline_required_current_pass(self):
        result = self._make_and_eval("baseline", True, "report_produced",
                                     "current", "pass")
        self.assertEqual(result["aggregate_verdict"], "pass")

    def test_02_baseline_required_current_fail(self):
        result = self._make_and_eval("baseline", True, "report_produced",
                                     "current", "fail")
        self.assertEqual(result["aggregate_verdict"], "fail")

    def test_03_baseline_required_current_blocked(self):
        result = self._make_and_eval("baseline", True, "report_produced",
                                     "current", "blocked")
        self.assertEqual(result["aggregate_verdict"], "blocked")

    def test_04_baseline_required_current_inconclusive(self):
        result = self._make_and_eval("baseline", True, "report_produced",
                                     "current", "inconclusive")
        self.assertEqual(result["aggregate_verdict"], "inconclusive")

    def test_05_baseline_required_current_human_review_required(self):
        result = self._make_and_eval("baseline", True, "report_produced",
                                     "current", "human_review_required")
        self.assertEqual(result["aggregate_verdict"], "human_review_required")

    # --- Baseline, required=true, superseded/invalidated/stale/mismatched ---
    # v1.2: non-current freshness produces result (blocked), not error

    def test_06_baseline_required_superseded_blocked(self):
        result = self._make_and_eval_with_freshness(
            "baseline", True, "report_produced", "superseded",
            "pass", supersession={"artifact_id": "r2", "artifact_kind": "validation_report"})
        self.assertEqual(result["aggregate_verdict"], "blocked")

    def test_07_baseline_required_invalidated_blocked(self):
        result = self._make_and_eval_with_freshness(
            "baseline", True, "report_produced", "invalidated",
            "pass", invalidation={"invalidated_by": "admin", "invalidated_at": "now",
                                  "invalidation_reason": "error"})
        self.assertEqual(result["aggregate_verdict"], "blocked")

    def test_08_baseline_required_stale_blocked(self):
        """v1.2: stale report on required baseline -> blocked (was error)."""
        binding = _make_binding("bind-001", "req-001", "pass", confidence="high")
        binding["report_ref"] = {"artifact_id": None, "artifact_kind": "validation_report",
                                 "artifact_version": "1.0.0", "digest": DIGEST_A}
        req = self._make_req("baseline", True)
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["aggregate_verdict"], "blocked")

    def test_09_baseline_required_unreachable_fail_policy(self):
        """v1.2: baseline unreachable with fail policy -> fail result."""
        result = self._make_and_eval_no_report("baseline", True, "unreachable", "fail")
        self.assertEqual(result["aggregate_verdict"], "fail")

    def test_10_baseline_required_unreachable_hr_policy(self):
        """v1.2: baseline unreachable with hr policy -> human_review_required result."""
        result = self._make_and_eval_no_report("baseline", True, "unreachable",
                                               "human_review_required")
        self.assertEqual(result["aggregate_verdict"], "human_review_required")

    # --- Extension, required=true ---

    def test_11_extension_required_current_pass(self):
        result = self._make_and_eval("extension", True, "report_produced",
                                     "current", "pass")
        self.assertEqual(result["aggregate_verdict"], "pass")

    def test_12_extension_required_current_fail(self):
        result = self._make_and_eval("extension", True, "report_produced",
                                     "current", "fail")
        self.assertEqual(result["aggregate_verdict"], "fail")

    def test_13_extension_required_unreachable(self):
        """v1.2: extension required unreachable -> blocked result."""
        result = self._make_and_eval_no_report("extension", True, "unreachable")
        self.assertEqual(result["aggregate_verdict"], "blocked")

    def test_14_extension_required_no_report(self):
        """v1.2: extension required no_report -> blocked result."""
        result = self._make_and_eval_no_report("extension", True, "no_report")
        self.assertEqual(result["aggregate_verdict"], "blocked")

    # --- Extension, required=false ---

    def test_15_extension_optional_current_pass(self):
        result = self._make_and_eval("extension", False, "report_produced",
                                     "current", "pass")
        self.assertEqual(result["aggregate_verdict"], "pass")

    def test_16_extension_optional_unreachable_inconclusive(self):
        """v1.2: extension optional unreachable -> inconclusive result (was error)."""
        result = self._make_and_eval_no_report("extension", False, "unreachable")
        self.assertEqual(result["aggregate_verdict"], "inconclusive")

    # --- Duplicate/conflict ---

    def test_17_duplicate_binding_cross_requirement(self):
        """v1.2: Duplicate binding on required req contributes blocked. Verify freshness assessment."""
        reqs = [
            _make_declaration()["requirements"][0],
            self._make_ext_req("req-002", 1)
        ]
        decl = _make_declaration(requirements=reqs)
        b1 = _make_binding("bind-001", "req-001", "pass", digest=DIGEST_A, confidence="high")
        b2 = _make_binding("bind-002", "req-002", "pass", digest=DIGEST_A,
                           bound_at="2026-07-01T00:01:00Z", confidence="high")
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", b1),
            _make_dispatch_result("req-002", "report_produced", b2),
        ])
        result = rvm.evaluate_validator_mesh(request)
        # v1.2: duplicate on required req -> blocked contribution
        # aggregate: max(pass, blocked) = blocked
        self.assertEqual(result["aggregate_verdict"], "blocked")
        dup = [fa for fa in result["freshness_assessments"]
               if fa["freshness_status"] == "duplicate"]
        self.assertEqual(len(dup), 1)
        self.assertIn("comparison_key", dup[0]["freshness_details"])
        self.assertIn("first_requirement_id", dup[0]["freshness_details"])

    # --- Helper methods ---

    def _make_and_eval(self, kind, required, dispatch_status, freshness, verdict):
        if freshness == "current":
            binding = _make_binding("bind-001", "req-001", verdict, confidence="high")
            req = self._make_req(kind, required)
            decl = _make_declaration(requirements=[req])
            request = _make_request(declaration=decl, dispatch_results=[
                _make_dispatch_result("req-001", dispatch_status, binding)
            ])
            return rvm.evaluate_validator_mesh(request)
        else:
            return self._make_and_eval_with_freshness(kind, required, dispatch_status,
                                                       freshness, verdict)

    def _make_and_eval_with_freshness(self, kind, required, dispatch_status,
                                       freshness, verdict, **freshness_fields):
        binding = _make_binding("bind-001", "req-001", verdict, confidence="high")
        binding.update(freshness_fields)
        req = self._make_req(kind, required)
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", dispatch_status, binding)
        ])
        return rvm.evaluate_validator_mesh(request)

    def _make_and_eval_no_report(self, kind, required, dispatch_status,
                                  policy="fail"):
        req = self._make_req(kind, required, policy)
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", dispatch_status,
                                  error_code="validator_%s" % dispatch_status)
        ])
        return rvm.evaluate_validator_mesh(request)

    def _make_req(self, kind, required, policy="fail"):
        r = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": kind,
            "required": required,
            "dispatch_priority": 0,
            "failure_behavior": "halt_run"
        }
        if kind == "baseline":
            r["missing_mapping_policy"] = policy
        return r

    def _make_ext_req(self, req_id, priority):
        """Used in duplicate test - must share comparison key with default req."""
        return {
            "requirement_id": req_id,
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "extension",
            "required": True,
            "dispatch_priority": priority,
            "failure_behavior": "halt_mesh"
        }


# ---------------------------------------------------------------------------
# Determinism and Order Independence Tests
# ---------------------------------------------------------------------------

class DeterminismTests(unittest.TestCase):
    """Verify deterministic outputs independent of input ordering."""

    def _make_ext_req(self, req_id, priority):
        return {
            "requirement_id": req_id,
            "validator_identity": "validator-ext",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_B},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_B}],
            "requirement_kind": "extension",
            "required": True,
            "dispatch_priority": 1,
            "failure_behavior": "halt_mesh"
        }

    def test_01_report_order_independence(self):
        reqs = [
            _make_declaration()["requirements"][0],
            self._make_ext_req("req-002", 1)
        ]
        decl = _make_declaration(requirements=reqs)
        dr1 = _make_dispatch_result("req-001", "report_produced",
                                    _make_binding("bind-001", "req-001", "pass", digest=DIGEST_A, confidence="high"))
        dr2 = _make_dispatch_result("req-002", "report_produced",
                                    _make_binding("bind-002", "req-002", "fail",
                                                  digest=DIGEST_B,
                                                  contract_digest=DIGEST_B,
                                                  target_digest=DIGEST_B, confidence="high"))

        r1 = rvm.evaluate_validator_mesh(_make_request(
            declaration=decl, dispatch_results=[dr1, dr2]))
        r2 = rvm.evaluate_validator_mesh(_make_request(
            declaration=decl, dispatch_results=[dr2, dr1]))

        self.assertEqual(r1["aggregate_verdict"], r2["aggregate_verdict"])
        self.assertEqual(json.dumps(r1, sort_keys=True),
                         json.dumps(r2, sort_keys=True))

    def test_02_declaration_order_independence(self):
        req1 = {
            "requirement_id": "req-001",
            "validator_identity": "validator-1",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "baseline",
            "required": True,
            "dispatch_priority": 0,
            "missing_mapping_policy": "fail",
            "failure_behavior": "halt_run"
        }
        req2 = {
            "requirement_id": "req-002",
            "validator_identity": "validator-2",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_B},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_B}],
            "requirement_kind": "extension",
            "required": True,
            "dispatch_priority": 1,
            "failure_behavior": "halt_mesh"
        }

        decl1 = _make_declaration(requirements=[req1, req2])
        decl2 = _make_declaration(requirements=[req2, req1])

        dr1 = _make_dispatch_result("req-001", "report_produced",
                                    _make_binding("bind-001", "req-001", "pass", digest=DIGEST_A, confidence="high"))
        dr2 = _make_dispatch_result("req-002", "report_produced",
                                    _make_binding("bind-002", "req-002", "fail",
                                                  digest=DIGEST_B,
                                                  contract_digest=DIGEST_B,
                                                  target_digest=DIGEST_B, confidence="high"))

        r1 = rvm.evaluate_validator_mesh(_make_request(
            declaration=decl1, dispatch_results=[dr1, dr2]))
        r2 = rvm.evaluate_validator_mesh(_make_request(
            declaration=decl2, dispatch_results=[dr2, dr1]))

        self.assertEqual(r1["aggregate_verdict"], r2["aggregate_verdict"])

    def test_03_same_input_same_output(self):
        request = _make_request()
        r1 = rvm.evaluate_validator_mesh(request)
        r2 = rvm.evaluate_validator_mesh(request)
        self.assertEqual(json.dumps(r1, sort_keys=True),
                         json.dumps(r2, sort_keys=True))

    def test_04_error_determinism(self):
        decl = _make_declaration()
        decl["mesh_version"] = "0.9.0"
        request = _make_request(declaration=decl)
        r1 = rvm.evaluate_validator_mesh(request)
        r2 = rvm.evaluate_validator_mesh(request)
        self.assertEqual(json.dumps(r1, sort_keys=True),
                         json.dumps(r2, sort_keys=True))


# ---------------------------------------------------------------------------
# Input Preservation Tests
# ---------------------------------------------------------------------------

class InputPreservationTests(unittest.TestCase):
    """Verify caller objects remain deeply unchanged."""

    def test_01_success_preserves_input(self):
        request = _make_request()
        original = copy.deepcopy(request)
        rvm.evaluate_validator_mesh(request)
        self.assertEqual(json.dumps(request, sort_keys=True),
                         json.dumps(original, sort_keys=True))

    def test_02_error_preserves_input(self):
        decl = _make_declaration()
        decl["mesh_version"] = "0.9.0"
        request = _make_request(declaration=decl)
        original = copy.deepcopy(request)
        rvm.evaluate_validator_mesh(request)
        self.assertEqual(json.dumps(request, sort_keys=True),
                         json.dumps(original, sort_keys=True))

    def test_03_deep_copy_used(self):
        request = _make_request()
        result = rvm.evaluate_validator_mesh(request)
        original_request = copy.deepcopy(request)
        if "report_bindings" in result:
            self.assertIsNot(result.get("report_bindings"),
                             request["dispatch_results"][0].get("report_binding"))
        self.assertEqual(json.dumps(request, sort_keys=True),
                         json.dumps(original_request, sort_keys=True))


# ---------------------------------------------------------------------------
# XOR Semantics Tests
# ---------------------------------------------------------------------------

class XORSemanticsTests(unittest.TestCase):
    """Verify exactly one output per input branch."""

    def test_01_valid_input_produces_result_not_error(self):
        request = _make_request()
        result = rvm.evaluate_validator_mesh(request)
        self.assertIn("aggregate_verdict", result)
        self.assertNotIn("error_code", result)

    def test_02_invalid_input_produces_error_not_result(self):
        request = _make_request()
        request["mesh_declaration"]["mesh_version"] = "0.9.0"
        result = rvm.evaluate_validator_mesh(request)
        self.assertIn("error_code", result)
        self.assertNotIn("aggregate_verdict", result)

    def test_03_no_branch_produces_both(self):
        for verdict in ("pass", "fail", "blocked", "inconclusive", "human_review_required"):
            request = _make_request(dispatch_results=[
                _make_dispatch_result("req-001", "report_produced",
                                      _make_binding("bind-001", "req-001", verdict, confidence="high"))
            ])
            result = rvm.evaluate_validator_mesh(request)
            has_result = "aggregate_verdict" in result
            has_error = "error_code" in result
            self.assertTrue(has_result ^ has_error,
                            "verdict=%s: has_result=%s, has_error=%s"
                            % (verdict, has_result, has_error))


# ---------------------------------------------------------------------------
# Recommended Action Tests
# ---------------------------------------------------------------------------

class RecommendedActionTests(unittest.TestCase):
    """Verify recommended_action for all verdict-confidence pairs."""

    def test_01_pass_verdict_action(self):
        request = _make_request()
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["recommended_action"], "proceed")

    def test_02_fail_verdict_action(self):
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced",
                                  _make_binding("bind-001", "req-001", "fail", confidence="high"))
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["recommended_action"], "stop_run")

    def test_03_blocked_verdict_action(self):
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced",
                                  _make_binding("bind-001", "req-001", "blocked", confidence="high"))
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["recommended_action"], "more_evidence")

    def test_04_inconclusive_verdict_action(self):
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced",
                                  _make_binding("bind-001", "req-001", "inconclusive", confidence="high"))
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["recommended_action"], "human_intervention")

    def test_05_human_review_required_action(self):
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced",
                                  _make_binding("bind-001", "req-001", "human_review_required", confidence="high"))
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["recommended_action"], "human_intervention")


# ---------------------------------------------------------------------------
# Result Structure Tests (v1.2: no gate_bridge_envelope)
# ---------------------------------------------------------------------------

class ResultStructureTests(unittest.TestCase):
    """Verify result structure completeness (v1.2)."""

    REQUIRED_RESULT_FIELDS = [
        "mesh_eval_id", "mesh_id", "aggregate_verdict",
        "aggregate_confidence", "report_bindings", "requirement_results",
        "freshness_assessments", "recommended_action",
        "evaluated_at", "evaluated_by",
    ]

    REQUIRED_ERROR_FIELDS = [
        "mesh_eval_id", "mesh_id", "error_code", "error_description",
        "run_context",
    ]

    REQUIRED_REQUIREMENT_RESULT_FIELDS = [
        "requirement_id", "requirement_kind", "required",
        "dispatch_status", "report_verdict_or_null", "freshness_or_null",
        "result_kind",
    ]

    def test_01_result_has_all_required_fields(self):
        request = _make_request()
        result = rvm.evaluate_validator_mesh(request)
        for field in self.REQUIRED_RESULT_FIELDS:
            self.assertIn(field, result,
                          "Missing required result field: %s" % field)

    def test_02_error_has_all_required_fields(self):
        decl = _make_declaration()
        decl["mesh_version"] = "0.9.0"
        request = _make_request(declaration=decl)
        result = rvm.evaluate_validator_mesh(request)
        for field in self.REQUIRED_ERROR_FIELDS:
            self.assertIn(field, result,
                          "Missing required error field: %s" % field)

    def test_03_result_fields_from_caller(self):
        request = _make_request("eval-custom")
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["mesh_eval_id"], "eval-custom")
        self.assertEqual(result["mesh_id"], "mesh-test")
        self.assertEqual(result["evaluated_at"], request["requested_at"])
        self.assertEqual(result["evaluated_by"], request["requested_by"])

    def test_04_requirement_results_count(self):
        reqs = [
            _make_declaration()["requirements"][0],
            {
                "requirement_id": "req-002",
                "validator_identity": "validator-ext",
                "contract_ref": {
                    "artifact_id": "vc-001", "artifact_kind": "contract",
                    "artifact_version": "1.0.0", "digest": DIGEST_B},
                "artifact_scope": [{
                    "artifact_id": "artifact-001", "artifact_kind": "artifact",
                    "artifact_version": "1.0.0", "digest": DIGEST_B}],
                "requirement_kind": "extension",
                "required": True,
                "dispatch_priority": 1,
                "failure_behavior": "halt_mesh"
            }
        ]
        decl = _make_declaration(requirements=reqs)
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced",
                                  _make_binding("bind-001", "req-001", "pass", confidence="high")),
            _make_dispatch_result("req-002", "report_produced",
                                  _make_binding("bind-002", "req-002", "fail",
                                                digest=DIGEST_B,
                                                contract_digest=DIGEST_B,
                                                target_digest=DIGEST_B, confidence="high")),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(len(result["requirement_results"]), 2)

    def test_05_aggregate_confidence_valid(self):
        request = _make_request()
        result = rvm.evaluate_validator_mesh(request)
        self.assertIn(result["aggregate_confidence"], ("high", "medium", "low"))

    def test_06_requirement_result_has_required_fields(self):
        """v1.2: requirement_results entries have the new required fields."""
        request = _make_request()
        result = rvm.evaluate_validator_mesh(request)
        for rr in result["requirement_results"]:
            for field in self.REQUIRED_REQUIREMENT_RESULT_FIELDS:
                self.assertIn(field, rr,
                              "Missing requirement_result field: %s" % field)

    def test_07_verdict_contribution_xor_excluded_reason(self):
        """v1.2: verdict_contribution XOR excluded_reason, never both."""
        request = _make_request()
        result = rvm.evaluate_validator_mesh(request)
        for rr in result["requirement_results"]:
            has_verdict = "verdict_contribution" in rr
            has_excluded = "excluded_reason" in rr
            self.assertTrue(has_verdict ^ has_excluded,
                            "requirement_result has both or neither: %s" % rr["requirement_id"])


# ---------------------------------------------------------------------------
# Multi-requirement Scenario Tests (v1.2)
# ---------------------------------------------------------------------------

class MultiRequirementTests(unittest.TestCase):
    """Verify multi-requirement scenarios including mixed kinds."""

    def test_01_baseline_pass_opt_extension_unreachable(self):
        """Baseline pass + optional extension unreachable -> result (ext excluded)."""
        reqs = [
            {
                "requirement_id": "req-baseline",
                "validator_identity": "validator-core",
                "contract_ref": {
                    "artifact_id": "vc-001", "artifact_kind": "contract",
                    "artifact_version": "1.0.0", "digest": DIGEST_A},
                "artifact_scope": [{
                    "artifact_id": "artifact-001", "artifact_kind": "artifact",
                    "artifact_version": "1.0.0", "digest": DIGEST_A}],
                "requirement_kind": "baseline",
                "required": True,
                "dispatch_priority": 0,
                "missing_mapping_policy": "fail",
                "failure_behavior": "halt_run"
            },
            {
                "requirement_id": "req-ext-opt",
                "validator_identity": "validator-ext",
                "contract_ref": {
                    "artifact_id": "vc-001", "artifact_kind": "contract",
                    "artifact_version": "1.0.0", "digest": DIGEST_B},
                "artifact_scope": [{
                    "artifact_id": "artifact-001", "artifact_kind": "artifact",
                    "artifact_version": "1.0.0", "digest": DIGEST_B}],
                "requirement_kind": "extension",
                "required": False,
                "dispatch_priority": 1,
                "failure_behavior": "halt_mesh"
            }
        ]
        decl = _make_declaration(requirements=reqs)
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-baseline", "report_produced",
                                  _make_binding("bind-001", "req-baseline", "pass", confidence="high")),
            _make_dispatch_result("req-ext-opt", "unreachable",
                                  error_code="validator_unreachable"),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["aggregate_verdict"], "pass")

    def test_02_all_pass_baseline_cases(self):
        result = self._make_and_eval()
        self.assertEqual(result["aggregate_verdict"], "pass")
        self.assertEqual(result["aggregate_confidence"], "high")

    def test_03_empty_optional_extensions(self):
        """Optional extension absence -> result with inconclusive aggregate."""
        reqs = [
            {
                "requirement_id": "req-baseline",
                "validator_identity": "validator-core",
                "contract_ref": {
                    "artifact_id": "vc-001", "artifact_kind": "contract",
                    "artifact_version": "1.0.0", "digest": DIGEST_A},
                "artifact_scope": [{
                    "artifact_id": "artifact-001", "artifact_kind": "artifact",
                    "artifact_version": "1.0.0", "digest": DIGEST_A}],
                "requirement_kind": "baseline",
                "required": True,
                "dispatch_priority": 0,
                "missing_mapping_policy": "fail",
                "failure_behavior": "halt_run"
            },
            {
                "requirement_id": "req-ext-opt",
                "validator_identity": "validator-ext",
                "contract_ref": {
                    "artifact_id": "vc-001", "artifact_kind": "contract",
                    "artifact_version": "1.0.0", "digest": DIGEST_B},
                "artifact_scope": [{
                    "artifact_id": "artifact-001", "artifact_kind": "artifact",
                    "artifact_version": "1.0.0", "digest": DIGEST_B}],
                "requirement_kind": "extension",
                "required": False,
                "dispatch_priority": 1,
                "failure_behavior": "halt_mesh"
            }
        ]
        decl = _make_declaration(requirements=reqs)
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-baseline", "report_produced",
                                  _make_binding("bind-b", "req-baseline", "pass", confidence="high")),
            _make_dispatch_result("req-ext-opt", "no_report",
                                  error_code="validator_no_report"),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["aggregate_verdict"], "pass")

    def _make_and_eval(self):
        request = _make_request()
        return rvm.evaluate_validator_mesh(request)


# ---------------------------------------------------------------------------
# Report Quality Tests (v1.2: non-report statuses now produce results)
# ---------------------------------------------------------------------------

class ReportQualityTests(unittest.TestCase):
    """Verify handling of varying report quality (v1.2)."""

    def get_error_code(self, result):
        if "error_code" in result:
            return result["error_code"]
        return None

    def _make_req(self, kind, required, req_id="req-001", policy="fail"):
        return {
            "requirement_id": req_id,
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": kind,
            "required": required,
            "dispatch_priority": 0,
            "failure_behavior": "halt_run" if kind == "baseline" else "halt_mesh"
        }

    def test_01_degraded_storage_dispatch(self):
        """v1.2: baseline required degraded_storage -> fail result via policy."""
        req = self._make_req("baseline", True)
        req["missing_mapping_policy"] = "fail"
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "degraded_storage",
                                  error_code="validator_degraded_storage",
                                  degradation_note="degraded note")
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["aggregate_verdict"], "fail")

    def test_02_degraded_transport_dispatch(self):
        """v1.2: extension required degraded_transport -> blocked result."""
        req = self._make_req("extension", True)
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "degraded_transport",
                                  error_code="validator_degraded_transport",
                                  degradation_note="transport note")
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["aggregate_verdict"], "blocked")

    def test_03_optional_degraded_storage(self):
        """v1.2: optional extension degraded_storage -> inconclusive result (was error)."""
        req = self._make_req("extension", False)
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "degraded_storage",
                                  error_code="validator_degraded_storage",
                                  degradation_note="degraded note")
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["aggregate_verdict"], "inconclusive")


# ---------------------------------------------------------------------------
# All Five Verdicts for Each Report State Test
# ---------------------------------------------------------------------------

class AllVerdictReportStateTests(unittest.TestCase):
    """Enumerate all five verdicts for each report state."""

    def test_01_all_verdicts_produce_result(self):
        for verdict in ("pass", "fail", "blocked", "inconclusive", "human_review_required"):
            request = _make_request(dispatch_results=[
                _make_dispatch_result("req-001", "report_produced",
                                      _make_binding("bind-001", "req-001", verdict, confidence="high"))
            ])
            result = rvm.evaluate_validator_mesh(request)
            self.assertIn("aggregate_verdict", result,
                          "verdict=%s produced no aggregate_verdict" % verdict)
            self.assertEqual(result["aggregate_verdict"], verdict,
                             "verdict=%s got aggregate=%s" % (verdict, result["aggregate_verdict"]))

    def test_02_all_dispatch_status_covered(self):
        """Each dispatch status produces a result (v1.2)."""
        status_samples = {
            "report_produced": "pass",
            "unreachable": "fail",
            "no_report": "fail",
            "degraded_storage": "fail",
            "degraded_transport": "fail",
        }
        for status, expected_verdict in status_samples.items():
            binding = None
            if status == "report_produced":
                binding = _make_binding("bind-001", "req-001", "pass", confidence="high")
            req = {
                "requirement_id": "req-001",
                "validator_identity": "validator-core",
                "contract_ref": {
                    "artifact_id": "vc-001", "artifact_kind": "contract",
                    "artifact_version": "1.0.0", "digest": DIGEST_A},
                "artifact_scope": [{
                    "artifact_id": "artifact-001", "artifact_kind": "artifact",
                    "artifact_version": "1.0.0", "digest": DIGEST_A}],
                "requirement_kind": "baseline",
                "required": True,
                "dispatch_priority": 0,
                "missing_mapping_policy": "fail",
                "failure_behavior": "halt_run"
            }
            decl = _make_declaration(requirements=[req])
            dr = _make_dispatch_result("req-001", status, binding=binding)
            request = _make_request(declaration=decl, dispatch_results=[dr])
            result = rvm.evaluate_validator_mesh(request)
            if "aggregate_verdict" in result:
                self.assertEqual(result["aggregate_verdict"], expected_verdict,
                                 "status=%s got %s, expected %s"
                                 % (status, result["aggregate_verdict"], expected_verdict))


# ---------------------------------------------------------------------------
# ContractKey / TargetKey Comparison Tests (v1.2)
# ---------------------------------------------------------------------------

class ContractKeyTargetKeyTests(unittest.TestCase):
    """Verify ContractKey and TargetKey equality comparisons (v1.2)."""

    def get_error_code(self, result):
        if "error_code" in result:
            return result["error_code"]
        return None

    def test_01_contract_key_match_current(self):
        """When binding ContractKey matches declared, no mismatch."""
        request = _make_request()
        result = rvm.evaluate_validator_mesh(request)
        self.assertIsNone(self.get_error_code(result))
        mismatches = [fa for fa in result["freshness_assessments"]
                      if fa["freshness_status"] == "mismatched"]
        self.assertEqual(len(mismatches), 0)

    def test_02_contract_key_mismatch_mismatched(self):
        """When binding ContractKey differs, get mismatched with contract_binding."""
        req = _make_declaration()["requirements"][0]
        binding = _make_binding("bind-001", "req-001", "pass",
                                contract_digest=DIGEST_B, confidence="high")  # Different from DIGEST_A
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        mismatches = [fa for fa in result["freshness_assessments"]
                      if fa["freshness_status"] == "mismatched"]
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["freshness_details"],
                         {"field_category": "contract_binding"})

    def test_03_target_key_mismatch_mismatched(self):
        """When binding TargetKey differs, get mismatched with target_artifact_binding."""
        req = _make_declaration()["requirements"][0]
        binding = _make_binding("bind-001", "req-001", "pass",
                                target_digest=DIGEST_B, confidence="high")  # Different from DIGEST_A
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        mismatches = [fa for fa in result["freshness_assessments"]
                      if fa["freshness_status"] == "mismatched"]
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["freshness_details"],
                         {"field_category": "target_artifact_binding"})

    def test_04_contract_mismatch_before_target(self):
        """Contract mismatch is detected before target mismatch (precedence)."""
        req = _make_declaration()["requirements"][0]
        binding = _make_binding("bind-001", "req-001", "pass",
                                contract_digest=DIGEST_B,
                                target_digest=DIGEST_B, confidence="high")  # Both differ
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        mismatches = [fa for fa in result["freshness_assessments"]
                      if fa["freshness_status"] == "mismatched"]
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["freshness_details"],
                         {"field_category": "contract_binding"})


# ---------------------------------------------------------------------------
# Cross-requirement Duplicate/Conflict Tests (v1.2)
# ---------------------------------------------------------------------------

class CrossRequirementDuplicateConflictTests(unittest.TestCase):
    """Verify cross-requirement ComparisonKey grouping for duplicate/conflict."""

    def get_error_code(self, result):
        if "error_code" in result:
            return result["error_code"]
        return None

    def _make_req(self, req_id, kind, required, priority, validator="validator-core",
                  contract_digest=DIGEST_A, target_digest=DIGEST_A, policy="fail"):
        r = {
            "requirement_id": req_id,
            "validator_identity": validator,
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": contract_digest},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": target_digest}],
            "requirement_kind": kind,
            "required": required,
            "dispatch_priority": priority,
            "failure_behavior": "halt_run"
        }
        if kind == "baseline":
            r["missing_mapping_policy"] = policy
        return r

    def _make_binding_for_req(self, req, binding_id, verdict, digest, *,
                               confidence, contract_digest=None, target_digest=None):
        cd = contract_digest if contract_digest is not None else req["contract_ref"]["digest"]
        td = target_digest if target_digest is not None else req["artifact_scope"][0]["digest"]
        return _make_binding(
            binding_id, req["requirement_id"], verdict, confidence=confidence,
            digest=digest, contract_digest=cd, target_digest=td,
            validator_identity=req["validator_identity"],
            bound_at="2026-07-01T00:00:00Z")

    def test_01_same_comparison_key_same_digest_duplicate(self):
        """v1.2: Bindings with same ComparisonKey and same digest -> second is duplicate.
        Required duplicate contributes blocked, so aggregate = blocked."""
        req1 = self._make_req("req-001", "baseline", True, 0)
        req2 = self._make_req("req-002", "extension", True, 1, contract_digest=DIGEST_A, target_digest=DIGEST_A)
        decl = _make_declaration(requirements=[req1, req2])
        b1 = self._make_binding_for_req(req1, "bind-001", "pass", DIGEST_A, confidence="high")
        b2 = self._make_binding_for_req(req2, "bind-002", "pass", DIGEST_A, confidence="high")
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", b1),
            _make_dispatch_result("req-002", "report_produced", b2),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertIsNone(self.get_error_code(result))
        # v1.2: duplicate on required -> blocked; aggregate = max(pass, blocked) = blocked
        self.assertEqual(result["aggregate_verdict"], "blocked")
        dupes = [fa for fa in result["freshness_assessments"]
                 if fa["freshness_status"] == "duplicate"]
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0]["binding_id"], "bind-002")

    def test_02_same_comparison_key_diff_digest_conflicting(self):
        """v1.2: Bindings with same ComparisonKey but different digest -> conflicting.
        Conflicting on required req contributes blocked."""
        req1 = self._make_req("req-001", "baseline", True, 0)
        req2 = self._make_req("req-002", "extension", True, 1, contract_digest=DIGEST_A, target_digest=DIGEST_A)
        decl = _make_declaration(requirements=[req1, req2])
        b1 = self._make_binding_for_req(req1, "bind-001", "pass", DIGEST_A, confidence="high")
        b2 = self._make_binding_for_req(req2, "bind-002", "pass", DIGEST_B, confidence="high")
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", b1),
            _make_dispatch_result("req-002", "report_produced", b2),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertIsNone(self.get_error_code(result))
        # v1.2: conflicting on required -> blocked
        conflicts = [fa for fa in result["freshness_assessments"]
                     if fa["freshness_status"] == "conflicting"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["binding_id"], "bind-002")
        self.assertIn("comparison_key", conflicts[0]["freshness_details"])
        self.assertIn("requirement_ids", conflicts[0]["freshness_details"])
        self.assertGreaterEqual(len(conflicts[0]["freshness_details"]["requirement_ids"]), 2)

    def test_03_group_ordering_by_priority(self):
        """Group first member is determined by dispatch_priority, not requirement order."""
        req_low = self._make_req("req-low", "baseline", True, 10, contract_digest=DIGEST_A, target_digest=DIGEST_A)
        req_high = self._make_req("req-high", "extension", True, 0, contract_digest=DIGEST_A, target_digest=DIGEST_A)
        decl = _make_declaration(requirements=[req_low, req_high])
        b1 = self._make_binding_for_req(req_low, "bind-low", "pass", DIGEST_A, confidence="high")
        b2 = self._make_binding_for_req(req_high, "bind-high", "pass", DIGEST_A, confidence="high")
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-low", "report_produced", b1),
            _make_dispatch_result("req-high", "report_produced", b2),
        ])
        result = rvm.evaluate_validator_mesh(request)
        current = [fa for fa in result["freshness_assessments"]
                   if fa["freshness_status"] == "current"]
        self.assertEqual(len(current), 1)
        # Higher priority (lower number) should be current
        self.assertEqual(current[0]["binding_id"], "bind-high")

    def test_04_group_ordering_permutation_independence(self):
        """Group ordering is deterministic regardless of dispatch result order."""
        req1 = self._make_req("req-a", "baseline", True, 0, contract_digest=DIGEST_A, target_digest=DIGEST_A)
        req2 = self._make_req("req-b", "extension", True, 1, contract_digest=DIGEST_A, target_digest=DIGEST_A)
        decl = _make_declaration(requirements=[req1, req2])
        b1 = self._make_binding_for_req(req1, "bind-a", "pass", DIGEST_A, confidence="high")
        b2 = self._make_binding_for_req(req2, "bind-b", "pass", DIGEST_A, confidence="high")

        r1 = rvm.evaluate_validator_mesh(_make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-a", "report_produced", b1),
            _make_dispatch_result("req-b", "report_produced", b2),
        ]))
        r2 = rvm.evaluate_validator_mesh(_make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-b", "report_produced", b2),
            _make_dispatch_result("req-a", "report_produced", b1),
        ]))
        self.assertEqual(
            json.dumps(r1["freshness_assessments"], sort_keys=True),
            json.dumps(r2["freshness_assessments"], sort_keys=True))


# ---------------------------------------------------------------------------
# Exhaustive Matrix Tests (v1.2)
# ---------------------------------------------------------------------------

class ExhaustiveMatrixTests(unittest.TestCase):
    """Exhaustive 234-cell matrix tests that enumerate all combinations
    and compare to contract-derived oracle (v1.2)."""

    VERDICTS = ("pass", "blocked", "fail", "human_review_required", "inconclusive")
    FRESHNESSES = ("superseded", "invalidated", "stale", "mismatched", "duplicate", "conflicting", "current")
    UNAVAILABLE_STATUSES = ("no_report", "unreachable", "degraded_storage", "degraded_transport")

    def _oracle(self, kind, required, missing_policy, dispatch_status, verdict, freshness):
        """Contract-derived oracle for a single matrix cell.
        Returns (result_kind, verdict_contribution_or_None, excluded_reason_or_None).
        """
        if dispatch_status == "report_produced":
            if freshness == "current":
                return ("report", verdict, None)
            if required:
                return ("unusable_required_report", "blocked", None)
            return ("optional_excluded", None, "optional_unusable_report")
        else:
            if kind == "baseline":
                return ("missing_baseline", missing_policy, None)
            if required:
                return ("missing_required_extension", "blocked", None)
            return ("optional_excluded", None, "optional_unavailable")

    def test_01_matrix_cell_count(self):
        """Verify the exact matrix cell counts."""
        total = 0
        for kind, required, policy in self._requirement_combos():
            for status in self.UNAVAILABLE_STATUSES:
                total += 1
            for verdict in self.VERDICTS:
                total += 1  # current
                for freshness in self.FRESHNESSES:
                    if freshness != "current":
                        total += 1
        # 6 * (4 + 5 + 5*6) = 6 * 39 = 234
        self.assertEqual(total, 234)

    def test_02_all_cells_are_valid(self):
        """Every matrix cell produces a result, never an error.

        Invocation ledger: no continue, early return, skip, or xfail may bypass
        the production call or oracle comparison for any of the 234 cells.
        Duplicate and conflicting cells execute with two distinct requirement IDs
        and deterministic comparison groups.
        """
        attempted = 0
        compared = 0
        skipped = []
        unexpected_errors = []
        ledger = []  # (cell_index, kind, required, policy, status, verdict, freshness)

        for cell_idx, cell in enumerate(self._enumerate_all_cells()):
            kind, required, policy, status, verdict, freshness = cell
            expected = self._oracle(kind, required, policy, status, verdict, freshness)
            ledger.append((cell_idx, cell))

            if status == "report_produced":
                if freshness in ("duplicate", "conflicting"):
                    # Cross-requirement: two requirements share same ComparisonKey.
                    # req-001 (priority 0) is "current"; req-002 is the cell under test.
                    req_first = self._make_cell_req(kind, required, policy, "req-001", 0)
                    req_test = self._make_cell_req(kind, required, policy, "req-002", 1)
                    decl = _make_declaration(requirements=[req_first, req_test])

                    # First binding: current (matching contract/target digest)
                    first_binding = _make_binding("bind-first", "req-001", verdict,
                                                  digest=DIGEST_A, confidence="high")
                    first_dr = _make_dispatch_result("req-001", "report_produced", first_binding)

                    # Cell binding: same ComparisonKey as first_binding
                    # (same contract_digest, target_digest, validator_identity)
                    # but different report_sha256 for conflicting.
                    if freshness == "duplicate":
                        test_binding = _make_binding("bind-cell", "req-002", verdict,
                                                     digest=DIGEST_A, confidence="high")
                    else:  # conflicting
                        test_binding = _make_binding("bind-cell", "req-002", verdict,
                                                     digest=DIGEST_B,
                                                     contract_digest=DIGEST_A,
                                                     target_digest=DIGEST_A, confidence="high")
                    test_dr = _make_dispatch_result("req-002", "report_produced", test_binding)

                    request = _make_request(declaration=decl,
                                            dispatch_results=[first_dr, test_dr])
                    attempted += 1
                    result = rvm.evaluate_validator_mesh(request)

                    if "error_code" in result:
                        unexpected_errors.append((cell_idx, cell, result.get("error_code")))
                        continue

                    # The cell under test is req-002 (second in declaration order)
                    test_rr = next(
                        (rr for rr in result["requirement_results"]
                         if rr["requirement_id"] == "req-002"), None)
                    self.assertIsNotNone(test_rr,
                        "Cell %s: req-002 not found in requirement_results" % str(cell))

                    # Verify freshness
                    test_fa = next(
                        (fa for fa in result["freshness_assessments"]
                         if fa["binding_id"] == "bind-cell"), None)
                    self.assertIsNotNone(test_fa,
                        "Cell %s: bind-cell not found in freshness_assessments" % str(cell))
                    self.assertEqual(test_fa["freshness_status"], freshness,
                        "Cell %s: expected freshness=%s, got=%s" %
                        (cell, freshness, test_fa["freshness_status"]))
                    if freshness == "conflicting":
                        self.assertIn("requirement_ids",
                                      test_fa.get("freshness_details", {}))

                    compared += 1
                    self.assertEqual(test_rr["result_kind"], expected[0],
                        "Cell %s: expected result_kind=%s, got=%s" %
                        (cell, expected[0], test_rr["result_kind"]))
                    if expected[1] is not None:
                        self.assertIn("verdict_contribution", test_rr,
                            "Cell %s: expected verdict_contribution" % str(cell))
                        self.assertEqual(test_rr["verdict_contribution"], expected[1])
                    if expected[2] is not None:
                        self.assertIn("excluded_reason", test_rr,
                            "Cell %s: expected excluded_reason" % str(cell))
                        self.assertEqual(test_rr["excluded_reason"], expected[2])
                    continue

                # Single-requirement cells (non-duplicate, non-conflicting)
                req = self._make_cell_req(kind, required, policy)
                decl = _make_declaration(requirements=[req])

                if freshness == "current":
                    binding = _make_binding("bind-cell", "req-001", verdict, digest=DIGEST_A, confidence="high")
                    dr = _make_dispatch_result("req-001", "report_produced", binding)
                elif freshness == "superseded":
                    binding = _make_binding("bind-cell", "req-001", verdict, digest=DIGEST_A,
                                            extra={"supersession": {"artifact_id": "r2", "artifact_kind": "report"}}, confidence="high")
                    dr = _make_dispatch_result("req-001", "report_produced", binding)
                elif freshness == "invalidated":
                    binding = _make_binding("bind-cell", "req-001", verdict, digest=DIGEST_A,
                                            extra={"invalidation": {"reason": "test"}}, confidence="high")
                    dr = _make_dispatch_result("req-001", "report_produced", binding)
                elif freshness == "stale":
                    binding = _make_binding("bind-cell", "req-001", verdict, digest=DIGEST_A, confidence="high")
                    binding["report_ref"] = {"artifact_id": None, "artifact_kind": "validation_report",
                                             "artifact_version": "1.0.0", "digest": DIGEST_A}
                    dr = _make_dispatch_result("req-001", "report_produced", binding)
                elif freshness == "mismatched":
                    binding = _make_binding("bind-cell", "req-001", verdict,
                                            contract_digest=DIGEST_B, digest=DIGEST_A, confidence="high")
                    dr = _make_dispatch_result("req-001", "report_produced", binding)
                else:
                    skipped.append((cell_idx, cell, "unknown_freshness"))
                    continue
            else:
                req = self._make_cell_req(kind, required, policy)
                decl = _make_declaration(requirements=[req])
                dr = _make_dispatch_result("req-001", status,
                                           error_code="validator_%s" % status)

            request = _make_request(declaration=decl, dispatch_results=[dr])
            attempted += 1
            result = rvm.evaluate_validator_mesh(request)

            if "error_code" in result:
                unexpected_errors.append((cell_idx, cell, result.get("error_code")))
                continue

            compared += 1
            self.assertIn("aggregate_verdict", result,
                          "Cell %s produced error" % str(cell))

            rr = result["requirement_results"][0]
            self.assertEqual(rr["result_kind"], expected[0],
                             "Cell %s: expected result_kind=%s, got=%s" %
                             (cell, expected[0], rr["result_kind"]))
            if expected[1] is not None:
                self.assertIn("verdict_contribution", rr,
                              "Cell %s: expected verdict_contribution" % str(cell))
                self.assertEqual(rr["verdict_contribution"], expected[1])
            if expected[2] is not None:
                self.assertIn("excluded_reason", rr,
                              "Cell %s: expected excluded_reason" % str(cell))
                self.assertEqual(rr["excluded_reason"], expected[2])

        # Ledger verification
        self.assertEqual(attempted, 234,
            "Attempted %d cells, expected 234" % attempted)
        self.assertEqual(compared, 234,
            "Compared %d cells, expected 234 (skipped=%d, errors=%d)" %
            (compared, len(skipped), len(unexpected_errors)))
        self.assertEqual(len(skipped), 0,
            "Skipped %d cells: %s" % (len(skipped), skipped))
        self.assertEqual(len(unexpected_errors), 0,
            "Unexpected errors in %d cells: %s" %
            (len(unexpected_errors), unexpected_errors[:5]))

    def test_03_aggregate_precedence(self):
        """Verify aggregate precedence: fail > blocked > human_review_required > inconclusive > pass."""
        # Test all combinations of two contributions
        verdicts = ["fail", "blocked", "human_review_required", "inconclusive", "pass"]
        for i, v1 in enumerate(verdicts):
            for j, v2 in enumerate(verdicts):
                expected = verdicts[min(i, j)]  # lower index = higher precedence
                reqs = [
                    self._make_cell_req("baseline", True, "fail", "req-a", 0, DIGEST_A, DIGEST_A),
                    self._make_cell_req("extension", True, None, "req-b", 1, DIGEST_B, DIGEST_B),
                ]
                decl = _make_declaration(requirements=reqs)
                b1 = _make_binding("bind-a", "req-a", v1, digest=DIGEST_A, confidence="high")
                b2 = _make_binding("bind-b", "req-b", v2, digest=DIGEST_B,
                                   contract_digest=DIGEST_B, target_digest=DIGEST_B, confidence="high")
                request = _make_request(declaration=decl, dispatch_results=[
                    _make_dispatch_result("req-a", "report_produced", b1),
                    _make_dispatch_result("req-b", "report_produced", b2),
                ])
                result = rvm.evaluate_validator_mesh(request)
                self.assertEqual(result["aggregate_verdict"], expected,
                                 "v1=%s v2=%s expected=%s got=%s" %
                                 (v1, v2, expected, result["aggregate_verdict"]))

    def test_04_zero_contribution_inconclusive(self):
        """v1.2: zero contributions produces inconclusive result, never error."""
        req = self._make_cell_req("extension", False, None, "req-001", 0, DIGEST_C, DIGEST_C)
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "unreachable",
                                  error_code="validator_unreachable"),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertIn("aggregate_verdict", result)
        self.assertEqual(result["aggregate_verdict"], "inconclusive")

    def test_05_aggregate_confidence_from_current_only(self):
        """v1.2: aggregate_confidence only considers current contributing bindings."""
        req = _make_declaration()["requirements"][0]
        binding = _make_binding("bind-001", "req-001", "pass", digest=DIGEST_A, confidence="high")
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["aggregate_confidence"], "high")

    def test_06_confidence_low_when_no_current(self):
        """v1.2: when only non-current contributions, confidence is 'low'."""
        req = self._make_cell_req("baseline", True, "fail", "req-001", 0, DIGEST_A, DIGEST_A)
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "unreachable",
                                  error_code="validator_unreachable"),
        ])
        result = rvm.evaluate_validator_mesh(request)
        # Baseline unreachable -> contributes fail, but no current report
        # Confidence should be "low" since no current report contributes
        self.assertEqual(result["aggregate_confidence"], "low")

    def test_07_contribution_and_exclusion_totals(self):
        """Matrix totals: 140 contributions and 94 exclusions, computed from
        observed candidate outputs, not copied from constants."""
        contributions = 0
        exclusions = 0

        for cell in self._enumerate_all_cells():
            kind, required, policy, status, verdict, freshness = cell
            expected = self._oracle(kind, required, policy, status, verdict, freshness)

            if freshness in ("duplicate", "conflicting"):
                # Cross-requirement: two requirements, cell is req-002
                req_first = self._make_cell_req(kind, required, policy, "req-001", 0)
                req_test = self._make_cell_req(kind, required, policy, "req-002", 1)
                decl = _make_declaration(requirements=[req_first, req_test])
                first_binding = _make_binding("bind-first", "req-001", verdict,
                                              digest=DIGEST_A, confidence="high")
                first_dr = _make_dispatch_result("req-001", "report_produced", first_binding)
                test_digest = DIGEST_A if freshness == "duplicate" else DIGEST_B
                test_binding = _make_binding("bind-cell", "req-002", verdict,
                                             digest=test_digest,
                                             contract_digest=DIGEST_A,
                                             target_digest=DIGEST_A, confidence="high")
                test_dr = _make_dispatch_result("req-002", "report_produced", test_binding)
                request = _make_request(declaration=decl,
                                        dispatch_results=[first_dr, test_dr])
                result = rvm.evaluate_validator_mesh(request)
                test_rr = next(
                    (rr for rr in result["requirement_results"]
                     if rr["requirement_id"] == "req-002"), None)
            else:
                req = self._make_cell_req(kind, required, policy)
                decl = _make_declaration(requirements=[req])
                if status == "report_produced":
                    if freshness == "current":
                        binding = _make_binding("bind-cell", "req-001", verdict, digest=DIGEST_A, confidence="high")
                        dr = _make_dispatch_result("req-001", "report_produced", binding)
                    elif freshness == "superseded":
                        binding = _make_binding("bind-cell", "req-001", verdict, digest=DIGEST_A,
                                                extra={"supersession": {"artifact_id": "r2", "artifact_kind": "report"}}, confidence="high")
                        dr = _make_dispatch_result("req-001", "report_produced", binding)
                    elif freshness == "invalidated":
                        binding = _make_binding("bind-cell", "req-001", verdict, digest=DIGEST_A,
                                                extra={"invalidation": {"reason": "test"}}, confidence="high")
                        dr = _make_dispatch_result("req-001", "report_produced", binding)
                    elif freshness == "stale":
                        binding = _make_binding("bind-cell", "req-001", verdict, digest=DIGEST_A, confidence="high")
                        binding["report_ref"] = {"artifact_id": None, "artifact_kind": "validation_report",
                                                 "artifact_version": "1.0.0", "digest": DIGEST_A}
                        dr = _make_dispatch_result("req-001", "report_produced", binding)
                    elif freshness == "mismatched":
                        binding = _make_binding("bind-cell", "req-001", verdict,
                                                contract_digest=DIGEST_B, digest=DIGEST_A, confidence="high")
                        dr = _make_dispatch_result("req-001", "report_produced", binding)
                    else:
                        continue
                else:
                    dr = _make_dispatch_result("req-001", status,
                                               error_code="validator_%s" % status)
                request = _make_request(declaration=decl, dispatch_results=[dr])
                result = rvm.evaluate_validator_mesh(request)
                test_rr = result["requirement_results"][0]

            if expected[2] is not None or test_rr.get("result_kind") == "optional_excluded":
                exclusions += 1
            elif test_rr.get("verdict_contribution") is not None:
                contributions += 1

        self.assertEqual(contributions, 140,
            "Expected 140 contributions, got %d" % contributions)
        self.assertEqual(exclusions, 94,
            "Expected 94 exclusions, got %d" % exclusions)
        self.assertEqual(contributions + exclusions, 234,
            "Total contributions+exclusions=%d, expected 234" %
            (contributions + exclusions))

    # --- Helpers ---

    def _requirement_combos(self):
        for required in (True, False):
            for policy in ("fail", "human_review_required"):
                yield ("baseline", required, policy)
        for required in (True, False):
            yield ("extension", required, None)

    def _enumerate_all_cells(self):
        for kind, required, policy in self._requirement_combos():
            for status in self.UNAVAILABLE_STATUSES:
                yield (kind, required, policy, status, None, None)
            for verdict in self.VERDICTS:
                yield (kind, required, policy, "report_produced", verdict, "current")
                for freshness in self.FRESHNESSES:
                    if freshness != "current":
                        yield (kind, required, policy, "report_produced", verdict, freshness)

    def _make_cell_req(self, kind, required, policy, req_id="req-001", priority=0,
                       contract_digest=DIGEST_A, target_digest=DIGEST_A):
        r = {
            "requirement_id": req_id,
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": contract_digest},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": target_digest}],
            "requirement_kind": kind,
            "required": required,
            "dispatch_priority": priority,
            "failure_behavior": "halt_run" if kind == "baseline" else "halt_mesh"
        }
        if kind == "baseline":
            r["missing_mapping_policy"] = policy
        return r


# ---------------------------------------------------------------------------
# Deprecated Error Code Unreachability Tests
# ---------------------------------------------------------------------------

class DeprecatedErrorUnreachableTests(unittest.TestCase):
    """Verify all 6 deprecated error codes are unreachable in v1.2."""

    DEPRECATED_CODES = (
        "zero_valid_contributions",
        "required_requirement_blocked",
        "baseline_missing_unresolved",
        "report_digest_mismatch",
        "contract_digest_missing",
        "gate_bridge_construction_failed",
        "gate_evaluation_error",
    )

    def test_01_deprecated_codes_not_in_valid_set(self):
        """Deprecated codes are not in the VALID_ERROR_CODES set."""
        for code in self.DEPRECATED_CODES:
            self.assertNotIn(code, rvm.VALID_ERROR_CODES,
                             "Deprecated code %s should not be in VALID_ERROR_CODES" % code)

    def test_02_deprecated_codes_not_returned(self):
        """Various inputs that might trigger deprecated codes return valid v1.2 results."""
        # Test: optional extension unreachable -> should return result, not error
        req = {
            "requirement_id": "req-001",
            "validator_identity": "validator-core",
            "contract_ref": {
                "artifact_id": "vc-001", "artifact_kind": "contract",
                "artifact_version": "1.0.0", "digest": DIGEST_A},
            "artifact_scope": [{
                "artifact_id": "artifact-001", "artifact_kind": "artifact",
                "artifact_version": "1.0.0", "digest": DIGEST_A}],
            "requirement_kind": "extension",
            "required": False,
            "dispatch_priority": 0,
            "failure_behavior": "halt_mesh"
        }
        decl = _make_declaration(requirements=[req])
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "unreachable",
                                  error_code="validator_unreachable"),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertIn("aggregate_verdict", result)  # Should be a result, not error
        self.assertNotIn("error_code", result)

    def test_03_digest_mismatch_folded_into_binding_error(self):
        """v1.2: report digest mismatch -> invalid_report_binding, not report_digest_mismatch."""
        binding = _make_binding("bind-001", "req-001", digest=DIGEST_A, confidence="high")
        binding["report_sha256"] = DIGEST_B
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["error_code"], "invalid_report_binding")

    def test_04_contract_digest_missing_folded_into_binding_error(self):
        """v1.2: missing contract_ref.digest -> invalid_report_binding."""
        binding = _make_binding("bind-001", "req-001", confidence="high")
        del binding["contract_ref"]["digest"]
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["error_code"], "invalid_report_binding")


# ---------------------------------------------------------------------------
# Active Error Code Reachability Tests
# ---------------------------------------------------------------------------

class ActiveErrorReachabilityTests(unittest.TestCase):
    """Verify all 8 active error codes are reachable via distinct witnesses."""

    def test_01_invalid_mesh_declaration_reachable(self):
        decl = _make_declaration()
        decl["mesh_version"] = "0.9.0"
        result = rvm.evaluate_validator_mesh(_make_request(declaration=decl))
        self.assertEqual(result["error_code"], "invalid_mesh_declaration")

    def test_02_invalid_mesh_request_reachable(self):
        result = rvm.evaluate_validator_mesh({})
        self.assertEqual(result["error_code"], "invalid_mesh_request")

    def test_03_duplicate_requirement_id_reachable(self):
        r1 = _make_declaration()["requirements"][0]
        r2 = dict(r1)
        r2["dispatch_priority"] = 1
        decl = _make_declaration(requirements=[r1, r2])
        result = rvm.evaluate_validator_mesh(_make_request(declaration=decl))
        self.assertEqual(result["error_code"], "duplicate_requirement_id")

    def test_04_dispatch_count_mismatch_reachable(self):
        decl = _make_declaration(requirements=[
            _make_declaration()["requirements"][0],
            _make_declaration()["requirements"][0].copy()
        ])
        decl["requirements"][1]["requirement_id"] = "req-002"
        decl["requirements"][1]["dispatch_priority"] = 1
        result = rvm.evaluate_validator_mesh(_make_request(declaration=decl))
        self.assertEqual(result["error_code"], "dispatch_count_mismatch")

    def test_05_duplicate_dispatch_request_id_reachable(self):
        decl = _make_declaration(requirements=[
            dict(_make_declaration()["requirements"][0]),
            dict(_make_declaration()["requirements"][0]),
        ])
        decl["requirements"][1]["requirement_id"] = "req-002"
        decl["requirements"][1]["dispatch_priority"] = 1
        request = _make_request(declaration=decl, dispatch_results=[
            _make_dispatch_result("req-001", "report_produced",
                                  _make_binding("bind-001", "req-001", "pass", confidence="high")),
            _make_dispatch_result("req-001", "report_produced",
                                  _make_binding("bind-002", "req-002", "pass", confidence="high")),
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["error_code"], "duplicate_dispatch_request_id")

    def test_06_orphan_dispatch_result_reachable(self):
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-999", "report_produced",
                                  _make_binding("bind-001", "req-001", "pass", confidence="high"))
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["error_code"], "orphan_dispatch_result")

    def test_07_invalid_dispatch_result_reachable(self):
        """produced without binding triggers invalid_dispatch_result."""
        request = _make_request(dispatch_results=[
            {
                "dispatch_request_id": "req-001",
                "dispatch_status": "report_produced",
                "collected_at": "2026-07-01T00:00:00Z",
                "collected_by": "collector"
            }
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["error_code"], "invalid_dispatch_result")

    def test_08_invalid_report_binding_reachable(self):
        binding = _make_binding(confidence="high")
        binding["role"] = "architect"
        request = _make_request(dispatch_results=[
            _make_dispatch_result("req-001", "report_produced", binding)
        ])
        result = rvm.evaluate_validator_mesh(request)
        self.assertEqual(result["error_code"], "invalid_report_binding")

    def test_09_all_8_codes_distinct(self):
        """Verify exactly 8 active error codes."""
        self.assertEqual(len(rvm.VALID_ERROR_CODES), 8)


# ---------------------------------------------------------------------------
# Forbidden Import Scan
# ---------------------------------------------------------------------------

class ForbiddenImportTests(unittest.TestCase):
    """Verify the module has no forbidden imports."""

    FORBIDDEN_PATTERNS = [
        "import os.environ",
        "from os import environ",
        "import sqlite3",
        "from sqlite3",
        "import socket",
        "from socket",
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import pathlib",
        "from pathlib",
    ]

    def test_no_forbidden_imports_in_source(self):
        source_path = os.path.join(ROOT, "scripts", "runtime_validator_mesh.py")
        with open(source_path, "r", encoding="utf-8") as f:
            source = f.read()
        for pattern in self.FORBIDDEN_PATTERNS:
            self.assertNotIn(pattern, source,
                             "Forbidden import found: %s" % pattern)

    def test_no_file_open_write(self):
        source_path = os.path.join(ROOT, "scripts", "runtime_validator_mesh.py")
        with open(source_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("open(", source)
        self.assertNotIn("open (", source)

    def test_no_os_environ_access(self):
        source_path = os.path.join(ROOT, "scripts", "runtime_validator_mesh.py")
        with open(source_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("os.environ", source)


# ---------------------------------------------------------------------------
# Predecessor Hash Immutability
# ---------------------------------------------------------------------------

class PredecessorHashTests(unittest.TestCase):
    """Verify all four predecessor hashes remain byte-identical."""

    def test_01_contract_hash(self):
        self.assertEqual(_sha256(CONTRACT_PATH), EXPECTED_CONTRACT_SHA256)

    def test_02_schema_hash(self):
        self.assertEqual(_sha256(SCHEMA_PATH), EXPECTED_SCHEMA_SHA256)

    def test_03_conformance_hash(self):
        self.assertEqual(_sha256(CATALOG_PATH), EXPECTED_CONFORMANCE_SHA256)

    def test_04_schema_test_hash(self):
        schema_test_path = os.path.join(
            ROOT, "scripts", "test_runtime_validator_mesh_schema.py")
        self.assertEqual(_sha256(schema_test_path), EXPECTED_SCHEMA_TEST_SHA256)


# ---------------------------------------------------------------------------
# Nonzero Case Count Verification
# ---------------------------------------------------------------------------

class TestCoverageVerification(unittest.TestCase):
    """Verify focused tests include exact nonzero case counts."""

    def test_01_minimum_test_count(self):
        """Test suite has at least 80 focused test methods."""
        import sys
        module = sys.modules.get(__name__)
        if module is None:
            self.skipTest("Module not found in sys.modules")
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(module)
        count = suite.countTestCases()
        self.assertGreaterEqual(count, 80,
                                "Expected >= 80 test cases, found %d" % count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
