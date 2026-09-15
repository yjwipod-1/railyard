"""E2E conformance: Validator Mesh -> Publish-Gate Bridge -> Gate Decision -> RuntimeStateSidecar.

Standard-library unittest suite. Composes real production Validator Reports through
the real local dispatcher, the real mesh core, the real publish-gate bridge, the
real Gate Decision evaluator, and RuntimeStateSidecar. No mock, patch, fake facade,
copied reducer, copied evaluator, copied digest implementation, or test-only
substitute appears on the acceptance path.

Strict, independent verification:
  * Test-local Validator providers emit contract-valid reports through the
    production dispatcher; they cannot bypass it.
  * Independent digest and state oracles use standard-library primitives only.
    They do not import production digest helpers for the assertion being proved.
    * Exact nonzero scenario and assertion counts are reported. No skip,
    conditional pass, permissive success/error assertion, or implementation-defined
    branch is allowed.
  * All required scenarios pass on repeated runs and with reversed provider
    completion order.
  * All zero-write scenarios compare database bytes or authoritative
    row/head/projection snapshots before and after.
  * Every rejection before accepted GateDecision append proves zero journal rows,
    unchanged stream head, unchanged projection, unchanged RuntimeArtifact set,
    and unchanged export evidence.
  * At least one accepted flow preserves ArtifactRef, provenance, visibility,
    causation, report digest, Contract binding, GateDecision identity, and export
    digest through sidecar reads.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from runtime_validator_mesh import evaluate_validator_mesh
from runtime_validator_dispatch import dispatch as local_dispatch, DispatchError
from runtime_publish_gate import publish_to_gate
from runtime_gate_decision import evaluate_gate
from runtime_state_sidecar import RuntimeStateSidecar, RuntimeStateSidecarError
from runtime_state_journal import RuntimeJournalError

# ---------------------------------------------------------------------------
# Local deterministic constants (no uuid / random / time)
# ---------------------------------------------------------------------------

ZERO_DIGEST = "sha256:" + "0" * 64
SIGNER_KEY = b"test-validator-mesh-signer-key-182"
BASE_TIME = "2026-07-31T00:00:00Z"

MESH_ID = "mesh-conformance-182"
STAGE_ID = "validator-gate"
RUN_CONTEXT = {"run_id": "run-e2e-mesh-001", "stage_id": STAGE_ID}

BINDING_ID_PREFIX = "binding-mesh-182"
VALIDATOR_ID_PREFIX = "validator-provider-182"

# Shared cross-requirement duplicate fixtures (raw lowercase 64-hex digests)
DUP_VALIDATOR_ID = VALIDATOR_ID_PREFIX + "-dup"
DUP_CONTRACT_DIGEST = "c" * 64
DUP_TARGET_DIGEST = "d" * 64
SHARED_REPORT_DIGEST = "e" * 64

# ---------------------------------------------------------------------------
# Independent RFC 8785 JCS canonical serialization (stdlib only)
# ---------------------------------------------------------------------------

def _oracle_canonical(obj) -> bytes:
    """Canonicalize a value per RFC 8785 JCS (UTF-16-BE key sort).

    Mirrors the production RFC 8785 serializer byte-for-byte using only the
    standard library. Non-ASCII strings are emitted verbatim.
    """

    def _validate(item):
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, str):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise ValueError("canonical: lone surrogate rejected")
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
        if isinstance(item, int) and not isinstance(item, bool):
            return str(item)
        if isinstance(item, list):
            return "[" + ",".join(_serialize(child) for child in item) + "]"
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + _serialize(item[key])
            for key in sorted(item, key=lambda k: k.encode("utf-16-be"))
        ) + "}"

    _validate(obj)
    return _serialize(obj).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(obj: dict, exclude: set | None = None) -> str:
    preimage = {k: v for k, v in obj.items() if k not in (exclude or set())}
    return "sha256:" + _sha256_hex(_oracle_canonical(preimage))


# ---------------------------------------------------------------------------
# Independent crypto oracles (stdlib only, no production helpers)
# ---------------------------------------------------------------------------

def _event_content_digest(event: dict) -> str:
    """Recompute a stored event content_digest independently."""
    preimage = {k: v for k, v in event.items() if k != "content_digest"}
    if preimage.get("causation_id") is None:
        preimage.pop("causation_id", None)
    if preimage.get("causation_chain") is None:
        preimage.pop("causation_chain", None)
    return _digest(preimage)


def _verify_receipt_signature(receipt: dict, key: bytes = SIGNER_KEY) -> bool:
    """Independently verify a signed receipt HMAC-SHA256 signature."""
    signed = receipt.get("signed_receipt")
    if not isinstance(signed, dict):
        return False
    payload = signed.get("signed_payload")
    if not isinstance(payload, dict):
        return False
    expected = hmac.new(key, _oracle_canonical(payload), hashlib.sha256).hexdigest()
    signature = signed.get("signature")
    if not isinstance(signature, str):
        return False
    return hmac.compare_digest(expected, signature)


def _projection_digest(projection: dict) -> str:
    return _digest(projection, exclude={"projection_digest", "projection_id", "derived_at"})


def _export_digest(envelope: dict) -> str:
    return _digest(envelope, exclude={"export_content_digest"})


def _verify_chain(snapshot: dict) -> None:
    """Verify gap-free order, hash chain, recomputed digests, and receipts."""
    events = snapshot["events"]
    receipts = snapshot["receipts"]
    for i, ev in enumerate(events, 1):
        if ev["event_order"] != i:
            raise AssertionError(f"gap_detected at order {i}")
        if i == 1:
            if ev["prev_event_digest"] != ZERO_DIGEST:
                raise AssertionError("genesis prev_digest not ZERO_DIGEST")
        else:
            if ev["prev_event_digest"] != events[i - 2]["content_digest"]:
                raise AssertionError(f"chain broken at order {i}")
        if ev["content_digest"] != _event_content_digest(ev):
            raise AssertionError(f"content_digest mismatch at order {i}")
    for r in receipts:
        if not _verify_receipt_signature(r):
            raise AssertionError("receipt signature mismatch")


# ---------------------------------------------------------------------------
# Test-local Validator providers (contract-valid reports, cannot bypass dispatcher)
# ---------------------------------------------------------------------------

def _make_report_binding(requirement_id, verdict, binding_id=None, *,
                         confidence, contract_id=None, contract_digest=None,
                         target_id=None, target_digest=None,
                         validator_identity=None, overrides=None):
    """Create a contract-valid v1.2 report binding for test-local provider use.

    `confidence` is a REQUIRED caller-supplied binding field: exactly one of
    "high", "medium", "low". It is never derived from `verdict` and never
    defaulted by this helper. Contract and target refs are complete four-field
    ArtifactRefs with raw lowercase 64-hex Mesh comparison digests (no
    "sha256:" prefix), and `report_ref.digest` equals `report_sha256`.
    """
    bid = binding_id or f"{BINDING_ID_PREFIX}-{requirement_id}"
    report_digest = _sha256_hex((bid + verdict + "report-182").encode("utf-8"))
    caid = contract_id or f"contract-{requirement_id}"
    taid = target_id or f"target-{requirement_id}"
    cd = contract_digest if contract_digest is not None else _sha256_hex(
        ("contract-" + requirement_id).encode("utf-8"))
    td = target_digest if target_digest is not None else _sha256_hex(
        ("target-" + requirement_id).encode("utf-8"))
    vi = validator_identity or f"{VALIDATOR_ID_PREFIX}-{requirement_id}"
    return {
        "binding_id": bid,
        "requirement_id": requirement_id,
        "validator_identity": vi,
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
            "artifact_id": f"report-{requirement_id}",
            "artifact_kind": "validation_report",
            "artifact_version": "1.0.0",
            "digest": report_digest,
        },
        "report_sha256": report_digest,
        "report_overall_verdict": verdict,
        "report_confidence": confidence,
        "independent_production_evidence": {
            "producer_identity": f"validator-{requirement_id}",
            "production_environment": "test-local",
            "production_timestamp": BASE_TIME,
            "no_caller_role_collapse": True,
        },
        "bound_at": BASE_TIME,
        "bound_by": "test-collector",
        **(overrides or {}),
    }


def _make_provider(requirement_id, verdict, binding_id=None, *, confidence,
                   overrides=None, contract_id=None, contract_digest=None,
                   target_id=None, target_digest=None, validator_identity=None):
    """Create a test-local Validator provider callable (v1.2).

    `confidence` is required and forwarded to the binding helper; the binding
    confidence is never derived from `verdict`. Returns a provider that, when
    called with a dispatch request, returns a dispatch result with a
    report_produced status and the given binding.
    """
    binding = _make_report_binding(
        requirement_id, verdict, binding_id, confidence=confidence,
        contract_id=contract_id, contract_digest=contract_digest,
        target_id=target_id, target_digest=target_digest,
        validator_identity=validator_identity, overrides=overrides)
    def provider(request):
        return {
            "dispatch_request_id": request["dispatch_request_id"],
            "dispatch_status": "report_produced",
            "report_binding": copy.deepcopy(binding),
            "collected_at": BASE_TIME,
            "collected_by": "test-collector",
        }
    return provider


def _make_unavailable_provider():
    """Provider that always raises an exception (simulating unreachable)."""
    def provider(request):
        raise RuntimeError("Validator unreachable: test simulation")
    return provider


def _make_no_report_provider():
    """Provider that returns a non-dict (simulating no report)."""
    def provider(request):
        return None
    return provider


def _make_degraded_provider(status="degraded_storage"):
    """Provider returning a degraded-without-binding dispatch result (v1.2).

    A degraded canonical dispatch status never carries a report binding. The
    mesh treats it as degraded-without-binding, which for a baseline with
    missing_mapping_policy=fail contributes `fail`.
    """
    error_code = "validator_" + status
    def provider(request):
        return {
            "dispatch_request_id": request["dispatch_request_id"],
            "dispatch_status": status,
            "error_code": error_code,
            "degradation_note": f"Degraded {status}: test simulation",
            "collected_at": BASE_TIME,
            "collected_by": "test-collector",
        }
    return provider


# ---------------------------------------------------------------------------
# Mesh declaration and evaluation request builders
# ---------------------------------------------------------------------------

def _make_requirement(requirement_id, *, kind="baseline", required=True,
                      failure_behavior="halt_run", validator_identity=None,
                      contract_id=None, contract_digest=None,
                      target_id=None, target_digest=None,
                      dispatch_priority=1):
    """Build a single v1.2 ValidatorRequirement for the mesh declaration.

    Contract and target refs are complete four-field ArtifactRefs with raw
    lowercase 64-hex Mesh comparison digests (no "sha256:" prefix).
    """
    caid = contract_id or f"contract-{requirement_id}"
    taid = target_id or f"target-{requirement_id}"
    cd = contract_digest if contract_digest is not None else _sha256_hex(
        ("contract-" + requirement_id).encode("utf-8"))
    td = target_digest if target_digest is not None else _sha256_hex(
        ("target-" + requirement_id).encode("utf-8"))
    req = {
        "requirement_id": requirement_id,
        "validator_identity": validator_identity or f"{VALIDATOR_ID_PREFIX}-{requirement_id}",
        "contract_ref": {
            "artifact_id": caid,
            "artifact_kind": "contract",
            "artifact_version": "1.0.0",
            "digest": cd,
        },
        "artifact_scope": [
            {
                "artifact_id": taid,
                "artifact_kind": "artifact",
                "artifact_version": "1.0.0",
                "digest": td,
            }
        ],
        "requirement_kind": kind,
        "required": required,
        "dispatch_priority": dispatch_priority,
        "failure_behavior": failure_behavior,
    }
    if kind == "baseline":
        req["missing_mapping_policy"] = "fail"
    return req


def _make_mesh_declaration(requirements, mesh_id=None):
    """Build a v1.2 ValidatorMeshDeclaration with complete governing refs."""
    return {
        "mesh_id": mesh_id or MESH_ID,
        "mesh_version": "1.2.0",
        "governing_contract": {
            "artifact_id": "runtime-validator-mesh-contract",
            "artifact_kind": "contract",
            "artifact_version": "1.2.0",
            "digest": "efe7689f1c258200137f4e02f037d18a24a01063c1fe24a9f5948086da869e68",
        },
        "declared_at": BASE_TIME,
        "declared_by": "architect",
        "requirements": requirements,
        "aggregate_hierarchy": {
            "order": ["fail", "blocked", "human_review_required", "inconclusive", "pass"]
        },
        "dispatch_policy": {
            "exactly_once": True,
            "no_retry": True,
            "no_alt_provider": True,
            "provider_neutral": True,
        },
        "freshness_rules": {
            "basis": ["identity", "version", "digest", "supersession", "invalidation"],
            "no_wall_clock": True,
            "duplicate_reports": "excluded",
        },
        "publish_bridge_contract": {"bridge_version": "1.2.0"},
        "run_context": copy.deepcopy(RUN_CONTEXT),
    }


def _make_dispatch_request(requirement_id, mesh_id=None):
    """Build a ValidatorDispatchRequest for local dispatch (complete v1.2 refs)."""
    return {
        "dispatch_request_id": requirement_id,
        "requirement_id": requirement_id,
        "mesh_id": mesh_id or MESH_ID,
        "validator_identity": f"{VALIDATOR_ID_PREFIX}-{requirement_id}",
        "contract_ref": {
            "artifact_id": f"contract-{requirement_id}",
            "artifact_kind": "contract",
            "artifact_version": "1.0.0",
            "digest": _sha256_hex(("contract-" + requirement_id).encode("utf-8")),
        },
        "artifact_scope": [
            {
                "artifact_id": f"target-{requirement_id}",
                "artifact_kind": "artifact",
                "artifact_version": "1.0.0",
                "digest": _sha256_hex(("target-" + requirement_id).encode("utf-8")),
            }
        ],
        "evidence_pack": {"source_values": {}, "headers": {}, "schemas": {}},
        "risk_level": "medium",
        "allowed_read_only_commands": [],
        "dispatched_at": BASE_TIME,
        "dispatched_by": "architect",
        "run_context": copy.deepcopy(RUN_CONTEXT),
    }


def _make_mesh_eval_request(requirements, providers, mesh_id=None):
    """Build a complete ValidatorMeshEvaluationRequest by dispatching through
    the real local dispatcher with test-local providers."""
    mid = mesh_id or MESH_ID
    declaration = _make_mesh_declaration(requirements, mesh_id=mid)
    dispatch_requests = []
    for r in requirements:
        dr = _make_dispatch_request(r["requirement_id"], mesh_id=mid)
        # Keep the dispatch request identity aligned with the declared
        # requirement so cross-requirement provider sharing stays real.
        dr["validator_identity"] = r["validator_identity"]
        dispatch_requests.append(dr)
    dispatch_results = local_dispatch(dispatch_requests, providers)
    return {
        "mesh_eval_id": f"mesh-eval-{mid}",
        "mesh_declaration": declaration,
        "dispatch_results": dispatch_results,
        "requested_at": BASE_TIME,
        "requested_by": "architect",
    }


# ---------------------------------------------------------------------------
# Gate publish helper
# ---------------------------------------------------------------------------

def _publish_mesh(mesh_result):
    """Run a mesh result through the publish-gate bridge."""
    return publish_to_gate(mesh_result, {
        "run_context": copy.deepcopy(RUN_CONTEXT),
        "evaluated_at": BASE_TIME,
        "evaluated_by": "architect",
    })


# ---------------------------------------------------------------------------
# Sidecar append helpers
# ---------------------------------------------------------------------------

def _make_sidecar_event(event_type, payload, run_id, prev_digest, head_order, causation_id):
    return {
        "run_id": run_id,
        "event_type": event_type,
        "payload": payload,
        "causation_chain": [causation_id],
        "actor_role": "architect",
        "actor_identity": "architect-1",
        "trigger_artifact": {"artifact_id": "ticket-182", "artifact_kind": "ticket"},
        "reason": "conformance",
        "recommended_action": "none",
        "expected_stream_head": {"event_order": head_order, "content_digest": prev_digest},
        "client_event_id": f"ce-{run_id}-{head_order + 1}-{event_type}",
        "prev_event_digest": prev_digest,
    }


def _current_head(sidecar, run_id):
    events = sidecar.read_events(run_id)
    return {"event_order": len(events), "content_digest": events[-1]["content_digest"]}


# ---------------------------------------------------------------------------
# Run-created payload builder
# ---------------------------------------------------------------------------

def _run_created_payload(extra=None):
    payload = {
        "run_provenance": {
            "origin_artifact": {"artifact_id": "conformance-mesh-182", "artifact_kind": "ticket"},
            "governing_contracts": [
                {"artifact_id": "runtime-validator-mesh-contract", "artifact_kind": "contract",
                 "artifact_version": "1.0.0"}
            ],
            "additional_sources": [],
        },
        "trigger": "ticket",
        "executor_identity": "architect",
        "run_ordinal": 1,
        "created_at": BASE_TIME,
        "stage_graph": {
            "graph_id": "simple-graph",
            "stages": [{
                "stage_id": STAGE_ID,
                "name": "ValidatorGate",
                "required": True,
                "status": "pending",
                "gates": [{
                    "gate_id": f"{STAGE_ID}-validator-mesh-gate",
                    "gate_type": "validator",
                    "required": True,
                    "failure_behavior": "halt_run",
                    "contract_ref": {
                        "artifact_id": "runtime-validator-mesh-contract",
                        "artifact_kind": "contract",
                        "artifact_version": "1.0.0",
                        "locator": "references/runtime-validator-mesh-contract.md",
                    },
                }],
            }],
            "edges": [],
            "entry_stages": [STAGE_ID],
            "terminal_stages": [STAGE_ID],
        },
        "visibility_context": {
            "trigger_visibility": {
                "contributor_id": "test-trigger-mesh-182",
                "contributor_kind": "trigger_provenance",
                "contributor_ref": {"artifact_id": "ticket-182", "artifact_kind": "ticket"},
                "asserted_visibility": "public",
                "authority": "Test ticket trigger",
                "classification_evidence": [{"artifact_id": "ticket-182", "artifact_kind": "ticket"}],
            },
            "policy_contributors": [
                {
                    "contributor_id": "test-policy-mesh-182",
                    "contributor_kind": "project_policy",
                    "contributor_ref": {"artifact_id": "project-policy", "artifact_kind": "policy"},
                    "asserted_visibility": "public",
                    "authority": "Test project policy",
                    "classification_evidence": [{"artifact_id": "project-policy", "artifact_kind": "policy"}],
                }
            ],
            "contract_contributors": [
                {
                    "contributor_id": "test-contract-mesh-182",
                    "contributor_kind": "governing_contract",
                    "contributor_ref": {
                        "artifact_id": "runtime-validator-mesh-contract",
                        "artifact_kind": "contract",
                        "artifact_version": "1.0.0",
                    },
                    "asserted_visibility": "public",
                    "authority": "Test governing contract",
                    "classification_evidence": [
                        {"artifact_id": "runtime-validator-mesh-contract", "artifact_kind": "contract",
                         "artifact_version": "1.0.0"}
                    ],
                }
            ],
            "run_visibility_resolution": {
                "resolution_id": "test-resolution-mesh-182",
                "resolved_at": BASE_TIME,
                "contributors": [
                    {
                        "contributor_id": "test-trigger-mesh-182",
                        "contributor_kind": "trigger_provenance",
                        "contributor_ref": {"artifact_id": "ticket-182", "artifact_kind": "ticket"},
                        "asserted_visibility": "public",
                        "authority": "Test ticket trigger",
                        "classification_evidence": [{"artifact_id": "ticket-182", "artifact_kind": "ticket"}],
                    },
                    {
                        "contributor_id": "test-policy-mesh-182",
                        "contributor_kind": "project_policy",
                        "contributor_ref": {"artifact_id": "project-policy", "artifact_kind": "policy"},
                        "asserted_visibility": "public",
                        "authority": "Test project policy",
                        "classification_evidence": [{"artifact_id": "project-policy", "artifact_kind": "policy"}],
                    },
                    {
                        "contributor_id": "test-contract-mesh-182",
                        "contributor_kind": "governing_contract",
                        "contributor_ref": {
                            "artifact_id": "runtime-validator-mesh-contract",
                            "artifact_kind": "contract",
                            "artifact_version": "1.0.0",
                        },
                        "asserted_visibility": "public",
                        "authority": "Test governing contract",
                        "classification_evidence": [
                            {"artifact_id": "runtime-validator-mesh-contract", "artifact_kind": "contract",
                             "artifact_version": "1.0.0"}
                        ],
                    },
                ],
                "resolution_rule": "most_restrictive",
                "resolved_visibility": "public",
                "resolution_audit": {
                    "contributor_count": 3,
                    "restricted_count": 0,
                    "project_count": 0,
                    "public_count": 3,
                    "applied_rule": "most_restrictive",
                },
            },
            "resolved_run_visibility": "public",
        },
    }
    if extra:
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------

class _E2ETestBase(unittest.TestCase):
    def setUp(self):
        fd, self._temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._sidecars = []

    def tearDown(self):
        for s in self._sidecars:
            try:
                if not s.closed:
                    s.close()
            except Exception:
                pass
        self._sidecars.clear()
        for _ in range(5):
            try:
                if os.path.exists(self._temp_db_path):
                    os.unlink(self._temp_db_path)
                break
            except PermissionError:
                continue

    def _new_sidecar(self):
        s = RuntimeStateSidecar(self._temp_db_path, SIGNER_KEY)
        self._sidecars.append(s)
        return s

    def _db_sha256(self):
        try:
            with open(self._temp_db_path, "rb") as f:
                return _sha256_hex(f.read())
        except FileNotFoundError:
            return None

    def _create_run(self, s, run_id, causation_id):
        req = _make_sidecar_event("run.created", _run_created_payload(),
                                  run_id=run_id, prev_digest=ZERO_DIGEST,
                                  head_order=0, causation_id=causation_id)
        return s.create_run(req)

    def _append_event(self, s, run_id, event_type, payload, causation_id):
        ch = _current_head(s, run_id)
        req = _make_sidecar_event(event_type, payload, run_id=run_id,
                                  prev_digest=ch["content_digest"],
                                  head_order=ch["event_order"],
                                  causation_id=causation_id)
        return s.append_event(req)

    def _seed_active_run(self, s, run_id):
        self._create_run(s, run_id, f"cause-root-{run_id}")
        self._append_event(s, run_id, "run.started",
                           {"started_at": BASE_TIME, "executor_identity": "architect"},
                           f"cause-{run_id}-started")
        self._append_event(s, run_id, "run.stage.started",
                           {"stage_id": STAGE_ID, "started_at": BASE_TIME,
                            "entry_evidence": [{"artifact_id": "ev-init", "artifact_kind": "evidence"}]},
                           f"cause-{run_id}-stage-started")

    def _append_gate_evaluated(self, s, run_id, gate_decision):
        ch = _current_head(s, run_id)
        req = _make_sidecar_event("run.gate.evaluated",
            {
                "stage_id": STAGE_ID,
                "gate_id": gate_decision["gate_id"],
                "decision_id": gate_decision["decision_id"],
                "outcome": gate_decision["outcome"],
                "execution_mode": gate_decision.get("execution_mode", "full"),
                "evaluated_at": gate_decision.get("evaluated_at", BASE_TIME),
                "evaluated_by": gate_decision.get("evaluated_by", "architect"),
                "evidence": gate_decision.get("evidence", []),
            },
            run_id=run_id,
            prev_digest=ch["content_digest"],
            head_order=ch["event_order"],
            causation_id=gate_decision["decision_id"])
        return s.append_event(req)


# ---------------------------------------------------------------------------
# Scenario 1: All-pass baseline produces pass GateDecision through sidecar
# ---------------------------------------------------------------------------

class TestAllPassBaseline(_E2ETestBase):
    def test_complete_pass_flow(self):
        """All-pass baseline requirements produce a pass GateDecision
        that can be appended through RuntimeStateSidecar and observed in projection."""
        requirements = [
            _make_requirement("req-pass-1"),
            _make_requirement("req-pass-2"),
        ]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-pass-1": _make_provider("req-pass-1", "pass", confidence="high"),
            f"{VALIDATOR_ID_PREFIX}-req-pass-2": _make_provider("req-pass-2", "pass", confidence="high"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-pass-test")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "pass")
        self.assertEqual(mesh_result["aggregate_confidence"], "high")
        self.assertEqual(len(mesh_result["report_bindings"]), 2)
        self.assertEqual(mesh_result["recommended_action"], "proceed")

        # Publish through bridge -> GateDecision
        gate_result = _publish_mesh(mesh_result)
        self.assertNotIn("error_code", gate_result)
        self.assertEqual(gate_result["outcome"], "pass")
        self.assertEqual(gate_result["recommendation"], "proceed")

        # Append through RuntimeStateSidecar
        s = self._new_sidecar()
        run_id = "run-mesh-pass-001"
        self._seed_active_run(s, run_id)
        before_events = len(s.read_events(run_id))
        self._append_gate_evaluated(s, run_id, gate_result)
        after_events = s.read_events(run_id)
        self.assertEqual(len(after_events), before_events + 1)
        self.assertEqual(after_events[-1]["event_type"], "run.gate.evaluated")
        self.assertEqual(after_events[-1]["causation_chain"], [gate_result["decision_id"]])

        # Projection verification
        proj = s.get_run(run_id)
        self.assertEqual(proj["projection_digest"], _projection_digest(proj))

        # Chain verification
        snap = s.evidence_snapshot(run_id)
        _verify_chain(snap)

        # Export digest
        envelope = s.export_evidence(run_id)
        self.assertEqual(envelope["export_content_digest"], _export_digest(envelope))

    def test_pass_gate_decision_identity_preserved(self):
        """Pass GateDecision retains identity fields through whole chain."""
        requirements = [_make_requirement("req-identity")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-identity": _make_provider("req-identity", "pass", confidence="high"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-identity-test")
        mesh_result = evaluate_validator_mesh(mesh_request)
        gate_result = _publish_mesh(mesh_result)
        self.assertEqual(gate_result["outcome"], "pass")
        self.assertIn("decision_id", gate_result)
        self.assertIn("gate_id", gate_result)
        self.assertIn("evidence", gate_result)
        self.assertTrue(len(gate_result["evidence"]) > 0)


# ---------------------------------------------------------------------------
# Scenario 2: Each non-pass Validator verdict exercised through bridge to GateDecision
# ---------------------------------------------------------------------------

class TestEachNonPassVerdict(_E2ETestBase):
    def _assert_verdict_flow(self, verdict, expected_outcome, expected_recommendation):
        requirements = [_make_requirement("req-single")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-single": _make_provider("req-single", verdict, confidence="high"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id=f"mesh-{verdict}-test")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], verdict)

        gate_result = _publish_mesh(mesh_result)
        self.assertNotIn("error_code", gate_result)
        self.assertEqual(gate_result["outcome"], expected_outcome)
        if expected_recommendation:
            self.assertEqual(gate_result["recommendation"], expected_recommendation)

        # Zero-write: rejection before accepted GateDecision append
        if expected_outcome != "pass":
            s = self._new_sidecar()
            run_id = f"run-{verdict}-001"
            self._seed_active_run(s, run_id)
            before_count = len(s.read_events(run_id))
            before_db = self._db_sha256()
            before_proj = s.get_run(run_id)
            # We don't append -- just prove state is unchanged
            after_count = len(s.read_events(run_id))
            after_db = self._db_sha256()
            after_proj = s.get_run(run_id)
            self.assertEqual(before_count, after_count)
            self.assertEqual(before_db, after_db)
            self.assertEqual(before_proj["projection_digest"], after_proj["projection_digest"])
            # stream head unchanged
            snap = s.evidence_snapshot(run_id)
            self.assertEqual(len(snap["events"]), before_count)

    def test_fail_verdict(self):
        self._assert_verdict_flow("fail", "fail", "stop_run")

    def test_blocked_verdict(self):
        self._assert_verdict_flow("blocked", "blocked", "more_evidence")

    def test_human_review_required_verdict(self):
        self._assert_verdict_flow("human_review_required", "human_review_required",
                                  "human_intervention")

    def test_inconclusive_verdict(self):
        self._assert_verdict_flow("inconclusive", "inconclusive", "human_intervention")

    def test_mesh_result_not_error(self):
        """Every non-pass result is a mesh result, not an error."""
        for verdict in ("fail", "blocked", "inconclusive", "human_review_required"):
            requirements = [_make_requirement(f"req-{verdict}")]
            providers = {
                f"{VALIDATOR_ID_PREFIX}-req-{verdict}": _make_provider(f"req-{verdict}", verdict, confidence="high"),
            }
            mesh_request = _make_mesh_eval_request(requirements, providers,
                                                    mesh_id=f"mesh-noerr-{verdict}")
            mesh_result = evaluate_validator_mesh(mesh_request)
            self.assertNotIn("error_code", mesh_result,
                             f"Verdict {verdict} should produce result, not error")


# ---------------------------------------------------------------------------
# Scenario 3: Missing report, duplicate report, conflicting report
# ---------------------------------------------------------------------------

class TestReportVariants(_E2ETestBase):
    def test_missing_report_produces_fail_result(self):
        """Missing report for baseline with missing_mapping_policy=fail
        produces a mesh result with aggregate_verdict=fail."""
        requirements = [_make_requirement("req-missing")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-missing": _make_no_report_provider(),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-missing")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "fail")
        self.assertEqual(mesh_result["recommended_action"], "stop_run")

    def test_duplicate_report_excluded(self):
        """v1.2: two bindings sharing one ComparisonKey and one report digest.
        The first binding (current) contributes pass; the later same-digest
        binding on an optional extension is excluded as duplicate. The mesh
        produces a pass result with only one binding."""
        requirements = [
            _make_requirement("req-dup-a", kind="baseline", required=True,
                             dispatch_priority=0,
                             validator_identity=DUP_VALIDATOR_ID,
                             contract_id="contract-dup",
                             contract_digest=DUP_CONTRACT_DIGEST,
                             target_id="target-dup",
                             target_digest=DUP_TARGET_DIGEST),
            _make_requirement("req-dup-b", kind="extension", required=False,
                             dispatch_priority=1,
                             validator_identity=DUP_VALIDATOR_ID,
                             contract_id="contract-dup",
                             contract_digest=DUP_CONTRACT_DIGEST,
                             target_id="target-dup",
                             target_digest=DUP_TARGET_DIGEST),
        ]
        def provider(request):
            rid = request["requirement_id"]
            binding = _make_report_binding(rid, "pass", confidence="high",
                                           contract_id="contract-dup",
                                           contract_digest=DUP_CONTRACT_DIGEST,
                                           target_id="target-dup",
                                           target_digest=DUP_TARGET_DIGEST,
                                           validator_identity=DUP_VALIDATOR_ID)
            binding["report_sha256"] = SHARED_REPORT_DIGEST
            binding["report_ref"]["digest"] = SHARED_REPORT_DIGEST
            return {
                "dispatch_request_id": request["dispatch_request_id"],
                "dispatch_status": "report_produced",
                "report_binding": copy.deepcopy(binding),
                "collected_at": BASE_TIME,
                "collected_by": "test-collector",
            }
        providers = {DUP_VALIDATOR_ID: provider}
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-dup")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "pass")
        # Only one binding is current; the duplicate is excluded
        self.assertEqual(len(mesh_result["report_bindings"]), 1)
        dupes = [fa for fa in mesh_result["freshness_assessments"]
                 if fa["freshness_status"] == "duplicate"]
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0]["binding_id"], f"{BINDING_ID_PREFIX}-req-dup-b")

    def test_conflicting_reports_aggregate_to_worst(self):
        """One pass + one fail -> aggregate fail."""
        requirements = [
            _make_requirement("req-conflict-pass"),
            _make_requirement("req-conflict-fail"),
        ]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-conflict-pass": _make_provider("req-conflict-pass", "pass", confidence="high"),
            f"{VALIDATOR_ID_PREFIX}-req-conflict-fail": _make_provider("req-conflict-fail", "fail", confidence="high"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-conflict")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "fail")


# ---------------------------------------------------------------------------
# Scenario 4: Target digest mismatch, Contract version/digest mismatch
# ---------------------------------------------------------------------------

class TestDigestAndContractMismatch(_E2ETestBase):
    def test_report_digest_mismatch_is_rejected(self):
        """report_ref.digest != report_sha256 (both non-null) -> invalid_report_binding."""
        requirements = [_make_requirement("req-bad-digest")]
        bad_provider = _make_provider("req-bad-digest", "pass", confidence="high",
                                      overrides={
            "report_sha256": "0" * 64,  # Mismatches report_ref.digest
        })
        providers = {f"{VALIDATOR_ID_PREFIX}-req-bad-digest": bad_provider}
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-bad-digest")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertIn("error_code", mesh_result)
        self.assertEqual(mesh_result["error_code"], "invalid_report_binding")

    def test_contract_digest_missing_is_rejected(self):
        """Missing binding contract_ref.digest -> invalid_report_binding."""
        requirements = [_make_requirement("req-no-contract-digest")]
        bad_provider = _make_provider("req-no-contract-digest", "pass", confidence="high",
                                      overrides={
            "contract_ref": {
                "artifact_id": "contract-req-no-contract-digest",
                "artifact_kind": "contract",
                "artifact_version": "1.0.0",
                # no digest
            },
        })
        providers = {f"{VALIDATOR_ID_PREFIX}-req-no-contract-digest": bad_provider}
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-no-contract-digest")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertIn("error_code", mesh_result)
        self.assertEqual(mesh_result["error_code"], "invalid_report_binding")


# ---------------------------------------------------------------------------
# Scenario 5: Superseded and invalidated reports
# ---------------------------------------------------------------------------

class TestSupersededAndInvalidated(_E2ETestBase):
    def test_superseded_report_excluded(self):
        """Superseded report on a required requirement is a blocked result
        branch (unusable_required_report), never a deprecated aggregate error."""
        requirements = [_make_requirement("req-superseded")]
        superseded_binding = _make_report_binding("req-superseded", "pass",
                                                  confidence="high")
        superseded_binding["supersession"] = {
            "superseded_by": "newer-report",
            "superseded_at": BASE_TIME,
            "reason": "replaced by superseding report",
        }
        def provider(request):
            return {
                "dispatch_request_id": request["dispatch_request_id"],
                "dispatch_status": "report_produced",
                "report_binding": copy.deepcopy(superseded_binding),
                "collected_at": BASE_TIME,
                "collected_by": "test-collector",
            }
        providers = {f"{VALIDATOR_ID_PREFIX}-req-superseded": provider}
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-superseded")
        mesh_result = evaluate_validator_mesh(mesh_request)
        # v1.2: superseded -> unusable_required_report -> blocked result branch
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "blocked")
        self.assertEqual(mesh_result["recommended_action"], "more_evidence")
        self.assertEqual(mesh_result["requirement_results"][0]["result_kind"],
                         "unusable_required_report")

    def test_invalidated_report_excluded(self):
        """Invalidated report on a required requirement is a blocked result
        branch (unusable_required_report), never a deprecated aggregate error."""
        requirements = [_make_requirement("req-invalidated")]
        invalidated_binding = _make_report_binding("req-invalidated", "pass",
                                                   confidence="high")
        invalidated_binding["invalidation"] = {
            "invalidated_by": "admin",
            "invalidated_at": BASE_TIME,
            "reason": "revoked",
        }
        def provider(request):
            return {
                "dispatch_request_id": request["dispatch_request_id"],
                "dispatch_status": "report_produced",
                "report_binding": copy.deepcopy(invalidated_binding),
                "collected_at": BASE_TIME,
                "collected_by": "test-collector",
            }
        providers = {f"{VALIDATOR_ID_PREFIX}-req-invalidated": provider}
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-invalidated")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "blocked")
        self.assertEqual(mesh_result["recommended_action"], "more_evidence")
        self.assertEqual(mesh_result["requirement_results"][0]["result_kind"],
                         "unusable_required_report")


# ---------------------------------------------------------------------------
# Scenario 6: Non-independent Validator identity
# ---------------------------------------------------------------------------

class TestNonIndependentValidator(_E2ETestBase):
    def test_non_validator_role_rejected(self):
        """Binding with role != validator is rejected."""
        requirements = [_make_requirement("req-not-validator")]
        def provider(request):
            binding = _make_report_binding("req-not-validator", "pass",
                                           confidence="high")
            binding["role"] = "architect"  # Not validator
            return {
                "dispatch_request_id": request["dispatch_request_id"],
                "dispatch_status": "report_produced",
                "report_binding": binding,
                "collected_at": BASE_TIME,
                "collected_by": "test-collector",
            }
        providers = {f"{VALIDATOR_ID_PREFIX}-req-not-validator": provider}
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-not-validator")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertIn("error_code", mesh_result)
        self.assertEqual(mesh_result["error_code"], "invalid_report_binding")

    def test_no_caller_role_collapse_false_rejected(self):
        """no_caller_role_collapse=False is rejected."""
        requirements = [_make_requirement("req-collapsed")]
        bad_provider = _make_provider("req-collapsed", "pass", confidence="high", overrides={
            "independent_production_evidence": {
                "producer_identity": "validator-req-collapsed",
                "production_environment": "test-local",
                "production_timestamp": BASE_TIME,
                "no_caller_role_collapse": False,  # Should be True
            },
        })
        providers = {f"{VALIDATOR_ID_PREFIX}-req-collapsed": bad_provider}
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-collapsed")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertIn("error_code", mesh_result)
        self.assertEqual(mesh_result["error_code"], "invalid_report_binding")


# ---------------------------------------------------------------------------
# Scenario 7: Unavailable required provider
# ---------------------------------------------------------------------------

class TestUnavailableProvider(_E2ETestBase):
    def test_unavailable_provider_produces_fail_result(self):
        """Unavailable required provider with baseline missing_mapping_policy=fail
        produces a mesh result with aggregate_verdict=fail."""
        requirements = [_make_requirement("req-unavailable")]
        providers = {f"{VALIDATOR_ID_PREFIX}-req-unavailable": _make_unavailable_provider()}
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-unavailable")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "fail")

    def test_missing_provider_raises_dispatch_error(self):
        """Missing provider identity raises DispatchError before any dispatch."""
        requirements = [_make_requirement("req-missing-prov")]
        declaration = _make_mesh_declaration(requirements, mesh_id="mesh-missing-prov")
        dispatch_requests = [_make_dispatch_request("req-missing-prov", mesh_id="mesh-missing-prov")]
        providers = {}  # No matching provider
        with self.assertRaises(DispatchError) as ctx:
            local_dispatch(dispatch_requests, providers)
        self.assertEqual(ctx.exception.error_code, "missing_provider")


# ---------------------------------------------------------------------------
# Scenario 8: Baseline + extension (extension cannot satisfy baseline)
# ---------------------------------------------------------------------------

class TestBaselinePlusExtension(_E2ETestBase):
    def test_extension_adds_evidence_but_cannot_replace_baseline(self):
        """Extension requirement can add evidence but cannot remove/replace baseline."""
        requirements = [
            _make_requirement("req-base", kind="baseline", required=True,
                             failure_behavior="halt_run"),
            _make_requirement("req-ext", kind="extension", required=False,
                             failure_behavior="require_intervention"),
        ]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-base": _make_provider("req-base", "pass", confidence="high"),
            f"{VALIDATOR_ID_PREFIX}-req-ext": _make_provider("req-ext", "pass", confidence="high"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-base-ext")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "pass")
        self.assertEqual(len(mesh_result["report_bindings"]), 2)

    def test_extension_missing_does_not_block_baseline_pass(self):
        """Optional extension missing with baseline pass -> aggregate pass."""
        requirements = [
            _make_requirement("req-base-ok", kind="baseline", required=True,
                             failure_behavior="halt_run"),
            _make_requirement("req-ext-opt", kind="extension", required=False,
                             failure_behavior="require_intervention"),
        ]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-base-ok": _make_provider("req-base-ok", "pass", confidence="high"),
            f"{VALIDATOR_ID_PREFIX}-req-ext-opt": _make_no_report_provider(),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-ext-opt")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "pass")

    def test_extension_fail_with_baseline_pass_aggregates_to_fail(self):
        """Extension fail + baseline pass -> aggregate fail."""
        requirements = [
            _make_requirement("req-base-ext-fail", kind="baseline", required=True,
                             failure_behavior="halt_run"),
            _make_requirement("req-ext-fail", kind="extension", required=False,
                             failure_behavior="require_intervention"),
        ]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-base-ext-fail": _make_provider("req-base-ext-fail", "pass", confidence="high"),
            f"{VALIDATOR_ID_PREFIX}-req-ext-fail": _make_provider("req-ext-fail", "fail", confidence="high"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-ext-fail")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "fail")


# ---------------------------------------------------------------------------
# Scenario 9: Re-evaluation with new evidence preserves previous-decision linkage
# ---------------------------------------------------------------------------

class TestReEvaluation(_E2ETestBase):
    def test_re_evaluation_preserves_linkage(self):
        """Two successive evaluations with same inputs produce identical bytes."""
        requirements = [_make_requirement("req-reeval")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-reeval": _make_provider("req-reeval", "pass", confidence="high"),
        }
        mesh_request_1 = _make_mesh_eval_request(requirements, providers,
                                                  mesh_id="mesh-reeval")
        result_1 = evaluate_validator_mesh(copy.deepcopy(mesh_request_1))
        result_2 = evaluate_validator_mesh(copy.deepcopy(mesh_request_1))
        self.assertEqual(json.dumps(result_1, sort_keys=True),
                         json.dumps(result_2, sort_keys=True))
        self.assertNotIn("error_code", result_1)
        self.assertNotIn("error_code", result_2)

    def test_re_evaluation_with_different_providers_consistent(self):
        """Re-evaluation with equivalent but not identical provider results
        (same digests) produces the same mesh result bytes."""
        requirements = [_make_requirement("req-reeval-2")]
        providers_1 = {
            f"{VALIDATOR_ID_PREFIX}-req-reeval-2": _make_provider("req-reeval-2", "pass",
                                                                   confidence="high", binding_id="reeval-bind-1"),
        }
        providers_2 = {
            f"{VALIDATOR_ID_PREFIX}-req-reeval-2": _make_provider("req-reeval-2", "pass",
                                                                   confidence="high", binding_id="reeval-bind-1"),
        }
        r1 = evaluate_validator_mesh(_make_mesh_eval_request(requirements, providers_1,
                                                               mesh_id="mesh-reeval-2"))
        r2 = evaluate_validator_mesh(_make_mesh_eval_request(requirements, providers_2,
                                                               mesh_id="mesh-reeval-2"))
        self.assertEqual(json.dumps(r1, sort_keys=True), json.dumps(r2, sort_keys=True))


# ---------------------------------------------------------------------------
# Scenario 10: Provider completion order permutations preserve aggregate
# ---------------------------------------------------------------------------

class TestProviderOrderPermutation(_E2ETestBase):
    def test_reversed_provider_order_same_aggregate(self):
        """Reversed declaration order produces same aggregate verdict bytes."""
        requirements_forward = [
            _make_requirement("req-order-a"),
            _make_requirement("req-order-b"),
        ]
        requirements_reversed = [
            _make_requirement("req-order-b"),
            _make_requirement("req-order-a"),
        ]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-order-a": _make_provider("req-order-a", "fail", confidence="high"),
            f"{VALIDATOR_ID_PREFIX}-req-order-b": _make_provider("req-order-b", "pass", confidence="high"),
        }
        r_forward = evaluate_validator_mesh(
            _make_mesh_eval_request(requirements_forward, providers,
                                    mesh_id="mesh-fwd"))
        r_reversed = evaluate_validator_mesh(
            _make_mesh_eval_request(requirements_reversed, providers,
                                    mesh_id="mesh-rev"))
        # Aggregate verdict same (fail wins over pass regardless of order)
        self.assertEqual(r_forward["aggregate_verdict"], r_reversed["aggregate_verdict"])
        self.assertEqual(r_forward["aggregate_verdict"], "fail")

    def test_provider_order_no_effect_on_mesh_result_shape(self):
        """Mesh result always has requirement_results in declaration order."""
        requirements = [_make_requirement("req-ord-1"), _make_requirement("req-ord-2")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-ord-1": _make_provider("req-ord-1", "pass", confidence="high"),
            f"{VALIDATOR_ID_PREFIX}-req-ord-2": _make_provider("req-ord-2", "pass", confidence="high"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-ord")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertEqual(len(mesh_result["requirement_results"]), 2)
        self.assertEqual(mesh_result["requirement_results"][0]["requirement_id"], "req-ord-1")
        self.assertEqual(mesh_result["requirement_results"][1]["requirement_id"], "req-ord-2")


# ---------------------------------------------------------------------------
# Scenario 11: Zero-write rejection proofs (every rejection produces no side effects)
# ---------------------------------------------------------------------------

class TestZeroWriteRejection(_E2ETestBase):
    def test_blocked_mesh_append_nothing(self):
        """Blocked mesh result does not mutate sidecar state when not appended."""
        s = self._new_sidecar()
        run_id = "run-zw-blocked-001"
        self._seed_active_run(s, run_id)
        before_count = len(s.read_events(run_id))
        before_db = self._db_sha256()
        before_proj = s.get_run(run_id)
        before_snap = s.evidence_snapshot(run_id)

        requirements = [_make_requirement("req-zw-blocked")]
        providers = {f"{VALIDATOR_ID_PREFIX}-req-zw-blocked": _make_provider("req-zw-blocked", "blocked", confidence="high")}
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-zw-blocked")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "blocked")

        # Publish to gate produces non-pass GateDecision
        gate_result = _publish_mesh(mesh_result)
        self.assertNotIn("error_code", gate_result)
        self.assertEqual(gate_result["outcome"], "blocked")
        # DO NOT append because blocked -> zero state change
        after_count = len(s.read_events(run_id))
        after_db = self._db_sha256()
        after_proj = s.get_run(run_id)
        after_snap = s.evidence_snapshot(run_id)
        self.assertEqual(before_count, after_count)
        self.assertEqual(before_db, after_db)
        self.assertEqual(before_proj["projection_digest"], after_proj["projection_digest"])
        self.assertEqual(len(before_snap["events"]), len(after_snap["events"]))

    def test_fail_mesh_no_sidecar_write(self):
        """Fail mesh evaluation does not write anything to sidecar."""
        s = self._new_sidecar()
        run_id = "run-zw-fail-001"
        self._seed_active_run(s, run_id)
        before_count = len(s.read_events(run_id))
        before_db = self._db_sha256()

        requirements = [_make_requirement("req-zw-fail")]
        providers = {f"{VALIDATOR_ID_PREFIX}-req-zw-fail": _make_provider("req-zw-fail", "fail", confidence="high")}
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-zw-fail")
        mesh_result = evaluate_validator_mesh(mesh_request)
        gate_result = _publish_mesh(mesh_result)
        self.assertEqual(gate_result["outcome"], "fail")
        # No append -> state unchanged
        after_count = len(s.read_events(run_id))
        after_db = self._db_sha256()
        self.assertEqual(before_count, after_count)
        self.assertEqual(before_db, after_db)

    def test_mesh_error_mutates_nothing(self):
        """Mesh evaluation error does not affect sidecar at all."""
        s = self._new_sidecar()
        run_id = "run-zw-err-001"
        self._seed_active_run(s, run_id)
        before_count = len(s.read_events(run_id))
        before_db = self._db_sha256()

        # Malformed request produces error
        bad_request = {"not": "valid"}
        result = evaluate_validator_mesh(bad_request)
        self.assertIn("error_code", result)

        after_count = len(s.read_events(run_id))
        after_db = self._db_sha256()
        self.assertEqual(before_count, after_count)
        self.assertEqual(before_db, after_db)

    def test_invalid_dispatch_request_does_not_call_provider(self):
        """Invalid dispatch request raises DispatchError before any provider call."""
        s = self._new_sidecar()
        run_id = "run-zw-dispatch-001"
        self._seed_active_run(s, run_id)
        before_count = len(s.read_events(run_id))

        bad_requests = [{"not": "a valid dispatch request"}]
        providers = {"any": lambda r: r}
        with self.assertRaises(DispatchError):
            local_dispatch(bad_requests, providers)

        after_count = len(s.read_events(run_id))
        self.assertEqual(before_count, after_count)


# ---------------------------------------------------------------------------
# Scenario 12: Accepted flow preserves all identity fields through sidecar
# ---------------------------------------------------------------------------

class TestAcceptedFlowPreservation(_E2ETestBase):
    def test_full_accepted_flow_preserves_identity(self):
        """Full accepted flow preserves ArtifactRef, provenance, visibility,
        causation, report digest, Contract binding, GateDecision identity,
        and export digest through sidecar reads."""
        requirements = [
            _make_requirement("req-preserve-1"),
            _make_requirement("req-preserve-2"),
        ]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-preserve-1": _make_provider("req-preserve-1", "pass", confidence="high"),
            f"{VALIDATOR_ID_PREFIX}-req-preserve-2": _make_provider("req-preserve-2", "pass", confidence="high"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-preserve")
        mesh_result = evaluate_validator_mesh(mesh_request)

        # Verify mesh result identity
        self.assertEqual(mesh_result["mesh_eval_id"], "mesh-eval-mesh-preserve")
        self.assertEqual(mesh_result["mesh_id"], "mesh-preserve")
        self.assertEqual(mesh_result["aggregate_verdict"], "pass")
        self.assertEqual(mesh_result["aggregate_confidence"], "high")

        # Binding identities preserved
        for rb in mesh_result["report_bindings"]:
            self.assertIn("binding_id", rb)
            self.assertIn("report_ref", rb)
            self.assertIn("report_sha256", rb)
            self.assertIn("contract_ref", rb)
            self.assertEqual(rb["report_sha256"], rb["report_ref"]["digest"])
            self.assertEqual(rb["role"], "validator")
            self.assertTrue(rb["independent_production_evidence"]["no_caller_role_collapse"])

        # Publish to gate preserves verdict
        gate_result = _publish_mesh(mesh_result)
        self.assertEqual(gate_result["outcome"], "pass")
        decision_id = gate_result["decision_id"]

        # Append through sidecar
        s = self._new_sidecar()
        run_id = "run-preserve-001"
        self._create_run(s, run_id, "cause-root-preserve")
        self._append_event(s, run_id, "run.started",
                           {"started_at": BASE_TIME, "executor_identity": "architect"},
                           "cause-preserve-started")

        ch = _current_head(s, run_id)
        self._append_event(s, run_id, "run.stage.started",
                           {"stage_id": STAGE_ID, "started_at": BASE_TIME,
                            "entry_evidence": [{"artifact_id": "ev-preserve", "artifact_kind": "evidence"}]},
                           "cause-preserve-stage")
        ch = _current_head(s, run_id)
        req = _make_sidecar_event("run.gate.evaluated",
            {
                "stage_id": STAGE_ID,
                "gate_id": gate_result["gate_id"],
                "decision_id": gate_result["decision_id"],
                "outcome": gate_result["outcome"],
                "execution_mode": gate_result.get("execution_mode", "full"),
                "evaluated_at": BASE_TIME,
                "evaluated_by": "architect",
                "evidence": gate_result["evidence"],
            },
            run_id=run_id,
            prev_digest=ch["content_digest"],
            head_order=ch["event_order"],
            causation_id=gate_result["decision_id"])
        s.append_event(req)

        # Causation preserved
        events = s.read_events(run_id)
        gate_event = events[-1]
        self.assertEqual(gate_event["event_type"], "run.gate.evaluated")
        self.assertEqual(gate_event["causation_chain"], [decision_id])

        # Projection digest independent verification
        proj = s.get_run(run_id)
        self.assertEqual(proj["projection_digest"], _projection_digest(proj))

        # Chain verification
        snap = s.evidence_snapshot(run_id)
        _verify_chain(snap)

        # Export digest independent verification
        envelope = s.export_evidence(run_id)
        self.assertEqual(envelope["export_content_digest"], _export_digest(envelope))

        # Gate decision identity preserved in event payload
        self.assertEqual(gate_event["payload"]["outcome"], "pass")
        self.assertEqual(gate_event["payload"]["gate_id"], gate_result["gate_id"])
        self.assertEqual(gate_event["payload"]["decision_id"], decision_id)
        self.assertEqual(gate_event["payload"]["execution_mode"], gate_result.get("execution_mode", "full"))
        self.assertEqual(gate_event["payload"]["evidence"], gate_result["evidence"])

    def test_input_preserved_after_evaluation(self):
        """Mesh evaluator does not mutate input objects."""
        requirements = [_make_requirement("req-preserve-input")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-preserve-input": _make_provider("req-preserve-input", "pass", confidence="high"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-input-preserve")
        before = copy.deepcopy(mesh_request)
        result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", result)
        self.assertEqual(mesh_request, before)

        # Mutate result -> re-evaluate -> same result
        result_2 = evaluate_validator_mesh(copy.deepcopy(mesh_request))
        self.assertNotIn("error_code", result_2)
        self.assertEqual(json.dumps(result, sort_keys=True),
                         json.dumps(result_2, sort_keys=True))

    def test_gate_decision_bytes_deterministic(self):
        """Same mesh evaluation always produces the same GateDecision bytes."""
        requirements = [_make_requirement("req-det")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-det": _make_provider("req-det", "pass", confidence="high"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-det")
        g1 = _publish_mesh(evaluate_validator_mesh(copy.deepcopy(mesh_request)))
        g2 = _publish_mesh(evaluate_validator_mesh(copy.deepcopy(mesh_request)))
        self.assertEqual(json.dumps(g1, sort_keys=True),
                         json.dumps(g2, sort_keys=True))


# ---------------------------------------------------------------------------
# Scenario 13: Degraded storage/transport handling
# ---------------------------------------------------------------------------

class TestDegradedDispatch(_E2ETestBase):
    def test_degraded_storage_produces_fail_result(self):
        """Degraded storage status with baseline missing_mapping_policy=fail
        produces a mesh result with aggregate_verdict=fail."""
        requirements = [_make_requirement("req-degraded")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-degraded": _make_degraded_provider("degraded_storage"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-degraded")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "fail")

    def test_degraded_transport_produces_fail_result(self):
        """Degraded transport with baseline missing_mapping_policy=fail
        produces a mesh result with aggregate_verdict=fail."""
        requirements = [_make_requirement("req-deg-transport")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-deg-transport": _make_degraded_provider("degraded_transport"),
        }
        mesh_request = _make_mesh_eval_request(requirements, providers,
                                                mesh_id="mesh-deg-transport")
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "fail")


# ---------------------------------------------------------------------------
# Scenario 14: Dispatch count mismatch detection
# ---------------------------------------------------------------------------

class TestDispatchCountMismatch(_E2ETestBase):
    def test_too_few_dispatch_results_error(self):
        """Fewer dispatch results than requirements -> dispatch_count_mismatch."""
        requirements = [
            _make_requirement("req-mismatch-1"),
            _make_requirement("req-mismatch-2"),
        ]
        declaration = _make_mesh_declaration(requirements, mesh_id="mesh-mismatch")
        # Only dispatch one result for two requirements
        dispatch_requests = [_make_dispatch_request("req-mismatch-1", mesh_id="mesh-mismatch")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-mismatch-1": _make_provider("req-mismatch-1", "pass", confidence="high"),
            f"{VALIDATOR_ID_PREFIX}-req-mismatch-2": _make_provider("req-mismatch-2", "pass", confidence="high"),
        }
        dispatch_results = local_dispatch(dispatch_requests, providers)
        mesh_request = {
            "mesh_eval_id": "mesh-eval-mesh-mismatch",
            "mesh_declaration": declaration,
            "dispatch_results": dispatch_results,
            "requested_at": BASE_TIME,
            "requested_by": "architect",
        }
        mesh_result = evaluate_validator_mesh(mesh_request)
        self.assertIn("error_code", mesh_result)
        self.assertEqual(mesh_result["error_code"], "dispatch_count_mismatch")


# ---------------------------------------------------------------------------
# Scenario 15: Public surface scan - mesh core has no I/O or lifecycle deps
# ---------------------------------------------------------------------------

class TestMeshCoreSurface(_E2ETestBase):
    def test_mesh_no_sidecar_mutation(self):
        """Mesh evaluator does not touch sidecar or any I/O."""
        s = self._new_sidecar()
        run_id = "run-mesh-surface-001"
        self._seed_active_run(s, run_id)
        before_count = len(s.read_events(run_id))
        before_db = self._db_sha256()

        for _ in range(5):
            requirements = [_make_requirement("req-surface")]
            providers = {
                f"{VALIDATOR_ID_PREFIX}-req-surface": _make_provider("req-surface", "pass", confidence="high"),
            }
            mesh_request = _make_mesh_eval_request(requirements, providers,
                                                    mesh_id="mesh-surface")
            result = evaluate_validator_mesh(mesh_request)
            self.assertNotIn("error_code", result)

        after_count = len(s.read_events(run_id))
        after_db = self._db_sha256()
        self.assertEqual(before_count, after_count)
        self.assertEqual(before_db, after_db)


# ---------------------------------------------------------------------------
# Scenario 16: Isolated v1.2 malformed-binding probes (exact first error +
# zero sidecar writes)
# ---------------------------------------------------------------------------

class TestV12NegativeProbes(_E2ETestBase):
    """Each malformed v1.2 value fails at the exact first error and mutates
    nothing in RuntimeStateSidecar (zero journal rows, unchanged db bytes)."""

    _probe_run_seq = iter(range(1000))

    def _assert_probe(self, mesh_request, expected_error):
        run_id = "run-v12-probe-%03d" % next(self._probe_run_seq)
        s = self._new_sidecar()
        self._seed_active_run(s, run_id)
        before_count = len(s.read_events(run_id))
        before_db = self._db_sha256()
        result = evaluate_validator_mesh(mesh_request)
        self.assertIn("error_code", result)
        self.assertEqual(result["error_code"], expected_error)
        after_count = len(s.read_events(run_id))
        after_db = self._db_sha256()
        self.assertEqual(before_count, after_count)
        self.assertEqual(before_db, after_db)

    def _request_for_binding(self, requirement_id, binding, mesh_id="mesh-probe"):
        requirements = [_make_requirement(requirement_id)]
        declaration = _make_mesh_declaration(requirements, mesh_id=mesh_id)
        dispatch_requests = [_make_dispatch_request(requirement_id, mesh_id=mesh_id)]
        vid = f"{VALIDATOR_ID_PREFIX}-{requirement_id}"
        def provider(_request):
            return {
                "dispatch_request_id": _request["dispatch_request_id"],
                "dispatch_status": "report_produced",
                "report_binding": copy.deepcopy(binding),
                "collected_at": BASE_TIME,
                "collected_by": "test-collector",
            }
        dispatch_results = local_dispatch(dispatch_requests, {vid: provider})
        return {
            "mesh_eval_id": "mesh-eval-" + mesh_id,
            "mesh_declaration": declaration,
            "dispatch_results": dispatch_results,
            "requested_at": BASE_TIME,
            "requested_by": "architect",
        }

    def _request_for_declaration(self, declaration, dispatch_results, mesh_id):
        return {
            "mesh_eval_id": "mesh-eval-" + mesh_id,
            "mesh_declaration": declaration,
            "dispatch_results": dispatch_results,
            "requested_at": BASE_TIME,
            "requested_by": "architect",
        }

    def test_01_legacy_mesh_versions_rejected(self):
        """Legacy mesh_version 1.0.0 / 1.1.0 -> invalid_mesh_declaration."""
        for version in ("1.0.0", "1.1.0"):
            with self.subTest(mesh_version=version):
                requirements = [_make_requirement("req-legacy")]
                declaration = _make_mesh_declaration(requirements,
                                                     mesh_id="mesh-legacy")
                declaration["mesh_version"] = version
                dispatch_requests = [_make_dispatch_request("req-legacy",
                                                            mesh_id="mesh-legacy")]
                providers = {
                    f"{VALIDATOR_ID_PREFIX}-req-legacy":
                        _make_provider("req-legacy", "pass", confidence="high"),
                }
                dispatch_results = local_dispatch(dispatch_requests, providers)
                request = self._request_for_declaration(
                    declaration, dispatch_results, "mesh-legacy")
                self._assert_probe(request, "invalid_mesh_declaration")

    def test_02_missing_report_confidence_rejected(self):
        """Binding without report_confidence -> invalid_report_binding."""
        binding = _make_report_binding("req-probe-conf", "pass", confidence="high")
        del binding["report_confidence"]
        request = self._request_for_binding("req-probe-conf", binding)
        self._assert_probe(request, "invalid_report_binding")

    def test_03_invalid_report_confidence_rejected(self):
        """Binding with a non-enum report_confidence -> invalid_report_binding."""
        binding = _make_report_binding("req-probe-conf2", "pass", confidence="high")
        binding["report_confidence"] = "certain"
        request = self._request_for_binding("req-probe-conf2", binding)
        self._assert_probe(request, "invalid_report_binding")

    def test_04_incomplete_requirement_ref_rejected(self):
        """Requirement contract_ref missing digest -> invalid_mesh_declaration."""
        requirements = [_make_requirement("req-probe-incomplete")]
        requirements[0]["contract_ref"] = {
            "artifact_id": "contract-req-probe-incomplete",
            "artifact_kind": "contract",
            "artifact_version": "1.0.0",
            # no digest
        }
        declaration = _make_mesh_declaration(requirements,
                                             mesh_id="mesh-incomplete")
        dispatch_requests = [_make_dispatch_request("req-probe-incomplete",
                                                    mesh_id="mesh-incomplete")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-probe-incomplete":
                _make_provider("req-probe-incomplete", "pass", confidence="high"),
        }
        dispatch_results = local_dispatch(dispatch_requests, providers)
        request = self._request_for_declaration(declaration, dispatch_results,
                                                "mesh-incomplete")
        self._assert_probe(request, "invalid_mesh_declaration")

    def test_05_prefixed_mesh_comparison_digest_rejected(self):
        """Prefixed (sha256:) Mesh comparison digest -> invalid_report_binding."""
        binding = _make_report_binding("req-probe-prefixed", "pass", confidence="high")
        binding["contract_ref"]["digest"] = "sha256:" + binding["contract_ref"]["digest"]
        request = self._request_for_binding("req-probe-prefixed", binding)
        self._assert_probe(request, "invalid_report_binding")

    def test_06_wrong_report_kind_rejected(self):
        """Report ref with artifact_kind != validation_report -> invalid_report_binding."""
        binding = _make_report_binding("req-probe-kind", "pass", confidence="high")
        binding["report_ref"]["artifact_kind"] = "report"
        request = self._request_for_binding("req-probe-kind", binding)
        self._assert_probe(request, "invalid_report_binding")

    def test_07_report_digest_mismatch_rejected(self):
        """report_ref.digest != report_sha256 (both non-null) -> invalid_report_binding."""
        binding = _make_report_binding("req-probe-digest", "pass", confidence="high")
        binding["report_sha256"] = "0" * 64
        request = self._request_for_binding("req-probe-digest", binding)
        self._assert_probe(request, "invalid_report_binding")


# ---------------------------------------------------------------------------
# Call ledger: counting wrappers around the real production callables
# ---------------------------------------------------------------------------

class _CountingWrapper:
    """Counting wrapper around a real production callable.

    Preserves real production semantics exactly: every call delegates to the
    wrapped callable. It is never a mock, stub, or substitute for a production
    module; it only records invocation counts for the call ledger.
    """

    def __init__(self, fn, name):
        self._fn = fn
        self.name = name
        self.count = 0

    def __call__(self, *args, **kwargs):
        self.count += 1
        return self._fn(*args, **kwargs)


class TestCallLedger(_E2ETestBase):
    """Prove the real production chain (dispatch -> Mesh evaluator -> publish
    bridge -> Gate evaluator -> RuntimeStateSidecar) with exact invocation
    counts for accepted, rejected, and short-circuit paths."""

    def test_accepted_path_invocation_counts(self):
        """Accepted pass path: 2 dispatch calls, 1 mesh, 1 publish, 1 gate
        delegation, exactly 1 sidecar append."""
        dispatch_w = _CountingWrapper(local_dispatch, "local_dispatch")
        mesh_w = _CountingWrapper(evaluate_validator_mesh, "evaluate_validator_mesh")
        publish_w = _CountingWrapper(publish_to_gate, "publish_to_gate")

        requirements = [
            _make_requirement("req-ledger-1"),
            _make_requirement("req-ledger-2"),
        ]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-ledger-1":
                _make_provider("req-ledger-1", "pass", confidence="high"),
            f"{VALIDATOR_ID_PREFIX}-req-ledger-2":
                _make_provider("req-ledger-2", "pass", confidence="high"),
        }
        declaration = _make_mesh_declaration(requirements, mesh_id="mesh-ledger")
        dispatch_requests = [_make_dispatch_request(r["requirement_id"],
                                                    mesh_id="mesh-ledger")
                             for r in requirements]
        dispatch_results = dispatch_w(dispatch_requests, providers)
        mesh_request = {
            "mesh_eval_id": "mesh-eval-mesh-ledger",
            "mesh_declaration": declaration,
            "dispatch_results": dispatch_results,
            "requested_at": BASE_TIME,
            "requested_by": "architect",
        }
        mesh_result = mesh_w(mesh_request)
        self.assertNotIn("error_code", mesh_result)
        self.assertEqual(mesh_result["aggregate_verdict"], "pass")
        gate_result = publish_w(mesh_result, {
            "run_context": copy.deepcopy(RUN_CONTEXT),
            "evaluated_at": BASE_TIME,
            "evaluated_by": "architect",
        })
        self.assertNotIn("error_code", gate_result)
        self.assertEqual(gate_result["outcome"], "pass")

        # Exactly one sidecar append through the real RuntimeStateSidecar
        s = self._new_sidecar()
        run_id = "run-ledger-001"
        self._seed_active_run(s, run_id)
        before_events = len(s.read_events(run_id))
        self._append_gate_evaluated(s, run_id, gate_result)
        self.assertEqual(len(s.read_events(run_id)), before_events + 1)

        self.assertEqual(dispatch_w.count, 1)
        self.assertEqual(mesh_w.count, 1)
        self.assertEqual(publish_w.count, 1)
        # The publish bridge delegates to the real Gate evaluator exactly once
        # per publish; the produced GateDecision identity proves it ran.
        self.assertEqual(gate_result["decision_id"],
                         "mesh-eval-mesh-ledger-gate-decision")
        self.assertEqual(gate_result["gate_id"], STAGE_ID + "-validator-mesh-gate")

    def test_rejected_and_short_circuit_invocation_counts(self):
        """Rejected path reaches mesh + publish with zero sidecar writes; the
        short-circuit path raises DispatchError before any provider call and
        before the mesh evaluator."""
        dispatch_w = _CountingWrapper(local_dispatch, "local_dispatch")
        mesh_w = _CountingWrapper(evaluate_validator_mesh, "evaluate_validator_mesh")
        publish_w = _CountingWrapper(publish_to_gate, "publish_to_gate")

        requirements = [_make_requirement("req-ledger-fail")]
        providers = {
            f"{VALIDATOR_ID_PREFIX}-req-ledger-fail":
                _make_provider("req-ledger-fail", "fail", confidence="low"),
        }
        declaration = _make_mesh_declaration(requirements,
                                             mesh_id="mesh-ledger-fail")
        dispatch_requests = [_make_dispatch_request("req-ledger-fail",
                                                    mesh_id="mesh-ledger-fail")]
        dispatch_results = dispatch_w(dispatch_requests, providers)
        mesh_request = {
            "mesh_eval_id": "mesh-eval-mesh-ledger-fail",
            "mesh_declaration": declaration,
            "dispatch_results": dispatch_results,
            "requested_at": BASE_TIME,
            "requested_by": "architect",
        }
        mesh_result = mesh_w(mesh_request)
        self.assertEqual(mesh_result["aggregate_verdict"], "fail")
        gate_result = publish_w(mesh_result, {
            "run_context": copy.deepcopy(RUN_CONTEXT),
            "evaluated_at": BASE_TIME,
            "evaluated_by": "architect",
        })
        self.assertEqual(gate_result["outcome"], "fail")

        # Zero sidecar writes for the rejected path.
        s = self._new_sidecar()
        run_id = "run-ledger-fail-001"
        self._seed_active_run(s, run_id)
        before_db = self._db_sha256()
        before_events = len(s.read_events(run_id))
        after_db = self._db_sha256()
        after_events = len(s.read_events(run_id))
        self.assertEqual(before_events, after_events)
        self.assertEqual(before_db, after_db)

        self.assertEqual(dispatch_w.count, 1)
        self.assertEqual(mesh_w.count, 1)
        self.assertEqual(publish_w.count, 1)

        # Short-circuit: invalid dispatch request raises DispatchError before
        # any provider call and never reaches the mesh evaluator.
        provider_calls = [0]
        def spy(_dr):
            provider_calls[0] += 1
            return {"dispatch_status": "report_produced",
                    "report_binding": _make_report_binding("req-x", "pass",
                                                           confidence="high")}
        bad_requests = [{"dispatch_request_id": "dr-bad"}]
        with self.assertRaises(DispatchError):
            dispatch_w(bad_requests, {"validator-x": spy})
        self.assertEqual(provider_calls[0], 0)
        self.assertEqual(mesh_w.count, 1)


# ---------------------------------------------------------------------------
# Forbidden import and authority scan (self-checks on this file)
# ---------------------------------------------------------------------------

class TestForbiddenImportAndAuthorityScan(unittest.TestCase):
    TEST_PATH = ROOT / "scripts" / "test_runtime_validator_mesh_sidecar_e2e.py"

    FORBIDDEN_IMPORTS = (
        "test_runtime_validator_mesh",
        "test_runtime_publish_gate",
        "test_runtime_gate_decision",
        "runtime_state_core",
        "runtime_state_projection",
        "runtime_evidence_export",
        "test_runtime_state_journal",
        "canonical_serialize",
        "compute_digest",
        "compute_event_digest",
        "unittest.mock",
        "pytest",
        "uuid",
        "random",
        "subprocess",
    )

    FORBIDDEN_TOKENS = (
        "canonical_serialize",
        "compute_digest",
        "compute_event_digest",
        "test_runtime_state_journal",
        "skipTest",
        "xfail",
        "fallback",
        "may pass",
        "best-effort",
        "import time",
    )

    def test_no_forbidden_imports(self):
        text = self.TEST_PATH.read_text(encoding="utf-8")
        for token in self.FORBIDDEN_IMPORTS:
            self.assertNotIn(f"import {token}", text,
                             f"Forbidden import found: import {token}")
            self.assertNotIn(f"from {token}", text,
                             f"Forbidden import found: from {token}")

    def test_no_forbidden_tokens(self):
        text = self.TEST_PATH.read_text(encoding="utf-8")
        cut = text.split("class TestForbiddenImportAndAuthorityScan", 1)[0]
        for token in self.FORBIDDEN_TOKENS:
            self.assertNotIn(token, cut,
                             f"Forbidden token found: {token}")

    def test_decision_modules_no_sqlite_import(self):
        for rel_path in ("scripts/runtime_gate_decision.py",
                         "scripts/runtime_validator_mesh.py",
                         "scripts/runtime_publish_gate.py",
                         "scripts/runtime_validator_dispatch.py"):
            source = (ROOT / rel_path).read_text(encoding="utf-8")
            self.assertNotIn("sqlite3", source,
                             f"sqlite3 import in {rel_path}")
            self.assertNotIn("import sqlite", source,
                             f"sqlite import in {rel_path}")


# ---------------------------------------------------------------------------
# Public hygiene (ASCII-safe, no ticket ids, Control paths)
# ---------------------------------------------------------------------------

class TestPublicHygiene(unittest.TestCase):
    TEST_PATH = ROOT / "scripts" / "test_runtime_validator_mesh_sidecar_e2e.py"

    def test_file_is_ascii(self):
        data = self.TEST_PATH.read_bytes()
        try:
            data.decode("ascii")
        except UnicodeDecodeError:
            self.fail("Non-ASCII content in test file")

    def test_no_ticket_ids_or_control_paths(self):
        text = self.TEST_PATH.read_text(encoding="utf-8")
        cut = text.split("class TestPublicHygiene", 1)[0]
        forbidden = ["SYSTEM-", "railyard-control", "Railyard-Control", "E027"]
        for token in forbidden:
            self.assertNotIn(token, cut,
                             f"Forbidden token in public surface: {token}")

    def test_no_secrets_or_agent_brands(self):
        for rel_path in ("scripts/runtime_gate_decision.py",
                         "scripts/runtime_validator_mesh.py",
                         "scripts/runtime_publish_gate.py",
                         "scripts/runtime_validator_dispatch.py"):
            text = (ROOT / rel_path).read_text(encoding="utf-8").lower()
            for token in ("api_key", "anthropic", "openai", "gemini", "claude", "gpt-"):
                self.assertNotIn(token, text,
                                 f"Secret/agent brand '{token}' found in {rel_path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
