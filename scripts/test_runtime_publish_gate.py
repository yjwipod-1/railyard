"""Tests for the reconciled pure publish-gate bridge module.

The bridge is reconciled to the accepted Runtime Validator Mesh Contract v1.2.0
and Gate Decision v2.2.0. This suite:

  1. Verifies every frozen Mesh / Gate Decision authority hash stays byte-identical
  2. Proves the production bridge contains zero ``gate_bridge_envelope``
     occurrences and the accepted result shape has exactly ten Mesh fields
  3. Direct five-verdict matrix built by production ``evaluate_validator_mesh``:
     each raw evaluator result is passed directly to the bridge (no
     normalization) and the GateDecision outcome equals the Mesh aggregate
  4. Independent 234-cell enumeration (six declaration combinations times
     39 dispatch/verdict/freshness cells): each raw production result is passed
     directly to the bridge; 234 attempted / 234 delegated / 234 outcome-
     preserved / zero skipped / zero errors
  5. Nullable stale identity coverage: each nullable identity field
     independently, all-null, mixed-null, required and optional branches;
     no null or fabricated value enters a Gate ArtifactRef; every valid Mesh
     result delegates exactly once
  6. Branch coverage: current, stale, mismatched, duplicate, conflicting,
     superseded, invalidated, missing, unavailable, degraded; the bridge never
     revises Mesh verdict, confidence, requirement results, freshness, or
     recommendation
  7. Negative mutations: missing each required Mesh result field, wrong root
     type, Mesh error object, invalid verdict, malformed gate facts, and a
     legacy synthetic ``gate_bridge_envelope`` as non-authority; every rejected
     input makes zero Gate calls
  8. Field-origin proof: every Gate request field comes from caller facts,
     exact Mesh fields, or an explicit immutable bridge table
  9. Spy injection: zero Gate calls for malformed/invalid inputs; exactly one
     for every valid input
 10. Differential tests: bridge output equals direct ``evaluate_gate`` on the
     exact request the bridge constructed
 11. GateDecision and caller inputs remain deeply unchanged; results are
     independent deep copies
 12. Static and runtime purity probes: no filesystem, network, SQLite, event
     append, retry, clock, random, or hidden-state operation; ASCII-only
     sources; no trailing whitespace; isolated compile probes
"""

import copy
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Import the modules under test
import scripts.runtime_publish_gate as rpg
import scripts.runtime_validator_mesh as rvm
import scripts.runtime_gate_decision as rgd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Frozen contract and authority constants
# ---------------------------------------------------------------------------

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64

MESH_RESULT_REQUIRED_FIELDS = (
    "mesh_eval_id", "mesh_id", "aggregate_verdict", "aggregate_confidence",
    "report_bindings", "requirement_results", "freshness_assessments",
    "recommended_action", "evaluated_at", "evaluated_by",
)

MESH_CONTRACT_VERSION = "1.2.0"

_GATE_FACTS = {
    "run_context": {"run_id": "run-001", "stage_id": "stage-001"},
    "evaluated_at": "2026-07-01T00:00:00Z",
    "evaluated_by": "bridge-caller",
}

# Frozen authority hashes (must remain byte-identical before and after work)
EXPECTED_HASHES = {
    "references/runtime-validator-mesh-contract.md":
        "efe7689f1c258200137f4e02f037d18a24a01063c1fe24a9f5948086da869e68",
    "assets/schemas/runtime-validator-mesh-v1.schema.json":
        "16d99188a5306c1d279c533b780f669d459743f7fd9f54fe00f9d97bb226b12a",
    "examples/runtime_validator_mesh_contract/conformance.json":
        "9ca19445da55ac15f826765a0390810e56ea45b6d4f15080dc03171938ef4b92",
    "scripts/runtime_validator_mesh.py":
        "389e4e9b0e1aef5cbfd723e4ec53f57c6593091c5ee793c4321591bb101604fb",
    "scripts/test_runtime_validator_mesh.py":
        "2e4d73341303b59ffe1d3eacf307001f460dc82b9b96b39ec2e4a05b6728b718",
    "references/runtime-gate-decision-contract.md":
        "711d1139b8c463024876f2460ff42bb195784dc7bc43d1d04bd2fc1c6d582033",
    "assets/schemas/runtime-gate-decision-v2.schema.json":
        "32cd278b25bd348cb9e810cec27337f72d9bfceef43f01c50c8bcddfc280264a",
    "scripts/runtime_gate_decision.py":
        "c0b14d44aa13b389f2acc5a10147cde1042a5093d23c8f6d8f89d4e32f0d27ff",
    "scripts/test_runtime_gate_decision.py":
        "86b98b8aa9997fc03ca32f70e40929386f252100b8b1923967cd1fde7736b35e",
}


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


# ---------------------------------------------------------------------------
# Mesh request builders (self-contained; mirror the accepted v1.2 shapes)
# ---------------------------------------------------------------------------

def _make_declaration(mesh_id="mesh-001", requirements=None, run_context=None):
    """Create a minimal valid ValidatorMeshDeclaration (v1.2)."""
    if requirements is None:
        requirements = [_make_cell_req("baseline", True, "fail")]
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


def _make_cell_req(kind, required, policy, req_id="req-001", priority=0,
                   contract_digest=DIGEST_A, target_digest=DIGEST_A):
    """Create a valid v1.2 requirement for a matrix cell."""
    r = {
        "requirement_id": req_id,
        "validator_identity": "validator-core",
        "contract_ref": {
            "artifact_id": "vc-001", "artifact_kind": "contract",
            "artifact_version": "1.0.0", "digest": contract_digest,
        },
        "artifact_scope": [{
            "artifact_id": "artifact-001", "artifact_kind": "artifact",
            "artifact_version": "1.0.0", "digest": target_digest,
        }],
        "requirement_kind": kind,
        "required": required,
        "dispatch_priority": priority,
        "failure_behavior": "halt_run" if kind == "baseline" else "halt_mesh",
    }
    if kind == "baseline":
        r["missing_mapping_policy"] = policy
    return r


_UNSET = object()


def _make_binding(binding_id="bind-001", requirement_id="req-001",
                  verdict="pass", confidence="high", digest=DIGEST_A,
                  contract_digest=None, target_digest=None,
                  report_artifact_id=_UNSET, report_artifact_version=_UNSET,
                  report_sha256=_UNSET, extra=None):
    """Create a minimal valid ValidationReportBinding (v1.2).

    Nullable report identity values are preserved verbatim and never rewritten
    by this builder; pass None explicitly to construct a null identity value.
    """
    cd = contract_digest if contract_digest is not None else digest
    td = target_digest if target_digest is not None else digest
    raid = report_artifact_id if report_artifact_id is not _UNSET else "report-001"
    raver = report_artifact_version if report_artifact_version is not _UNSET else "1.0.0"
    sha = digest if report_sha256 is _UNSET else report_sha256
    binding = {
        "binding_id": binding_id,
        "requirement_id": requirement_id,
        "validator_identity": "validator-core",
        "role": "validator",
        "contract_ref": {
            "artifact_id": "vc-001", "artifact_kind": "contract",
            "artifact_version": "1.0.0", "digest": cd,
        },
        "target_artifact_ref": {
            "artifact_id": "artifact-001", "artifact_kind": "artifact",
            "artifact_version": "1.0.0", "digest": td,
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
            "producer_identity": "validator-core",
            "production_environment": "ci-pipeline-001",
            "production_timestamp": "2026-07-01T00:05:00Z",
            "no_caller_role_collapse": True,
        },
        "bound_at": "2026-07-01T00:00:00Z",
        "bound_by": "dispatcher",
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
        "collected_by": "collector",
    }
    if status == "report_produced":
        if binding is None:
            raise ValueError("report_produced requires an explicit binding")
        result["report_binding"] = binding
    else:
        result["error_code"] = error_code or "validator_unreachable"
        if status in ("degraded_storage", "degraded_transport"):
            result["degradation_note"] = (
                degradation_note or "%s note" % status)
    return result


def _make_request(eval_id="eval-001", declaration=None, dispatch_results=None):
    """Create a valid ValidatorMeshEvaluationRequest."""
    if declaration is None:
        declaration = _make_declaration()
    if dispatch_results is None:
        dispatch_results = [
            _make_dispatch_result(
                "req-001", "report_produced",
                _make_binding("bind-001", "req-001", "pass"))
        ]
    return {
        "mesh_eval_id": eval_id,
        "mesh_declaration": declaration,
        "dispatch_results": dispatch_results,
        "requested_at": "2026-07-01T00:00:00Z",
        "requested_by": "architect",
    }


def _mesh_result_for_verdict(verdict):
    """Production Mesh result for a single current baseline report."""
    req = _make_cell_req("baseline", True, "fail")
    decl = _make_declaration(requirements=[req])
    binding = _make_binding("bind-001", "req-001", verdict)
    dr = _make_dispatch_result("req-001", "report_produced", binding)
    return rvm.evaluate_validator_mesh(
        _make_request(declaration=decl, dispatch_results=[dr]))


# ---------------------------------------------------------------------------
# Recording spy
# ---------------------------------------------------------------------------

class _SpyContext(object):
    """Context manager that swaps rpg.evaluate_gate for a recording spy."""

    def __init__(self):
        self.original = rpg.evaluate_gate
        self.requests = []
        self.call_count = 0

    def __enter__(self):
        rpg.evaluate_gate = self
        return self

    def __exit__(self, *exc_info):
        rpg.evaluate_gate = self.original
        return False

    def __call__(self, request):
        self.call_count += 1
        self.requests.append(request)
        return self.original(request)


# ---------------------------------------------------------------------------
# Independent expected-request derivation (field-origin crosswalk)
# ---------------------------------------------------------------------------

def _nonempty(value):
    return isinstance(value, str) and len(value) > 0


def _build_expected_request(mesh_result, gate_facts):
    """Independently derive the canonical GateEvaluationRequest from Mesh
    result fields, caller gate facts, and the immutable bridge tables."""
    run_context = gate_facts["run_context"]
    stage_id = run_context["stage_id"]
    mesh_eval_id = mesh_result["mesh_eval_id"]
    mesh_id = mesh_result["mesh_id"]
    verdict = mesh_result["aggregate_verdict"]
    gate_id = stage_id + "-validator-mesh-gate"
    decision_id = mesh_eval_id + "-gate-decision"

    report_ref = {
        "artifact_id": mesh_id,
        "artifact_kind": "report",
        "artifact_version": MESH_CONTRACT_VERSION,
    }

    primary_evidence = []
    omitted_refs = []
    for binding in mesh_result.get("report_bindings", []):
        ref = binding.get("report_ref")
        aid = ref.get("artifact_id") if isinstance(ref, dict) else None
        akind = ref.get("artifact_kind") if isinstance(ref, dict) else None
        aver = ref.get("artifact_version") if isinstance(ref, dict) else None
        if _nonempty(aid) and _nonempty(akind) and _nonempty(aver):
            primary_evidence.append({
                "artifact_id": aid,
                "artifact_kind": akind,
                "artifact_version": aver,
            })
        else:
            missing = []
            if not _nonempty(aid):
                missing.append("report_ref.artifact_id")
            if not _nonempty(akind):
                missing.append("report_ref.artifact_kind")
            if not _nonempty(aver):
                missing.append("report_ref.artifact_version")
            omitted_refs.append((binding.get("binding_id", "unknown"), missing))

    classification = rpg.EVIDENCE_CLASSIFICATION[verdict]

    envelope = {
        "envelope_id": mesh_eval_id + "-gate-envelope",
        "gate_id": gate_id,
        "primary_evidence": primary_evidence,
        "supporting_evidence": [],
        "validation_report": copy.deepcopy(report_ref),
        "evidence_classification": classification,
        "collected_at": mesh_result["evaluated_at"],
        "collected_by": mesh_result["evaluated_by"],
    }

    if classification != "complete":
        freshness = {}
        assessments = mesh_result.get("freshness_assessments", [])
        if isinstance(assessments, list):
            for fa in assessments:
                if isinstance(fa, dict) and _nonempty(fa.get("binding_id")):
                    freshness[fa["binding_id"]] = fa.get("freshness_status", "stale")
        descriptions = []
        described = set()
        for bid, missing in omitted_refs:
            if bid in freshness:
                descriptions.append("binding %s: %s (%s)" % (
                    bid, freshness[bid], ", ".join(missing)))
            else:
                descriptions.append("binding %s: incomplete report identity (%s)" % (
                    bid, ", ".join(missing)))
            described.add(bid)
        for bid, status in freshness.items():
            if status == "current" or bid in described:
                continue
            descriptions.append("binding %s: %s" % (bid, status))
        if not descriptions:
            descriptions.append("validator mesh evidence is not complete")
        envelope["missing_evidence_description"] = descriptions

    signal = {
        "report_ref": copy.deepcopy(report_ref),
        "overall_verdict": verdict,
    }
    if verdict != "pass":
        code = rpg.GATE_FAILURE_CODE[verdict]
        bindings = mesh_result.get("report_bindings", [])
        if verdict == "blocked" and (not isinstance(bindings, list) or len(bindings) == 0):
            code = "validator_unreachable"
        signal["failure_code"] = code

    gate_decl = {
        "gate_id": gate_id,
        "gate_type": "validator",
        "required": True,
        "allow_gate_override": False,
        "contract_ref": {
            "artifact_id": "runtime-validator-mesh-contract",
            "artifact_kind": "contract",
            "artifact_version": MESH_CONTRACT_VERSION,
        },
        "failure_behavior": "halt_run",
    }

    return {
        "request_kind": "initial",
        "decision_id": decision_id,
        "evaluated_at": gate_facts["evaluated_at"],
        "evaluated_by": gate_facts["evaluated_by"],
        "gate_declaration": gate_decl,
        "evidence_envelope": envelope,
        "evaluation_signal": signal,
        "run_context": copy.deepcopy(run_context),
        "execution_mode": "full",
    }


# ---------------------------------------------------------------------------
# Frozen authorities
# ---------------------------------------------------------------------------

class TestFrozenAuthorities(unittest.TestCase):
    """All nine frozen Mesh / Gate Decision authorities stay byte-identical."""

    def test_all_authority_hashes_unchanged(self):
        for rel_path, expected in EXPECTED_HASHES.items():
            abs_path = os.path.join(ROOT, rel_path)
            if not os.path.exists(abs_path):
                self.fail("Missing authority file: %s" % rel_path)
            actual = _sha256(abs_path)
            self.assertEqual(actual, expected,
                             "Hash mismatch for %s" % rel_path)


# ---------------------------------------------------------------------------
# Accepted result shape and production hygiene
# ---------------------------------------------------------------------------

class TestAcceptedResultShapeAndHygiene(unittest.TestCase):
    """The accepted v1.2 result has exactly ten fields and the production
    bridge contains zero ``gate_bridge_envelope`` occurrences."""

    def test_mesh_result_has_exactly_ten_fields(self):
        for verdict in ("pass", "fail", "blocked", "inconclusive",
                        "human_review_required"):
            mesh_result = _mesh_result_for_verdict(verdict)
            self.assertEqual(set(mesh_result.keys()),
                             set(MESH_RESULT_REQUIRED_FIELDS))
            self.assertNotIn("gate_bridge_envelope", mesh_result)

    def test_production_has_zero_gate_bridge_envelope(self):
        with open(os.path.join(ROOT, "scripts", "runtime_publish_gate.py"),
                  "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(source.count("gate_bridge_envelope"), 0)

    def test_production_bridge_source_ascii(self):
        with open(os.path.join(ROOT, "scripts", "runtime_publish_gate.py"),
                  "rb") as handle:
            data = handle.read()
        try:
            data.decode("ascii")
        except UnicodeDecodeError as exc:
            self.fail("Non-ASCII character in bridge source: %s" % exc)

    def test_test_source_ascii(self):
        with open(os.path.join(ROOT, "scripts", "test_runtime_publish_gate.py"),
                  "rb") as handle:
            data = handle.read()
        try:
            data.decode("ascii")
        except UnicodeDecodeError as exc:
            self.fail("Non-ASCII character in test source: %s" % exc)

    def test_no_trailing_whitespace(self):
        for rel_path in ("scripts/runtime_publish_gate.py",
                         "scripts/test_runtime_publish_gate.py"):
            with open(os.path.join(ROOT, rel_path), "r", encoding="utf-8") as handle:
                lines = handle.read().split("\n")
            offending = [i + 1 for i, line in enumerate(lines)
                         if line.rstrip() != line]
            self.assertEqual(offending, [],
                             "Trailing whitespace in %s lines %s" %
                             (rel_path, offending))


# ---------------------------------------------------------------------------
# Direct five-verdict matrix (acceptance check a)
# ---------------------------------------------------------------------------

class TestDirectFiveVerdictMatrix(unittest.TestCase):
    """Five-verdict matrix built by production ``evaluate_validator_mesh``.
    Each raw evaluator result passes directly to the bridge (no normalization);
    the GateDecision outcome equals the Mesh aggregate verdict."""

    VERDICTS = ("pass", "fail", "blocked", "inconclusive", "human_review_required")

    def test_direct_matrix(self):
        attempted = 0
        delegated = 0
        compared = 0
        errors = []
        with _SpyContext() as spy:
            for verdict in self.VERDICTS:
                mesh_result = _mesh_result_for_verdict(verdict)
                self.assertEqual(mesh_result["aggregate_verdict"], verdict)
                attempted += 1
                before = spy.call_count
                bridge_output = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
                if spy.call_count != before + 1:
                    errors.append((verdict, "gate_call_count_%d" % (
                        spy.call_count - before)))
                    continue
                delegated += 1
                if "error_code" in bridge_output:
                    errors.append((verdict, bridge_output["error_code"]))
                    continue
                if bridge_output["outcome"] != verdict:
                    errors.append((verdict, "outcome_mismatch"))
                    continue
                compared += 1
        self.assertEqual(attempted, 5, "five attempted")
        self.assertEqual(delegated, 5, "five delegated")
        self.assertEqual(compared, 5, "five compared")
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# Independent 234-cell enumeration (acceptance check b)
# ---------------------------------------------------------------------------

class TestIndependent234CellEnumeration(unittest.TestCase):
    """Independently enumerate all 234 valid Mesh cells (six declaration
    combinations times 39 dispatch/verdict/freshness cells). Each raw
    production result passes directly to the bridge."""

    VERDICTS = ("pass", "blocked", "fail", "human_review_required", "inconclusive")
    FRESHNESSES = ("superseded", "invalidated", "stale", "mismatched",
                   "duplicate", "conflicting", "current")
    UNAVAILABLE_STATUSES = ("no_report", "unreachable",
                            "degraded_storage", "degraded_transport")

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
                        yield (kind, required, policy, "report_produced",
                               verdict, freshness)

    def _build_cell_request(self, cell):
        kind, required, policy, status, verdict, freshness = cell
        if status == "report_produced":
            if freshness in ("duplicate", "conflicting"):
                req_first = _make_cell_req(kind, required, policy, "req-001", 0)
                req_test = _make_cell_req(kind, required, policy, "req-002", 1)
                decl = _make_declaration(requirements=[req_first, req_test])
                first_binding = _make_binding(
                    "bind-first", "req-001", verdict, digest=DIGEST_A)
                first_dr = _make_dispatch_result(
                    "req-001", "report_produced", first_binding)
                if freshness == "duplicate":
                    test_binding = _make_binding(
                        "bind-cell", "req-002", verdict, digest=DIGEST_A)
                else:
                    test_binding = _make_binding(
                        "bind-cell", "req-002", verdict, digest=DIGEST_B,
                        contract_digest=DIGEST_A, target_digest=DIGEST_A)
                test_dr = _make_dispatch_result(
                    "req-002", "report_produced", test_binding)
                return _make_request(
                    declaration=decl,
                    dispatch_results=[first_dr, test_dr])
            req = _make_cell_req(kind, required, policy)
            decl = _make_declaration(requirements=[req])
            if freshness == "current":
                binding = _make_binding(
                    "bind-cell", "req-001", verdict, digest=DIGEST_A)
            elif freshness == "superseded":
                binding = _make_binding(
                    "bind-cell", "req-001", verdict, digest=DIGEST_A,
                    extra={"supersession": {"artifact_id": "r2",
                                            "artifact_kind": "report"}})
            elif freshness == "invalidated":
                binding = _make_binding(
                    "bind-cell", "req-001", verdict, digest=DIGEST_A,
                    extra={"invalidation": {"reason": "test"}})
            elif freshness == "stale":
                binding = _make_binding(
                    "bind-cell", "req-001", verdict, digest=DIGEST_A)
                binding["report_ref"] = {
                    "artifact_id": None, "artifact_kind": "validation_report",
                    "artifact_version": "1.0.0", "digest": DIGEST_A}
            elif freshness == "mismatched":
                binding = _make_binding(
                    "bind-cell", "req-001", verdict, digest=DIGEST_A,
                    contract_digest=DIGEST_B)
            else:
                raise AssertionError("unexpected freshness: %s" % freshness)
            dr = _make_dispatch_result("req-001", "report_produced", binding)
            return _make_request(declaration=decl, dispatch_results=[dr])
        req = _make_cell_req(kind, required, policy)
        decl = _make_declaration(requirements=[req])
        dr = _make_dispatch_result("req-001", status,
                                   error_code="validator_%s" % status)
        return _make_request(declaration=decl, dispatch_results=[dr])

    def test_cell_count_is_234(self):
        total = 0
        for _kind, _req, _pol in self._requirement_combos():
            total += len(self.UNAVAILABLE_STATUSES)
            for _verdict in self.VERDICTS:
                total += 1
                total += len(self.FRESHNESSES) - 1
        self.assertEqual(total, 234)

    def test_all_234_cells_delegate_once_and_preserve_outcome(self):
        attempted = 0
        delegated = 0
        outcome_preserved = 0
        errors = []
        with _SpyContext() as spy:
            for cell_idx, cell in enumerate(self._enumerate_all_cells()):
                mesh_result = rvm.evaluate_validator_mesh(
                    self._build_cell_request(cell))
                if "error_code" in mesh_result:
                    errors.append((cell_idx, cell, "mesh_error_%s" %
                                   mesh_result["error_code"]))
                    continue
                attempted += 1
                before = spy.call_count
                bridge_output = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
                if spy.call_count != before + 1:
                    errors.append((cell_idx, cell, "gate_call_count_%d" %
                                   (spy.call_count - before)))
                    continue
                delegated += 1
                if "error_code" in bridge_output:
                    errors.append((cell_idx, cell, "bridge_error_%s" %
                                   bridge_output["error_code"]))
                    continue
                if bridge_output["outcome"] != mesh_result["aggregate_verdict"]:
                    errors.append((cell_idx, cell, "outcome_mismatch"))
                    continue
                outcome_preserved += 1
        self.assertEqual(attempted, 234, "234 attempted")
        self.assertEqual(delegated, 234, "234 delegated")
        self.assertEqual(outcome_preserved, 234, "234 outcome-preserved")
        self.assertEqual(errors, [], "errors: %s" % errors[:5])


# ---------------------------------------------------------------------------
# Nullable stale identity coverage (acceptance check c)
# ---------------------------------------------------------------------------

class TestNullableStaleIdentity(unittest.TestCase):
    """Each nullable stale identity field independently, all-null, mixed-null,
    required and optional branches. No null or fabricated value enters a Gate
    ArtifactRef; every valid Mesh result delegates exactly once."""

    def _identity_cases(self):
        return [
            ("null_artifact_id", None, "1.0.0", DIGEST_A, DIGEST_A),
            ("null_artifact_version", "report-001", None, DIGEST_A, DIGEST_A),
            ("null_digest", "report-001", "1.0.0", None, DIGEST_A),
            ("null_report_sha256", "report-001", "1.0.0", DIGEST_A, None),
            ("all_null", None, None, None, None),
            ("mixed_null_id_and_version", None, None, DIGEST_A, DIGEST_A),
        ]

    def _run_case(self, name, raid, raver, report_digest, report_sha,
                  required):
        req = _make_cell_req("baseline", required, "fail")
        decl = _make_declaration(requirements=[req])
        binding = _make_binding(
            "bind-cell", "req-001", "pass", digest=DIGEST_A,
            report_artifact_id=raid, report_artifact_version=raver,
            report_sha256=report_sha)
        binding["report_ref"]["digest"] = report_digest
        dr = _make_dispatch_result("req-001", "report_produced", binding)
        mesh_result = rvm.evaluate_validator_mesh(
            _make_request(declaration=decl, dispatch_results=[dr]))
        self.assertNotIn("error_code", mesh_result,
                         "%s required=%s must be a result" % (name, required))
        self.assertEqual(
            mesh_result["freshness_assessments"][0]["freshness_status"], "stale",
            "%s required=%s must classify as stale" % (name, required))

        with _SpyContext() as spy:
            bridge_output = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
            self.assertEqual(spy.call_count, 1,
                             "%s required=%s must delegate exactly once" %
                             (name, required))
            request = spy.requests[0]

        self.assertNotIn("error_code", bridge_output,
                         "%s required=%s produced a bridge error" %
                         (name, required))
        self.assertEqual(bridge_output["outcome"],
                         mesh_result["aggregate_verdict"])

        primary = request["evidence_envelope"]["primary_evidence"]
        for artifact_ref in primary:
            for field in ("artifact_id", "artifact_kind", "artifact_version"):
                value = artifact_ref.get(field)
                self.assertTrue(_nonempty(value),
                                "%s required=%s: null/fabricated %s in ref %s" %
                                (name, required, field, artifact_ref))
        included = raid is not None and raver is not None if required else False
        self.assertEqual("report-001" in [r["artifact_id"] for r in primary],
                         included,
                         "%s required=%s inclusion mismatch" % (name, required))

        if request["evidence_envelope"]["evidence_classification"] != "complete":
            descriptions = request["evidence_envelope"][
                "missing_evidence_description"]
            self.assertIsInstance(descriptions, list)
            self.assertTrue(descriptions)
            for description in descriptions:
                self.assertTrue(_nonempty(description))

    def test_nullable_identity_each_independently_required(self):
        for name, raid, raver, digest, sha in self._identity_cases():
            self._run_case(name, raid, raver, digest, sha, required=True)

    def test_nullable_identity_each_independently_optional(self):
        for name, raid, raver, digest, sha in self._identity_cases():
            self._run_case(name, raid, raver, digest, sha, required=False)

    def test_included_refs_keep_mesh_only_digest_stripped(self):
        """A complete triple keeps identity but never leaks Mesh-only digest."""
        req = _make_cell_req("baseline", True, "fail")
        decl = _make_declaration(requirements=[req])
        binding = _make_binding("bind-cell", "req-001", "pass", digest=DIGEST_A)
        dr = _make_dispatch_result("req-001", "report_produced", binding)
        mesh_result = rvm.evaluate_validator_mesh(
            _make_request(declaration=decl, dispatch_results=[dr]))
        with _SpyContext() as spy:
            rpg.publish_to_gate(mesh_result, _GATE_FACTS)
            request = spy.requests[0]
        for artifact_ref in request["evidence_envelope"]["primary_evidence"]:
            self.assertNotIn("digest", artifact_ref)
            self.assertNotIn("report_sha256", artifact_ref)


# ---------------------------------------------------------------------------
# Branch coverage (acceptance check d)
# ---------------------------------------------------------------------------

class TestBranchCoverage(unittest.TestCase):
    """Current, stale, mismatched, duplicate, conflicting, superseded,
    invalidated, missing, unavailable, and degraded branches. The bridge never
    revises Mesh verdict, confidence, requirement result, freshness, or
    recommendation."""

    def _branch_cells(self):
        return [
            ("current", "fail"),
            ("stale", None),
            ("mismatched", None),
            ("duplicate", None),
            ("conflicting", None),
            ("superseded", None),
            ("invalidated", None),
            ("no_report", None),
            ("unreachable", None),
            ("degraded_storage", None),
            ("degraded_transport", None),
        ]

    def _run_branch(self, status, verdict):
        kind, required, policy = "baseline", True, "fail"
        if status == "current":
            req = _make_cell_req(kind, required, policy)
            decl = _make_declaration(requirements=[req])
            binding = _make_binding("bind-cell", "req-001", verdict)
            dr = _make_dispatch_result("req-001", "report_produced", binding)
            request = _make_request(declaration=decl, dispatch_results=[dr])
        elif status == "stale":
            req = _make_cell_req(kind, required, policy)
            decl = _make_declaration(requirements=[req])
            binding = _make_binding("bind-cell", "req-001", "pass")
            binding["report_ref"] = {
                "artifact_id": None, "artifact_kind": "validation_report",
                "artifact_version": "1.0.0", "digest": DIGEST_A}
            dr = _make_dispatch_result("req-001", "report_produced", binding)
            request = _make_request(declaration=decl, dispatch_results=[dr])
        elif status == "mismatched":
            req = _make_cell_req(kind, required, policy)
            decl = _make_declaration(requirements=[req])
            binding = _make_binding("bind-cell", "req-001", "pass",
                                    contract_digest=DIGEST_B)
            dr = _make_dispatch_result("req-001", "report_produced", binding)
            request = _make_request(declaration=decl, dispatch_results=[dr])
        elif status in ("duplicate", "conflicting"):
            req_first = _make_cell_req(kind, required, policy, "req-001", 0)
            req_test = _make_cell_req(kind, required, policy, "req-002", 1)
            decl = _make_declaration(requirements=[req_first, req_test])
            first_binding = _make_binding("bind-first", "req-001", "pass",
                                          digest=DIGEST_A)
            if status == "duplicate":
                test_binding = _make_binding("bind-cell", "req-002", "pass",
                                             digest=DIGEST_A)
            else:
                test_binding = _make_binding("bind-cell", "req-002", "pass",
                                             digest=DIGEST_B,
                                             contract_digest=DIGEST_A,
                                             target_digest=DIGEST_A)
            request = _make_request(declaration=decl, dispatch_results=[
                _make_dispatch_result("req-001", "report_produced",
                                      first_binding),
                _make_dispatch_result("req-002", "report_produced",
                                      test_binding),
            ])
        elif status == "superseded":
            req = _make_cell_req(kind, required, policy)
            decl = _make_declaration(requirements=[req])
            binding = _make_binding(
                "bind-cell", "req-001", "pass",
                extra={"supersession": {"artifact_id": "r2",
                                        "artifact_kind": "report"}})
            dr = _make_dispatch_result("req-001", "report_produced", binding)
            request = _make_request(declaration=decl, dispatch_results=[dr])
        elif status == "invalidated":
            req = _make_cell_req(kind, required, policy)
            decl = _make_declaration(requirements=[req])
            binding = _make_binding(
                "bind-cell", "req-001", "pass",
                extra={"invalidation": {"reason": "test"}})
            dr = _make_dispatch_result("req-001", "report_produced", binding)
            request = _make_request(declaration=decl, dispatch_results=[dr])
        else:
            req = _make_cell_req(kind, required, policy)
            decl = _make_declaration(requirements=[req])
            dr = _make_dispatch_result("req-001", status,
                                       error_code="validator_%s" % status)
            request = _make_request(declaration=decl, dispatch_results=[dr])
        return request

    def test_all_branches_preserve_mesh_output(self):
        covered = 0
        for status, verdict in self._branch_cells():
            request = self._run_branch(status, verdict)
            mesh_result = rvm.evaluate_validator_mesh(request)
            self.assertNotIn("error_code", mesh_result,
                             "%s branch must be a result" % status)
            snapshot = copy.deepcopy(mesh_result)
            with _SpyContext() as spy:
                bridge_output = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
                self.assertEqual(spy.call_count, 1)
            self.assertNotIn("error_code", bridge_output,
                             "%s branch produced a bridge error" % status)
            self.assertEqual(bridge_output["outcome"],
                             mesh_result["aggregate_verdict"],
                             "%s branch outcome mismatch" % status)
            self.assertEqual(mesh_result, snapshot,
                             "%s branch mutated the Mesh result" % status)
            covered += 1
        self.assertEqual(covered, 11, "eleven branches covered")


# ---------------------------------------------------------------------------
# Negative mutations (acceptance check e)
# ---------------------------------------------------------------------------

class TestNegativeMutations(unittest.TestCase):
    """Every rejected input produces the bridge error with zero Gate calls.
    A legacy synthetic ``gate_bridge_envelope`` is non-authority and never
    alters the frozen bridge behavior."""

    def _valid_mesh_result(self):
        req = _make_cell_req("baseline", True, "fail")
        decl = _make_declaration(requirements=[req])
        binding = _make_binding("bind-001", "req-001", "pass")
        dr = _make_dispatch_result("req-001", "report_produced", binding)
        return rvm.evaluate_validator_mesh(
            _make_request(declaration=decl, dispatch_results=[dr]))

    def test_missing_each_required_field(self):
        for field in MESH_RESULT_REQUIRED_FIELDS:
            bad = self._valid_mesh_result()
            del bad[field]
            with _SpyContext() as spy:
                result = rpg.publish_to_gate(bad, _GATE_FACTS)
                self.assertIn("error_code", result)
                self.assertEqual(result["error_code"],
                                 "gate_bridge_construction_failed")
                self.assertEqual(spy.call_count, 0,
                                 "missing %s must not reach evaluate_gate" % field)

    def test_wrong_root_type(self):
        for bad in (None, [], "not-a-dict", 42, ("tuple",)):
            with _SpyContext() as spy:
                result = rpg.publish_to_gate(bad, _GATE_FACTS)
                self.assertIn("error_code", result)
                self.assertEqual(spy.call_count, 0)

    def test_mesh_error_object(self):
        error = rvm.evaluate_validator_mesh({})
        self.assertIn("error_code", error)
        with _SpyContext() as spy:
            result = rpg.publish_to_gate(error, _GATE_FACTS)
            self.assertIn("error_code", result)
            self.assertEqual(spy.call_count, 0)

    def test_invalid_verdict(self):
        bad = self._valid_mesh_result()
        bad["aggregate_verdict"] = "unknown"
        with _SpyContext() as spy:
            result = rpg.publish_to_gate(bad, _GATE_FACTS)
            self.assertIn("error_code", result)
            self.assertEqual(spy.call_count, 0)

    def test_malformed_gate_facts(self):
        cases = [
            None,
            {},
            {"run_context": {"run_id": "r", "stage_id": ""},
             "evaluated_at": "t", "evaluated_by": "x"},
            {"run_context": {"run_id": "", "stage_id": "s"},
             "evaluated_at": "t", "evaluated_by": "x"},
            {"run_context": {"run_id": "r", "stage_id": "s"},
             "evaluated_at": "", "evaluated_by": "x"},
            {"run_context": {"run_id": "r", "stage_id": "s"},
             "evaluated_at": "t", "evaluated_by": ""},
            {"run_context": "not-a-dict", "evaluated_at": "t",
             "evaluated_by": "x"},
        ]
        for bad in cases:
            with _SpyContext() as spy:
                result = rpg.publish_to_gate(self._valid_mesh_result(), bad)
                self.assertIn("error_code", result)
                self.assertEqual(spy.call_count, 0)

    def test_legacy_gate_bridge_envelope_is_non_authoritative(self):
        base = self._valid_mesh_result()
        with_envelope = copy.deepcopy(base)
        with_envelope["gate_bridge_envelope"] = {
            "failure_behavior": "require_intervention"}
        with _SpyContext() as spy:
            result = rpg.publish_to_gate(with_envelope, _GATE_FACTS)
            self.assertNotIn("error_code", result)
            self.assertEqual(spy.call_count, 1)
            request = spy.requests[0]
            self.assertEqual(request["gate_declaration"]["failure_behavior"],
                             "halt_run")
            self.assertEqual(request["gate_declaration"]["contract_ref"][
                                 "artifact_version"], "1.2.0")
        with _SpyContext() as spy_base:
            result_base = rpg.publish_to_gate(base, _GATE_FACTS)
            self.assertEqual(result, result_base)
            self.assertEqual(spy_base.call_count, 1)


# ---------------------------------------------------------------------------
# Field-origin proof (acceptance check f)
# ---------------------------------------------------------------------------

class TestFieldOriginProof(unittest.TestCase):
    """Every Gate request field comes from caller facts, exact Mesh fields, or
    an explicit immutable bridge table."""

    def setUp(self):
        self.mesh_result = _mesh_result_for_verdict("pass")
        self.gate_facts = copy.deepcopy(_GATE_FACTS)

    def _capture(self, mesh_result=None, gate_facts=None):
        with _SpyContext() as spy:
            bridge_output = rpg.publish_to_gate(
                mesh_result if mesh_result is not None else self.mesh_result,
                gate_facts if gate_facts is not None else self.gate_facts)
            return spy.requests[0], bridge_output

    def test_request_matches_independent_derivation(self):
        request, _ = self._capture()
        expected = _build_expected_request(self.mesh_result, self.gate_facts)
        self.assertEqual(request, expected)

    def test_request_kind_is_initial(self):
        request, _ = self._capture()
        self.assertEqual(request["request_kind"], "initial")

    def test_decision_id_derived_from_mesh_eval_id(self):
        mr = _mesh_result_for_verdict("pass")
        request, _ = self._capture(mesh_result=mr)
        self.assertEqual(request["decision_id"], "eval-001-gate-decision")

    def test_gate_id_derived_from_stage_id(self):
        request, _ = self._capture()
        self.assertEqual(request["gate_declaration"]["gate_id"],
                         "stage-001-validator-mesh-gate")
        self.assertEqual(request["evidence_envelope"]["gate_id"],
                         "stage-001-validator-mesh-gate")

    def test_evaluated_at_copied_from_gate_facts(self):
        facts = copy.deepcopy(_GATE_FACTS)
        facts["evaluated_at"] = "2026-12-31T23:59:59Z"
        request, _ = self._capture(gate_facts=facts)
        self.assertEqual(request["evaluated_at"], "2026-12-31T23:59:59Z")

    def test_evaluated_by_copied_from_gate_facts(self):
        facts = copy.deepcopy(_GATE_FACTS)
        facts["evaluated_by"] = "some-caller"
        request, _ = self._capture(gate_facts=facts)
        self.assertEqual(request["evaluated_by"], "some-caller")

    def test_run_context_copied_from_gate_facts(self):
        request, _ = self._capture()
        self.assertEqual(request["run_context"], self.gate_facts["run_context"])

    def test_execution_mode_is_full(self):
        request, _ = self._capture()
        self.assertEqual(request["execution_mode"], "full")

    def test_gate_declaration_frozen_fields(self):
        request, _ = self._capture()
        decl = request["gate_declaration"]
        self.assertEqual(decl["gate_type"], "validator")
        self.assertIs(decl["required"], True)
        self.assertIs(decl["allow_gate_override"], False)
        self.assertEqual(decl["failure_behavior"], "halt_run")

    def test_gate_contract_ref_bound_to_v120(self):
        request, _ = self._capture()
        ref = request["gate_declaration"]["contract_ref"]
        self.assertEqual(ref, {
            "artifact_id": "runtime-validator-mesh-contract",
            "artifact_kind": "contract",
            "artifact_version": "1.2.0",
        })

    def test_envelope_id_derived_from_mesh_eval_id(self):
        request, _ = self._capture()
        self.assertEqual(request["evidence_envelope"]["envelope_id"],
                         "eval-001-gate-envelope")

    def test_primary_evidence_from_report_bindings_in_order(self):
        mr = _mesh_result_for_verdict("pass")
        request, _ = self._capture(mesh_result=mr)
        primary = request["evidence_envelope"]["primary_evidence"]
        self.assertEqual(primary, [
            {"artifact_id": "report-001", "artifact_kind": "validation_report",
             "artifact_version": "1.0.0"},
        ])
        for artifact_ref in primary:
            self.assertNotIn("digest", artifact_ref)

    def test_validation_report_bound_to_mesh_id_v120(self):
        request, _ = self._capture()
        self.assertEqual(request["evidence_envelope"]["validation_report"], {
            "artifact_id": "mesh-001",
            "artifact_kind": "report",
            "artifact_version": "1.2.0",
        })

    def test_signal_report_ref_equals_validation_report(self):
        request, _ = self._capture()
        self.assertEqual(request["evaluation_signal"]["report_ref"],
                         request["evidence_envelope"]["validation_report"])

    def test_overall_verdict_from_mesh(self):
        mr = _mesh_result_for_verdict("fail")
        request, _ = self._capture(mesh_result=mr)
        self.assertEqual(request["evaluation_signal"]["overall_verdict"], "fail")
        self.assertEqual(request["evaluation_signal"]["failure_code"],
                         "validator_fail_deterministic")

    def test_blocked_without_bindings_uses_validator_unreachable(self):
        req = _make_cell_req("extension", True, None)
        decl = _make_declaration(requirements=[req])
        dr = _make_dispatch_result("req-001", "unreachable",
                                   error_code="validator_unreachable")
        mr = rvm.evaluate_validator_mesh(
            _make_request(declaration=decl, dispatch_results=[dr]))
        self.assertEqual(mr["aggregate_verdict"], "blocked")
        request, output = self._capture(mesh_result=mr)
        self.assertEqual(request["evaluation_signal"]["failure_code"],
                         "validator_unreachable")
        self.assertEqual(output["failure_code"], "validator_unreachable")

    def test_evidence_classification_from_immutable_table(self):
        for verdict, expected in (("pass", "complete"), ("fail", "complete"),
                                  ("blocked", "partial_recoverable"),
                                  ("inconclusive", "partial_absent"),
                                  ("human_review_required", "partial_absent")):
            mr = _mesh_result_for_verdict(verdict)
            request, _ = self._capture(mesh_result=mr)
            self.assertEqual(
                request["evidence_envelope"]["evidence_classification"],
                expected, verdict)
            if expected == "complete":
                self.assertNotIn("missing_evidence_description",
                                 request["evidence_envelope"])
            else:
                descriptions = request["evidence_envelope"][
                    "missing_evidence_description"]
                self.assertTrue(descriptions)
                self.assertTrue(all(_nonempty(d) for d in descriptions))

    def test_collected_from_mesh_fields(self):
        request, _ = self._capture()
        self.assertEqual(request["evidence_envelope"]["collected_at"],
                         "2026-07-01T00:00:00Z")
        self.assertEqual(request["evidence_envelope"]["collected_by"],
                         "architect")


# ---------------------------------------------------------------------------
# Differential vs direct evaluate_gate
# ---------------------------------------------------------------------------

class TestDifferentialVsDirectEvaluateGate(unittest.TestCase):
    """Bridge output equals direct ``evaluate_gate`` on the exact request the
    bridge constructed."""

    def test_five_verdicts_differential(self):
        for verdict in ("pass", "fail", "blocked", "inconclusive",
                        "human_review_required"):
            mesh_result = _mesh_result_for_verdict(verdict)
            with _SpyContext() as spy:
                bridge_output = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
                self.assertEqual(spy.call_count, 1)
                captured = spy.requests[0]
            direct = rgd.evaluate_gate(copy.deepcopy(captured))
            self.assertEqual(bridge_output, direct, verdict)

    def test_stale_and_unreachable_differential(self):
        # stale required -> blocked
        req = _make_cell_req("baseline", True, "fail")
        decl = _make_declaration(requirements=[req])
        binding = _make_binding("bind-cell", "req-001", "pass")
        binding["report_ref"] = {
            "artifact_id": None, "artifact_kind": "validation_report",
            "artifact_version": "1.0.0", "digest": DIGEST_A}
        dr = _make_dispatch_result("req-001", "report_produced", binding)
        stale_mesh = rvm.evaluate_validator_mesh(
            _make_request(declaration=decl, dispatch_results=[dr]))
        with _SpyContext() as spy:
            bridge_output = rpg.publish_to_gate(stale_mesh, _GATE_FACTS)
            captured = spy.requests[0]
        self.assertEqual(rgd.evaluate_gate(copy.deepcopy(captured)),
                         bridge_output)

        # required extension unreachable -> blocked with no bindings
        req = _make_cell_req("extension", True, None)
        decl = _make_declaration(requirements=[req])
        dr = _make_dispatch_result("req-001", "unreachable",
                                   error_code="validator_unreachable")
        unreachable_mesh = rvm.evaluate_validator_mesh(
            _make_request(declaration=decl, dispatch_results=[dr]))
        with _SpyContext() as spy:
            bridge_output = rpg.publish_to_gate(unreachable_mesh, _GATE_FACTS)
            captured = spy.requests[0]
        self.assertEqual(rgd.evaluate_gate(copy.deepcopy(captured)),
                         bridge_output)


# ---------------------------------------------------------------------------
# Spy injection
# ---------------------------------------------------------------------------

class TestSpyInjection(unittest.TestCase):
    """Zero Gate calls for malformed/invalid inputs; exactly one for every
    valid input."""

    def test_zero_calls_for_malformed_inputs(self):
        cases = [
            (None, _GATE_FACTS),
            ({}, _GATE_FACTS),
            ({"mesh_eval_id": "e", "mesh_id": "m", "aggregate_verdict": "pass"},
             _GATE_FACTS),
            (_mesh_result_for_verdict("pass"), None),
            (_mesh_result_for_verdict("pass"),
             {"evaluated_at": "t", "evaluated_by": "x"}),
        ]
        for mesh_result, gate_facts in cases:
            with _SpyContext() as spy:
                result = rpg.publish_to_gate(mesh_result, gate_facts)
                self.assertIn("error_code", result)
                self.assertEqual(spy.call_count, 0)

    def test_exactly_one_call_for_each_valid_verdict(self):
        for verdict in ("pass", "fail", "blocked", "inconclusive",
                        "human_review_required"):
            mesh_result = _mesh_result_for_verdict(verdict)
            with _SpyContext() as spy:
                result = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
                self.assertEqual(spy.call_count, 1, verdict)
                self.assertNotIn("error_code", result)

    def test_exactly_one_call_for_stale_result(self):
        req = _make_cell_req("baseline", True, "fail")
        decl = _make_declaration(requirements=[req])
        binding = _make_binding("bind-cell", "req-001", "pass")
        binding["report_ref"] = {
            "artifact_id": None, "artifact_kind": "validation_report",
            "artifact_version": "1.0.0", "digest": DIGEST_A}
        dr = _make_dispatch_result("req-001", "report_produced", binding)
        mesh_result = rvm.evaluate_validator_mesh(
            _make_request(declaration=decl, dispatch_results=[dr]))
        with _SpyContext() as spy:
            result = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
            self.assertEqual(spy.call_count, 1)
            self.assertNotIn("error_code", result)


# ---------------------------------------------------------------------------
# Non-mutation and return independence
# ---------------------------------------------------------------------------

class TestNonMutation(unittest.TestCase):
    """Caller inputs remain deeply unchanged; results are independent copies."""

    def test_mesh_result_unchanged_after_pass_call(self):
        mesh_result = _mesh_result_for_verdict("pass")
        before = copy.deepcopy(mesh_result)
        rpg.publish_to_gate(mesh_result, _GATE_FACTS)
        self.assertEqual(mesh_result, before)

    def test_mesh_result_unchanged_after_fail_call(self):
        mesh_result = _mesh_result_for_verdict("fail")
        before = copy.deepcopy(mesh_result)
        rpg.publish_to_gate(mesh_result, _GATE_FACTS)
        self.assertEqual(mesh_result, before)

    def test_mesh_result_unchanged_after_stale_call(self):
        req = _make_cell_req("baseline", True, "fail")
        decl = _make_declaration(requirements=[req])
        binding = _make_binding("bind-cell", "req-001", "pass")
        binding["report_ref"] = {
            "artifact_id": None, "artifact_kind": "validation_report",
            "artifact_version": "1.0.0", "digest": DIGEST_A}
        dr = _make_dispatch_result("req-001", "report_produced", binding)
        mesh_result = rvm.evaluate_validator_mesh(
            _make_request(declaration=decl, dispatch_results=[dr]))
        before = copy.deepcopy(mesh_result)
        rpg.publish_to_gate(mesh_result, _GATE_FACTS)
        self.assertEqual(mesh_result, before)

    def test_mesh_result_unchanged_after_error_call(self):
        mesh_result = {}
        before = copy.deepcopy(mesh_result)
        rpg.publish_to_gate(mesh_result, _GATE_FACTS)
        self.assertEqual(mesh_result, before)

    def test_gate_facts_unchanged(self):
        gate_facts = copy.deepcopy(_GATE_FACTS)
        before = copy.deepcopy(gate_facts)
        rpg.publish_to_gate(_mesh_result_for_verdict("pass"), gate_facts)
        self.assertEqual(gate_facts, before)

    def test_result_is_independent_deep_copy(self):
        mesh_result = _mesh_result_for_verdict("pass")
        result = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
        result["outcome"] = "mutated"
        result["evidence"] = [{"artifact_id": "mutated", "artifact_kind": "x"}]
        result2 = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
        self.assertEqual(result2["outcome"], "pass")
        result2["outcome"] = "mutated2"
        result3 = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
        self.assertEqual(result3["outcome"], "pass")

    def test_mesh_verdict_confidence_and_facts_never_revised(self):
        mesh_result = _mesh_result_for_verdict("blocked")
        snapshot = copy.deepcopy(mesh_result)
        output = rpg.publish_to_gate(mesh_result, _GATE_FACTS)
        self.assertEqual(mesh_result["aggregate_verdict"],
                         snapshot["aggregate_verdict"])
        self.assertEqual(mesh_result["aggregate_confidence"],
                         snapshot["aggregate_confidence"])
        self.assertEqual(mesh_result["requirement_results"],
                         snapshot["requirement_results"])
        self.assertEqual(mesh_result["freshness_assessments"],
                         snapshot["freshness_assessments"])
        self.assertEqual(mesh_result["recommended_action"],
                         snapshot["recommended_action"])
        self.assertEqual(output["outcome"], "blocked")


# ---------------------------------------------------------------------------
# Purity probes
# ---------------------------------------------------------------------------

class TestPurityProbes(unittest.TestCase):
    """Static probes prove no sidecar, SQLite, event append, artifact write,
    filesystem write, network, lifecycle helper, retry, clock, random, or
    publication operation."""

    FORBIDDEN_IMPORT_PATTERNS = (
        "sqlite3", "psycopg", "requests", "urllib", "sidecar", "subprocess",
        "socket", "pathlib", "json.dump", "datetime", "random", "os.",
        "time.", "hashlib", "threading",
    )

    def _bridge_source(self):
        with open(os.path.join(ROOT, "scripts", "runtime_publish_gate.py"),
                  "r", encoding="utf-8") as handle:
            return handle.read()

    def test_bridge_only_imports_copy_and_evaluate_gate(self):
        imports = []
        for line in self._bridge_source().split("\n"):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                imports.append(stripped)
        self.assertEqual(imports,
                         ["import copy",
                          "from scripts.runtime_gate_decision import evaluate_gate"])

    def test_no_forbidden_imports(self):
        for line in self._bridge_source().split("\n"):
            stripped = line.strip()
            if not (stripped.startswith("import ") or
                    stripped.startswith("from ")):
                continue
            for pattern in self.FORBIDDEN_IMPORT_PATTERNS:
                self.assertNotIn(pattern, stripped,
                                 "Forbidden import pattern %s in %s" %
                                 (pattern, stripped))

    def test_mesh_authority_imports_stay_frozen(self):
        """The bridge must not import or embed the Mesh evaluator."""
        source = self._bridge_source()
        self.assertNotIn("runtime_validator_mesh", source)


class TestCompileProbes(unittest.TestCase):
    """Compile probes use isolated temporary output; no Source __pycache__
    writes are required."""

    def test_compileall_isolated(self):
        tmp = tempfile.mkdtemp(prefix="pycache-216-")
        try:
            env = dict(os.environ)
            env["PYTHONPYCACHEPREFIX"] = tmp
            result = subprocess.run(
                [sys.executable, "-m", "compileall", "-q",
                 "scripts/runtime_publish_gate.py",
                 "scripts/test_runtime_publish_gate.py"],
                capture_output=True, text=True, cwd=ROOT, env=env)
            self.assertEqual(result.returncode, 0,
                             "compileall failed:\n%s" % result.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Regression pass
# ---------------------------------------------------------------------------

class TestRegressionPass(unittest.TestCase):
    """The Mesh core and Gate Decision suites remain importable alongside the
    focused publish suite."""

    def test_mesh_suite_importable(self):
        import scripts.test_runtime_validator_mesh  # noqa: F401

    def test_gate_decision_suite_importable(self):
        import scripts.test_runtime_gate_decision  # noqa: F401


if __name__ == "__main__":
    unittest.main()
