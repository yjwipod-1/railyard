"""Differential and adversarial tests for runtime_state_core against frozen contract v0.9.0.

This suite:
- Tests every frozen event type against canonical vectors from the conformance fixture
- Verifies core behavior reproduces the frozen prior test behavior
- Runs adversarial coordinated drift mutations that the frozen prior test catches
- Must NOT modify the frozen test file; runs it as external verification
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import unittest

# Add parent to path so we can import from scripts
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.runtime_state_core import (
    canonical_serialize,
    compute_digest,
    validate_append_request,
    validate_payload,
    evaluate_append_decision,
    compute_event_digest,
    apply_reducer,
    compute_projection_digest,
    sign_receipt,
    verify_receipt,
    verify_stream_integrity,
    initial_projection,
    compute_completion_digest,
    validate_lineage,
    validate_stage_graph,
    bind_store_event_metadata,
    ZERO_DIGEST,
    FROZEN_EVENT_TYPES,
    FROZEN_EVENT_SCHEMAS,
    FROZEN_REDUCERS,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "examples" / "runtime_state_contract_fixtures" / "conformance.json"
FROZEN_TEST = ROOT / "scripts" / "test_runtime_state_contract.py"

FROZEN_CONTRACT_SHA256 = "016db98f28eb5fc291f6fc608f761d733f9fa6652fb12ea7af78166a0a54c9b1"
FROZEN_FIXTURE_SHA256 = "5fa055cb9353e6172b304de211208f8a6dfc017822fc4ce45f32576e10667e45"
FROZEN_TEST_SHA256 = "99c7af2af12a0fd9bccd4b673eeaa68b8d5df076e68d132e0d5b3804f0d2f540"

DEFAULT_KEY = b"runtime-state-conformance-key"

ORACLE_SPEC = importlib.util.spec_from_file_location("runtime_state_contract_oracle", FROZEN_TEST)
ORACLE = importlib.util.module_from_spec(ORACLE_SPEC)
ORACLE_SPEC.loader.exec_module(ORACLE)


def load_fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _public_visibility_contributor(contributor_id, kind, artifact_id, artifact_kind):
    ref = {"artifact_id": artifact_id, "artifact_kind": artifact_kind}
    return {
        "contributor_id": contributor_id,
        "contributor_kind": kind,
        "contributor_ref": ref,
        "asserted_visibility": "public",
        "authority": "Frozen core conformance input.",
        "classification_evidence": [copy.deepcopy(ref)],
    }


def _public_visibility_context():
    trigger = _public_visibility_contributor("trigger-public", "trigger_provenance", "ticket-test", "ticket")
    policy = _public_visibility_contributor("policy-public", "project_policy", "policy-public", "policy")
    contract = _public_visibility_contributor("contract-public", "governing_contract", "runtime-state-contract", "contract")
    contributors = [trigger, policy, contract]
    return {
        "trigger_visibility": trigger,
        "policy_contributors": [policy],
        "contract_contributors": [contract],
        "run_visibility_resolution": {
            "resolution_id": "run-public-resolution",
            "resolved_at": "2026-01-01T00:00:00Z",
            "contributors": copy.deepcopy(contributors),
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
    }


# ---------------------------------------------------------------------------
# Helper: build a minimal valid AppendRequest for each event type
# ---------------------------------------------------------------------------

def _make_run_created_payload():
    """Minimal valid run.created payload."""
    return {
        "run_provenance": {
            "origin_artifact": {"artifact_id": "test-001", "artifact_kind": "test"},
            "governing_contracts": [{"artifact_id": "runtime-architecture", "artifact_kind": "contract", "artifact_version": "0.8.0"}],
            "additional_sources": [],
        },
        "trigger": "ticket",
        "executor_identity": "runner-1",
        "run_ordinal": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "stage_graph": {
            "graph_id": "simple-graph",
            "stages": [
                {
                    "stage_id": "build",
                    "name": "Build",
                    "required": True,
                    "status": "pending",
                    "gates": [
                        {
                            "gate_id": "check-build",
                            "gate_type": "validator",
                            "required": True,
                            "failure_behavior": "halt_run",
                            "contract_ref": {
                                "artifact_id": "test-contract",
                                "artifact_kind": "contract",
                                "artifact_version": "0.8.2",
                                "locator": "references/runtime-state-contract.md#8-required-stage-and-required-gate-invariants",
                            },
                        }
                    ],
                }
            ],
            "edges": [],
            "entry_stages": ["build"],
            "terminal_stages": ["build"],
        },
        "visibility_context": _public_visibility_context(),
    }


def _make_request(event_type, payload, run_id="test-run", prev_digest=None, head_order=0):
    """Build a minimal valid AppendRequest."""
    if prev_digest is None:
        prev_digest = ZERO_DIGEST
    return {
        "run_id": run_id,
        "event_type": event_type,
        "payload": payload,
        "causation_chain": [],
        "actor_role": "runner",
        "actor_identity": "runner-1",
        "trigger_artifact": {"artifact_id": "ticket-test", "artifact_kind": "ticket"},
        "reason": "test",
        "recommended_action": "none",
        "expected_stream_head": {"event_order": head_order, "content_digest": prev_digest},
        "client_event_id": f"test-{event_type}-{head_order+1}",
        "prev_event_digest": prev_digest,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFrozenInputIntegrity(unittest.TestCase):
    """Verify frozen inputs have not been modified."""

    def test_contract_hash(self):
        h = hashlib.sha256((ROOT / "references" / "runtime-state-contract.md").read_bytes()).hexdigest()
        self.assertEqual(h, FROZEN_CONTRACT_SHA256)

    def test_fixture_hash(self):
        h = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
        self.assertEqual(h, FROZEN_FIXTURE_SHA256)

    def test_frozen_test_hash(self):
        h = hashlib.sha256(FROZEN_TEST.read_bytes()).hexdigest()
        self.assertEqual(h, FROZEN_TEST_SHA256)


class TestCanonicalSerialization(unittest.TestCase):
    """Verify JCS canonical serialization matches frozen vectors."""

    def setUp(self):
        self.fixture = load_fixture()
        self.vectors = self.fixture["canonical_serialization"]["vectors"]

    def test_empty_object_digest(self):
        self.assertEqual(compute_digest({}), "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a")

    def test_empty_object_bytes(self):
        self.assertEqual(canonical_serialize({}), b"{}")

    def test_typed_nested_digest(self):
        obj = {
            "array": [None, True, False, 0, -7, 42],
            "delimiter_string": "a:b,c{}[]",
            "nested": {"a": [1, {"k": "v"}], "z": None},
        }
        self.assertEqual(compute_digest(obj), "sha256:0b8cd4fc84058be18af361dd70b65e9d44e971cd26dede99188690d11323a816")

    def test_escaping_key_order_digest(self):
        obj = {"z": "line\nquote\"slash\\", "a": {"b": 2, "a": 1}}
        self.assertEqual(compute_digest(obj), "sha256:dbf612d31a596d6c4651103b341aa2c84214ea296d3f5b79197bc14dde18bde7")

    def test_utf16_key_order(self):
        # U+10000 (non-BMP surrogate pair D800 DC00) sorts before U+E000 (BMP)
        obj = {u"\ue000": "bmp-private-use", u"\U00010000": "non-bmp"}
        self.assertEqual(compute_digest(obj), "sha256:188d67278618861de0e2a3dc1f8a3fd25b5a84c186cd332197b2ce466134d180")

    def test_injective_serialization_no_collision(self):
        """Different values produce different digests."""
        a = compute_digest({"a": 1, "b": 2})
        b = compute_digest({"b": 2, "a": 1})
        self.assertEqual(a, b)  # same input, same digest

        c = compute_digest({"a": 1, "b": 3})
        self.assertNotEqual(a, c)  # different value, different digest


class TestPayloadValidation(unittest.TestCase):
    """Validate payloads for all 18 event types."""

    def test_all_event_types_have_schemas(self):
        schemas = set(FROZEN_EVENT_SCHEMAS.keys())
        self.assertEqual(schemas, FROZEN_EVENT_TYPES)

    def test_all_event_types_have_reducers(self):
        reducers = set(FROZEN_REDUCERS.keys())
        self.assertEqual(reducers, FROZEN_EVENT_TYPES)

    def test_frozen_schema_and_reducer_maps_match_fixture_exactly(self):
        fixture = load_fixture()
        self.assertEqual(FROZEN_EVENT_SCHEMAS, fixture["event_schemas"])
        self.assertEqual(FROZEN_REDUCERS, fixture["reducers"])

    def test_run_created_required_fields(self):
        payload = _make_run_created_payload()
        r = validate_payload("run.created", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_created_missing_required(self):
        r = validate_payload("run.created", {})
        self.assertFalse(r["valid"])

    def test_run_started_required_fields(self):
        r = validate_payload("run.started", {"started_at": "2026-01-01T00:00:00Z", "executor_identity": "runner-1"})
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_started_missing_executor(self):
        r = validate_payload("run.started", {"started_at": "2026-01-01T00:00:00Z"})
        self.assertFalse(r["valid"])

    def test_run_stage_started_required(self):
        r = validate_payload("run.stage.started", {
            "stage_id": "build", "started_at": "2026-01-01T00:00:00Z",
            "entry_evidence": [{"artifact_id": "ev", "artifact_kind": "test"}]
        })
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_stage_completed_required(self):
        r = validate_payload("run.stage.completed", {
            "stage_id": "build", "completed_at": "2026-01-01T00:00:00Z",
            "gate_decisions": ["dec-1"], "artifacts_produced": []
        })
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_stage_failed_required(self):
        payload = {
            "stage_id": "build", "failed_at": "2026-01-01T00:00:00Z",
            "error": {"code": "E001", "message": "failed"},
            "failure_category": "command_failed",
            "failure_is_transient": False,
            "failure_is_deterministic": False,
            "artifacts_produced_before_failure": [],
            "retry_eligible": True,
        }
        r = validate_payload("run.stage.failed", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_stage_skipped_required(self):
        payload = {
            "stage_id": "optional", "skipped_at": "2026-01-01T00:00:00Z",
            "authorized_by": "human", "reason": "not needed",
            "authorizing_intervention_id": "int-1"
        }
        r = validate_payload("run.stage.skipped", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_gate_evaluated_required(self):
        payload = {
            "stage_id": "build", "gate_id": "check-build",
            "decision_id": "dec-1", "outcome": "pass",
            "execution_mode": "full",
            "evaluated_at": "2026-01-01T00:00:00Z",
            "evaluated_by": "validator-1",
            "evidence": [{"artifact_id": "ev-1", "artifact_kind": "report"}],
        }
        r = validate_payload("run.gate.evaluated", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_gate_blocked_required(self):
        payload = {
            "stage_id": "build", "gate_id": "check-build",
            "decision_id": "dec-1", "outcome": "blocked",
            "execution_mode": "full",
            "evaluated_at": "2026-01-01T00:00:00Z",
            "evaluated_by": "validator-1",
            "evidence": [],
            "blocked_reason": "missing data",
            "required_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
        }
        r = validate_payload("run.gate.blocked", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_gate_overridden_required(self):
        payload = {
            "stage_id": "build", "gate_id": "optional-gate",
            "new_decision_id": "dec-2", "original_decision_id": "dec-1",
            "outcome": "pass", "execution_mode": "full",
            "evaluated_at": "2026-01-01T00:00:00Z",
            "evaluated_by": "architect-1", "evidence": [],
            "override_reason": "risk accepted",
            "authorized_by": "architect",
            "authorized_at": "2026-01-01T00:00:00Z",
            "authorizing_intervention_id": "int-1",
        }
        r = validate_payload("run.gate.overridden", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_retry_initiated_required(self):
        payload = {
            "new_run_id": "child-run-1",
            "lineage": {
                "parent_run_id": "parent-run", "lineage_kind": "retry",
                "lineage_reason": "fix", "parent_status": "failed",
                "parent_boundary_event_id": "evt-5",
                "parent_boundary_event_type": "run.failed",
                "parent_boundary_event_order": 5,
            },
            "retry_strategy": "full",
            "current_retry_count": 1,
            "max_retries": 3,
            "failure_category": "command_failed",
            "authorized_by": "architect",
            "authorized_at": "2026-01-01T00:00:00Z",
        }
        r = validate_payload("run.retry.initiated", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_resumed_required(self):
        payload = {
            "new_run_id": "child-run-1",
            "lineage": {
                "parent_run_id": "parent-run", "lineage_kind": "resume",
                "lineage_reason": "continue", "parent_status": "interrupted",
                "parent_boundary_event_id": "evt-5",
                "parent_boundary_event_type": "run.interrupted",
                "parent_boundary_event_order": 5,
            },
            "checkpoint_event_order": 3,
            "recovery_action": "replay_from_checkpoint",
            "authorized_by": "architect",
            "authorized_at": "2026-01-01T00:00:00Z",
        }
        r = validate_payload("run.resumed", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_redesign_required(self):
        payload = {
            "new_run_id": "child-run-1",
            "lineage": {
                "parent_run_id": "parent-run", "lineage_kind": "redesign",
                "lineage_reason": "rework", "parent_status": "failed",
                "parent_boundary_event_id": "evt-5",
                "parent_boundary_event_type": "run.failed",
                "parent_boundary_event_order": 5,
            },
            "revised_stage_graph": {
                "graph_id": "revised",
                "stages": [{"stage_id": "test", "name": "Test", "required": True, "status": "pending"}],
                "edges": [], "entry_stages": ["test"], "terminal_stages": ["test"],
            },
            "authorized_by": "architect",
            "authorized_at": "2026-01-01T00:00:00Z",
        }
        r = validate_payload("run.redesign", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_intervention_required(self):
        payload = {
            "intervention_id": "int-1",
            "intervention_type": "provide_evidence",
            "authorized_by": "architect",
            "reason": "new evidence",
            "evidence": [{"artifact_id": "ev-1", "artifact_kind": "report"}],
        }
        r = validate_payload("run.intervention", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_terminated_required(self):
        payload = {
            "terminated_at": "2026-01-01T00:00:00Z",
            "terminated_by": "architect",
            "termination_reason": "no longer needed",
            "from_status": "pending",
            "terminal_status": "failed",
        }
        r = validate_payload("run.terminated", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_completed_required(self):
        payload = {
            "completed_at": "2026-01-01T00:00:00Z",
            "terminal_stages_completed": ["build"],
            "final_projection_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001",
            "total_event_count": 5,
        }
        r = validate_payload("run.completed", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_failed_required(self):
        payload = {
            "failed_at": "2026-01-01T00:00:00Z",
            "failed_stage_id": "build",
            "error": {"code": "E001", "message": "failed"},
            "failure_category": "command_failed",
            "failure_is_transient": False,
            "failure_is_deterministic": False,
            "retry_eligible": True,
        }
        r = validate_payload("run.failed", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_blocked_required(self):
        payload = {
            "blocked_at": "2026-01-01T00:00:00Z",
            "blocked_reason": "external dependency",
            "resolution_paths": ["more_evidence"],
            "required_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
        }
        r = validate_payload("run.blocked", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_run_interrupted_required(self):
        payload = {
            "interrupted_at": "2026-01-01T00:00:00Z",
            "last_event_order": 3,
            "interruption_cause": "session_lost",
            "checkpoint_available": False,
        }
        r = validate_payload("run.interrupted", payload)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_invalid_event_type(self):
        r = validate_payload("run.nonexistent", {})
        self.assertFalse(r["valid"])
        self.assertEqual(r["rule_id"], "event-schema-missing")

    def test_unexpected_extra_field(self):
        payload = _make_run_created_payload()
        payload["extra_field"] = "unexpected"
        r = validate_payload("run.created", payload)
        self.assertFalse(r["valid"])
        self.assertIn("extra", r["rule_id"])

    def test_enum_validation(self):
        payload = _make_run_created_payload()
        payload["trigger"] = "invalid_trigger"
        r = validate_payload("run.created", payload)
        self.assertFalse(r["valid"])

    def test_timestamp_no_timezone_rejected(self):
        r = validate_payload("run.started", {"started_at": "2026-01-01", "executor_identity": "test"})
        self.assertFalse(r["valid"])

    def test_gate_blocked_must_have_outcome_blocked(self):
        payload = {
            "stage_id": "build", "gate_id": "check-build",
            "decision_id": "dec-1", "outcome": "pass",
            "execution_mode": "full",
            "evaluated_at": "2026-01-01T00:00:00Z",
            "evaluated_by": "test", "evidence": [],
            "blocked_reason": "x", "required_evidence": [],
        }
        r = validate_payload("run.gate.blocked", payload)
        self.assertFalse(r["valid"])

    def test_gate_overridden_outcome_must_be_pass(self):
        payload = {
            "stage_id": "build", "gate_id": "g1",
            "new_decision_id": "d2", "original_decision_id": "d1",
            "outcome": "fail", "execution_mode": "full",
            "evaluated_at": "2026-01-01T00:00:00Z",
            "evaluated_by": "a", "evidence": [],
            "override_reason": "r", "authorized_by": "architect",
            "authorized_at": "2026-01-01T00:00:00Z",
            "authorizing_intervention_id": "i1",
        }
        r = validate_payload("run.gate.overridden", payload)
        self.assertFalse(r["valid"])

    def test_degradation_note_required_for_non_full(self):
        payload = {
            "stage_id": "build", "gate_id": "check-build",
            "decision_id": "dec-1", "outcome": "pass",
            "execution_mode": "degraded_transport",
            "evaluated_at": "2026-01-01T00:00:00Z",
            "evaluated_by": "test",
            "evidence": [{"artifact_id": "ev-1", "artifact_kind": "report"}],
        }
        r = validate_payload("run.gate.evaluated", payload)
        self.assertFalse(r["valid"])
        self.assertIn("gate-degradation-note-required", r["errors"][0])

    def test_degradation_note_forbidden_for_full(self):
        payload = {
            "stage_id": "build", "gate_id": "check-build",
            "decision_id": "dec-1", "outcome": "pass",
            "execution_mode": "full",
            "evaluated_at": "2026-01-01T00:00:00Z",
            "evaluated_by": "test",
            "evidence": [{"artifact_id": "ev-1", "artifact_kind": "report"}],
            "degradation_note": "should not be here",
        }
        r = validate_payload("run.gate.evaluated", payload)
        self.assertFalse(r["valid"])


class TestAppendRequestValidation(unittest.TestCase):
    def test_valid_minimal_request(self):
        payload = _make_run_created_payload()
        req = _make_request("run.created", payload)
        r = validate_append_request(req)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_missing_required_field(self):
        req = _make_request("run.created", _make_run_created_payload())
        del req["run_id"]
        r = validate_append_request(req)
        self.assertFalse(r["valid"])

    def test_invalid_event_type(self):
        req = _make_request("invalid.type", {})
        r = validate_append_request(req)
        self.assertFalse(r["valid"])

    def test_missing_causation(self):
        req = _make_request("run.created", _make_run_created_payload())
        del req["causation_chain"]
        r = validate_append_request(req)
        self.assertFalse(r["valid"])

    def test_both_causation_fields(self):
        req = _make_request("run.created", _make_run_created_payload())
        req["causation_id"] = "c1"
        r = validate_append_request(req)
        self.assertFalse(r["valid"])


class TestAppendDecisionEvaluation(unittest.TestCase):
    def setUp(self):
        self.payload = _make_run_created_payload()
        self.req = _make_request("run.created", self.payload, run_id="test-decision")

    def test_ok_for_genesis_with_empty_stream(self):
        current_head = {"event_order": 0, "content_digest": ZERO_DIGEST}
        # For genesis, prev_event_digest must be ZERO_DIGEST
        req = _make_request("run.created", self.payload, run_id="test-decision",
                            prev_digest=ZERO_DIGEST, head_order=0)
        decision = evaluate_append_decision([], req, current_head)
        self.assertEqual(decision["code"], "ok", msg=decision)

    def test_stale_head(self):
        current_head = {"event_order": 0, "content_digest": ZERO_DIGEST}
        req = _make_request("run.created", self.payload, run_id="test-decision",
                            prev_digest=ZERO_DIGEST, head_order=0)
        # Change the expected head to cause stale rejection
        stale_head = {"event_order": 1, "content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001"}
        req["expected_stream_head"] = stale_head
        decision = evaluate_append_decision([], req, current_head)
        self.assertEqual(decision["code"], "stale_head")

    def test_hash_chain_link(self):
        # Current head uses ZERO_DIGEST, but prev_event_digest is something else
        # expected_stream_head must match current_head to pass stale_head check
        current_head = {"event_order": 0, "content_digest": ZERO_DIGEST}
        req = _make_request("run.created", self.payload, run_id="test-chain",
                            prev_digest="sha256:0000000000000000000000000000000000000000000000000000000000000001",
                            head_order=0)
        # expected_stream_head was set by _make_request with prev_digest; fix it to match current_head
        req["expected_stream_head"] = {"event_order": 0, "content_digest": ZERO_DIGEST}
        decision = evaluate_append_decision([], req, current_head)
        self.assertEqual(decision["code"], "hash_chain_link", msg=decision)

    def test_invalid_request_no_causation(self):
        current_head = {"event_order": 0, "content_digest": ZERO_DIGEST}
        req = _make_request("run.created", self.payload, prev_digest=ZERO_DIGEST, head_order=0)
        del req["causation_chain"]
        decision = evaluate_append_decision([], req, current_head)
        self.assertEqual(decision["code"], "invalid_request")


class TestReducers(unittest.TestCase):
    """Test reducers for all event types."""

    def setUp(self):
        self.payload = _make_run_created_payload()
        self.run_id = "test-reducer"

    def test_run_created_initial_projection(self):
        req = _make_request("run.created", self.payload, run_id=self.run_id)
        proj = apply_reducer(None, req, "run.created")
        self.assertEqual(proj["status"], "pending")
        self.assertEqual(proj["run_id"], self.run_id)
        self.assertEqual(proj["events_count"], 1)
        self.assertIn("stage_states", proj)
        self.assertIn("build", proj["stage_states"])
        self.assertIn("interventions", proj)
        self.assertIn("audit_events", proj)
        self.assertIn("child_actions", proj)

    def test_run_started(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        started_payload = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
        req = _make_request("run.started", started_payload, run_id=self.run_id, prev_digest=ZERO_DIGEST)
        proj2 = apply_reducer(proj, req, "run.started")
        self.assertEqual(proj2["status"], "active")
        self.assertEqual(proj2["events_count"], 2)

    def test_run_started_wrong_executor(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        started_payload = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "wrong-runner"}
        req = _make_request("run.started", started_payload, run_id=self.run_id, prev_digest=ZERO_DIGEST)
        with self.assertRaises(ValueError):
            apply_reducer(proj, req, "run.started")

    def test_run_stage_started(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.started", {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, run_id=self.run_id))
        stage_req = _make_request("run.stage.started", {
            "stage_id": "build", "started_at": "2026-01-01T00:00:02Z",
            "entry_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
        }, run_id=self.run_id)
        proj = apply_reducer(proj, stage_req, "run.stage.started")
        self.assertEqual(proj["stage_states"]["build"]["status"], "active")
        self.assertEqual(proj["current_stage_id"], "build")

    def test_run_gate_evaluated(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.started", {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.stage.started", {
            "stage_id": "build", "started_at": "2026-01-01T00:00:02Z",
            "entry_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
        }, run_id=self.run_id))
        gate_req = _make_request("run.gate.evaluated", {
            "stage_id": "build", "gate_id": "check-build",
            "decision_id": "dec-1", "outcome": "pass",
            "execution_mode": "full",
            "evaluated_at": "2026-01-01T00:00:03Z",
            "evaluated_by": "validator-1",
            "evidence": [{"artifact_id": "report-1", "artifact_kind": "validation_report"}],
        }, run_id=self.run_id)
        proj = apply_reducer(proj, gate_req, "run.gate.evaluated")
        self.assertIn("check-build", proj["stage_states"]["build"]["gate_decisions"])
        self.assertEqual(proj["stage_states"]["build"]["gate_decisions"]["check-build"]["outcome"], "pass")

    def test_run_gate_blocked(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.started", {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.stage.started", {
            "stage_id": "build", "started_at": "2026-01-01T00:00:02Z",
            "entry_evidence": [],
        }, run_id=self.run_id))
        gate_req = _make_request("run.gate.blocked", {
            "stage_id": "build", "gate_id": "check-build",
            "decision_id": "dec-1", "outcome": "blocked",
            "execution_mode": "full",
            "evaluated_at": "2026-01-01T00:00:03Z",
            "evaluated_by": "validator-1",
            "evidence": [],
            "blocked_reason": "missing data",
            "required_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
        }, run_id=self.run_id)
        proj = apply_reducer(proj, gate_req, "run.gate.blocked")
        self.assertEqual(proj["stage_states"]["build"]["gate_decisions"]["check-build"]["outcome"], "blocked")

    def test_run_intervention(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        # Need to start to be active for intervention
        proj = apply_reducer(proj, _make_request("run.started", {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, run_id=self.run_id))
        int_req = _make_request("run.intervention", {
            "intervention_id": "int-1",
            "intervention_type": "provide_evidence",
            "authorized_by": "architect",
            "reason": "new evidence",
            "evidence": [{"artifact_id": "ev-1", "artifact_kind": "report"}],
        }, run_id=self.run_id)
        proj = apply_reducer(proj, int_req, "run.intervention")
        self.assertEqual(len(proj["interventions"]), 1)
        self.assertEqual(proj["interventions"][0]["intervention_id"], "int-1")

    def test_run_failed(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.started", {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.stage.started", {
            "stage_id": "build", "started_at": "2026-01-01T00:00:02Z",
            "entry_evidence": [],
        }, run_id=self.run_id))
        fail_req = _make_request("run.failed", {
            "failed_at": "2026-01-01T00:00:03Z",
            "failed_stage_id": "build",
            "error": {"code": "E001", "message": "boom"},
            "failure_category": "command_failed",
            "failure_is_transient": False,
            "failure_is_deterministic": False,
            "retry_eligible": True,
        }, run_id=self.run_id)
        proj = apply_reducer(proj, fail_req, "run.failed")
        self.assertEqual(proj["status"], "failed")
        self.assertEqual(proj["failed_stage_id"], "build")

    def test_run_blocked(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.started", {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, run_id=self.run_id))
        block_req = _make_request("run.blocked", {
            "blocked_at": "2026-01-01T00:00:02Z",
            "blocked_reason": "external",
            "resolution_paths": ["more_evidence"],
            "required_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
        }, run_id=self.run_id)
        proj = apply_reducer(proj, block_req, "run.blocked")
        self.assertEqual(proj["status"], "blocked")

    def test_run_interrupted(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.started", {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, run_id=self.run_id))
        # events_count is 2 now
        int_req = _make_request("run.interrupted", {
            "interrupted_at": "2026-01-01T00:00:02Z",
            "last_event_order": 2,
            "interruption_cause": "session_lost",
            "checkpoint_available": False,
        }, run_id=self.run_id)
        proj = apply_reducer(proj, int_req, "run.interrupted")
        self.assertEqual(proj["status"], "interrupted")

    def test_run_terminated(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        # Pending -> terminated
        term_req = _make_request("run.terminated", {
            "terminated_at": "2026-01-01T00:00:01Z",
            "terminated_by": "architect",
            "termination_reason": "no longer needed",
            "from_status": "pending",
            "terminal_status": "failed",
        }, run_id=self.run_id)
        proj = apply_reducer(proj, term_req, "run.terminated")
        self.assertEqual(proj["status"], "failed")

    def test_run_retry_initiated(self):
        # Need a failed run first
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.started", {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.stage.started", {
            "stage_id": "build", "started_at": "2026-01-01T00:00:02Z",
            "entry_evidence": [],
        }, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.failed", {
            "failed_at": "2026-01-01T00:00:03Z",
            "failed_stage_id": "build",
            "error": {"code": "E001", "message": "boom"},
            "failure_category": "command_failed",
            "failure_is_transient": True,
            "failure_is_deterministic": True,
            "retry_eligible": True,
        }, run_id=self.run_id))
        # mock latest event metadata
        proj["latest_event_id"] = "evt-4"
        proj["latest_event_type"] = "run.failed"
        proj["latest_event_order"] = 4
        proj.pop("latest_terminal_status", None)
        retry_req = _make_request("run.retry.initiated", {
            "new_run_id": "child-1",
            "lineage": {
                "parent_run_id": self.run_id,
                "lineage_kind": "retry",
                "lineage_reason": "fix",
                "parent_status": "failed",
                "parent_boundary_event_id": "evt-4",
                "parent_boundary_event_type": "run.failed",
                "parent_boundary_event_order": 4,
            },
            "retry_strategy": "full",
            "current_retry_count": 1,
            "max_retries": 3,
            "failure_category": "command_failed",
            "authorized_by": "architect",
            "authorized_at": "2026-01-01T00:00:04Z",
        }, run_id=self.run_id)
        proj = apply_reducer(proj, retry_req, "run.retry.initiated")
        self.assertEqual(len(proj["child_actions"]), 1)
        self.assertEqual(proj["child_actions"][0]["new_run_id"], "child-1")

    def test_run_resumed(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.started", {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.interrupted", {
            "interrupted_at": "2026-01-01T00:00:02Z",
            "last_event_order": 2,
            "interruption_cause": "session_lost",
            "checkpoint_available": True,
        }, run_id=self.run_id))
        proj["latest_event_id"] = "evt-3"
        proj["latest_event_type"] = "run.interrupted"
        proj["latest_event_order"] = 3
        proj.pop("latest_terminal_status", None)
        resume_req = _make_request("run.resumed", {
            "new_run_id": "child-1",
            "lineage": {
                "parent_run_id": self.run_id,
                "lineage_kind": "resume",
                "lineage_reason": "continue",
                "parent_status": "interrupted",
                "parent_boundary_event_id": "evt-3",
                "parent_boundary_event_type": "run.interrupted",
                "parent_boundary_event_order": 3,
            },
            "checkpoint_event_order": 2,
            "recovery_action": "replay_from_checkpoint",
            "authorized_by": "architect",
            "authorized_at": "2026-01-01T00:00:03Z",
        }, run_id=self.run_id)
        proj = apply_reducer(proj, resume_req, "run.resumed")
        self.assertEqual(len(proj["child_actions"]), 1)

    def test_run_redesign(self):
        proj = apply_reducer(None, _make_request("run.created", self.payload, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.started", {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.stage.started", {
            "stage_id": "build", "started_at": "2026-01-01T00:00:02Z",
            "entry_evidence": [],
        }, run_id=self.run_id))
        proj = apply_reducer(proj, _make_request("run.failed", {
            "failed_at": "2026-01-01T00:00:03Z",
            "failed_stage_id": "build",
            "error": {"code": "E001", "message": "boom"},
            "failure_category": "command_failed",
            "failure_is_transient": False,
            "failure_is_deterministic": False,
            "retry_eligible": False,
        }, run_id=self.run_id))
        proj["latest_event_id"] = "evt-4"
        proj["latest_event_type"] = "run.failed"
        proj["latest_event_order"] = 4
        proj.pop("latest_terminal_status", None)
        redesign_payload = {
            "new_run_id": "child-1",
            "lineage": {
                "parent_run_id": self.run_id,
                "lineage_kind": "redesign",
                "lineage_reason": "rework",
                "parent_status": "failed",
                "parent_boundary_event_id": "evt-4",
                "parent_boundary_event_type": "run.failed",
                "parent_boundary_event_order": 4,
            },
            "revised_stage_graph": {
                "graph_id": "revised",
                "stages": [{"stage_id": "test", "name": "Test", "required": True, "status": "pending"}],
                "edges": [], "entry_stages": ["test"], "terminal_stages": ["test"],
            },
            "authorized_by": "architect",
            "authorized_at": "2026-01-01T00:00:04Z",
        }
        redesign_req = _make_request("run.redesign", redesign_payload, run_id=self.run_id)
        proj = apply_reducer(proj, redesign_req, "run.redesign")
        self.assertEqual(len(proj["child_actions"]), 1)
        self.assertEqual(proj["status"], "failed")


class TestReceiptSigningAndVerification(unittest.TestCase):
    def test_sign_and_verify(self):
        signed = sign_receipt("run-1", 5, "sha256:0000000000000000000000000000000000000000000000000000000000000001")
        self.assertEqual(signed["algorithm"], "HMAC-SHA256")
        self.assertEqual(signed["key_id"], "conformance-key-1")
        self.assertEqual(signed["signed_payload"]["run_id"], "run-1")
        self.assertEqual(signed["signed_payload"]["event_order"], 5)
        self.assertIn("signature", signed)
        self.assertEqual(len(signed["signature"]), 64)

        receipt = {
            "event_id": "evt-5",
            "event_order": 5,
            "stored_content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001",
            "new_stream_head": {
                "event_order": 5,
                "content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001",
            },
            "signed_receipt": signed,
        }
        r = verify_receipt(receipt, "run-1")
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_verify_wrong_run_id(self):
        signed = sign_receipt("run-1", 1, "sha256:0000000000000000000000000000000000000000000000000000000000000001")
        receipt = {
            "event_id": "evt-1", "event_order": 1,
            "stored_content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001",
            "new_stream_head": {"event_order": 1, "content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001"},
            "signed_receipt": signed,
        }
        r = verify_receipt(receipt, "run-2")
        self.assertFalse(r["valid"])

    def test_verify_tampered_signature(self):
        signed = sign_receipt("run-1", 1, "sha256:0000000000000000000000000000000000000000000000000000000000000001")
        tampered = copy.deepcopy(signed)
        tampered["signature"] = "0" * 64
        receipt = {
            "event_id": "evt-1", "event_order": 1,
            "stored_content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001",
            "new_stream_head": {"event_order": 1, "content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001"},
            "signed_receipt": tampered,
        }
        r = verify_receipt(receipt, "run-1")
        self.assertFalse(r["valid"])

    def test_verify_event_order_binding_failure(self):
        signed = sign_receipt("run-1", 1, "sha256:0000000000000000000000000000000000000000000000000000000000000001")
        receipt = {
            "event_id": "evt-1", "event_order": 2,  # Mismatched order
            "stored_content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001",
            "new_stream_head": {"event_order": 1, "content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001"},
            "signed_receipt": signed,
        }
        r = verify_receipt(receipt, "run-1")
        self.assertFalse(r["valid"])

    def test_verify_stored_content_digest_binding_failure(self):
        signed = sign_receipt("run-1", 1, "sha256:0000000000000000000000000000000000000000000000000000000000000001")
        receipt = {
            "event_id": "evt-1", "event_order": 1,
            "stored_content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000002",  # mismatched
            "new_stream_head": {"event_order": 1, "content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000001"},
            "signed_receipt": signed,
        }
        r = verify_receipt(receipt, "run-1")
        self.assertFalse(r["valid"])


class TestEventDigest(unittest.TestCase):
    def setUp(self):
        self.payload = _make_run_created_payload()

    def test_compute_event_digest_excludes_content_digest(self):
        stored = {
            "run_id": "test", "event_type": "run.created",
            "payload": self.payload, "event_order": 1,
            "event_id": "evt-1", "schema_version": "0.8.2",
            "occurred_at": "2026-01-01T00:00:00Z",
            "prior_state": {}, "next_state": {"status": "pending"},
            "prev_event_digest": ZERO_DIGEST,
            "content_digest": "bogus",
        }
        d = compute_event_digest(stored)
        self.assertTrue(d.startswith("sha256:"))
        self.assertEqual(len(d), 71)

    def test_event_digest_deterministic(self):
        stored1 = {
            "run_id": "test", "event_type": "run.created",
            "payload": self.payload, "event_order": 1,
            "event_id": "evt-1", "schema_version": "0.8.2",
            "occurred_at": "2026-01-01T00:00:00Z",
            "prior_state": {}, "next_state": {"status": "pending"},
            "prev_event_digest": ZERO_DIGEST,
        }
        d1 = compute_digest(stored1)
        d2 = compute_digest(copy.deepcopy(stored1))
        self.assertEqual(d1, d2)


class TestProjectionDigest(unittest.TestCase):
    def test_initial_projection_digest(self):
        req = _make_request("run.created", _make_run_created_payload())
        proj = initial_projection(req)
        d = compute_projection_digest(proj)
        self.assertTrue(d.startswith("sha256:"))

    def test_completion_digest(self):
        payload = _make_run_created_payload()
        req = _make_request("run.created", payload)
        proj = initial_projection(req)
        proj["completed_at"] = "2026-01-01T00:00:01Z"
        proj["terminal_stages_completed"] = ["build"]
        proj["declared_total_event_count"] = 3
        proj["status"] = "completed"
        d = compute_completion_digest(proj)
        self.assertTrue(d.startswith("sha256:"))

    def test_replay_produces_same_projection_digest(self):
        payload = _make_run_created_payload()
        proj1 = initial_projection(_make_request("run.created", payload))
        proj2 = initial_projection(_make_request("run.created", copy.deepcopy(payload)))
        self.assertEqual(compute_projection_digest(proj1), compute_projection_digest(proj2))


class TestStreamIntegrity(unittest.TestCase):
    """Stream integrity must replay with the shared metadata-binding
    helper so a metadata-complete stored stream verifies cleanly. Builds a small
    stream the way the journal write-path would (reducer then bind_store_event_metadata)
    and confirms verify_stream_integrity accepts it, and that a gap is rejected."""

    def _make_bound_event(self, run_id, event_id, event_type, event_order, payload, prior_state, prev_event_digest):
        ev = {
            "run_id": run_id,
            "event_id": event_id,
            "event_type": event_type,
            "event_order": event_order,
            "payload": payload,
            "prior_state": prior_state,
            "prev_event_digest": prev_event_digest,
        }
        proj = apply_reducer(
            None if prior_state == {} else prior_state,
            {"run_id": run_id, "event_type": event_type, "payload": payload},
        )
        proj = bind_store_event_metadata(proj, ev)
        ev["next_state"] = proj
        ev["content_digest"] = compute_digest(dict(ev))
        return ev

    def test_metadata_complete_stream_verifies(self):
        e1 = self._make_bound_event(
            "r1", "e1", "run.created", 1, _make_run_created_payload(), {}, ZERO_DIGEST)
        e2 = self._make_bound_event(
            "r1", "e2", "run.started", 2,
            {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"},
            e1["next_state"], e1["content_digest"])
        result = verify_stream_integrity([e1, e2])
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["events_verified"], 2)

    def test_gap_free_order_required(self):
        e1 = self._make_bound_event(
            "r1", "e1", "run.created", 1, _make_run_created_payload(), {}, ZERO_DIGEST)
        e2 = self._make_bound_event(
            "r1", "e2", "run.started", 2,
            {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"},
            e1["next_state"], e1["content_digest"])
        # Introduce a gap in event_order.
        broken = [e1, dict(e2, event_order=3)]
        result = verify_stream_integrity(broken)
        self.assertFalse(result["valid"])
        self.assertTrue(any("stream-event-order-gap" in e for e in result["errors"]))


class TestLineageValidation(unittest.TestCase):
    def test_valid_retry_lineage(self):
        lineage = {
            "parent_run_id": "parent", "lineage_kind": "retry",
            "lineage_reason": "fix", "parent_status": "failed",
            "parent_boundary_event_id": "evt-5",
            "parent_boundary_event_type": "run.failed",
            "parent_boundary_event_order": 5,
        }
        r = validate_lineage(lineage)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_valid_resume_lineage(self):
        lineage = {
            "parent_run_id": "parent", "lineage_kind": "resume",
            "lineage_reason": "continue", "parent_status": "interrupted",
            "parent_boundary_event_id": "evt-5",
            "parent_boundary_event_type": "run.interrupted",
            "parent_boundary_event_order": 5,
        }
        r = validate_lineage(lineage)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_valid_redesign_lineage(self):
        lineage = {
            "parent_run_id": "parent", "lineage_kind": "redesign",
            "lineage_reason": "rework", "parent_status": "failed",
            "parent_boundary_event_id": "evt-5",
            "parent_boundary_event_type": "run.failed",
            "parent_boundary_event_order": 5,
        }
        r = validate_lineage(lineage)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_invalid_lineage_kind(self):
        lineage = {
            "parent_run_id": "p", "lineage_kind": "unknown",
            "lineage_reason": "x", "parent_status": "active",
            "parent_boundary_event_id": "evt-1",
            "parent_boundary_event_type": "run.started",
            "parent_boundary_event_order": 1,
        }
        r = validate_lineage(lineage)
        self.assertFalse(r["valid"])

    def test_lineage_missing_field(self):
        r = validate_lineage({"parent_run_id": "p"})
        self.assertFalse(r["valid"])


class TestStageGraphValidation(unittest.TestCase):
    def setUp(self):
        self.valid_graph = {
            "graph_id": "g1",
            "stages": [
                {"stage_id": "build", "name": "Build", "required": True, "status": "pending"},
                {"stage_id": "test", "name": "Test", "required": False, "status": "pending"},
            ],
            "edges": [{"from": "build", "to": "test", "condition": "on_pass"}],
            "entry_stages": ["build"],
            "terminal_stages": ["test"],
        }

    def test_valid_graph(self):
        r = validate_stage_graph(self.valid_graph)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_cyclic_graph_rejected(self):
        g = copy.deepcopy(self.valid_graph)
        g["edges"].append({"from": "test", "to": "build", "condition": "always"})
        r = validate_stage_graph(g)
        self.assertFalse(r["valid"])

    def test_duplicate_stage_id(self):
        g = copy.deepcopy(self.valid_graph)
        g["stages"].append({"stage_id": "build", "name": "Build2", "required": False, "status": "pending"})
        r = validate_stage_graph(g)
        self.assertFalse(r["valid"])

    def test_unresolved_edge_reference(self):
        g = copy.deepcopy(self.valid_graph)
        g["edges"].append({"from": "test", "to": "nonexistent", "condition": "always"})
        r = validate_stage_graph(g)
        self.assertFalse(r["valid"])

    def test_validator_gate_requires_contract_ref(self):
        g = copy.deepcopy(self.valid_graph)
        g["stages"][0]["gates"] = [{"gate_id": "g1", "gate_type": "validator", "required": True, "failure_behavior": "halt_run"}]
        r = validate_stage_graph(g)
        self.assertFalse(r["valid"])

    def test_validator_gate_with_contract_ref(self):
        g = copy.deepcopy(self.valid_graph)
        g["stages"][0]["gates"] = [{
            "gate_id": "g1", "gate_type": "validator", "required": True,
            "failure_behavior": "halt_run",
            "contract_ref": {"artifact_id": "c1", "artifact_kind": "contract", "locator": "references/runtime-state-contract.md"}
        }]
        r = validate_stage_graph(g)
        self.assertTrue(r["valid"], msg=r["errors"])

    def test_missing_top_level_field(self):
        r = validate_stage_graph({"graph_id": "g1", "stages": [], "edges": [], "entry_stages": [], "terminal_stages": []})
        self.assertFalse(r["valid"])


class TestSystem082Compatibility(unittest.TestCase):
    """Run the frozen prior test suite as external verification."""

    def test_frozen_harness_passes(self):
        result = subprocess.run(
            [sys.executable, str(FROZEN_TEST)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60
        )
        self.assertEqual(result.returncode, 0,
                        msg=f"prior tests failed:\nSTDOUT:\n{result.stdout[-2000:]}\nSTDERR:\n{result.stderr[-2000:]}")


class TestVisibilityMigrationDifferential(unittest.TestCase):
    """Differential and adversarial coverage for the v0.9.0 visibility surface."""

    def setUp(self):
        self.contract = ORACLE.load_fixture()
        self.run_id = "run-root"

    @staticmethod
    def _artifact(artifact_id, visibility="public"):
        artifact = ORACLE._public_runtime_artifact(artifact_id, run_id="run-root", stage_id="build")
        artifact["visibility"] = visibility
        resolution = artifact["visibility_resolution"]
        resolution["resolved_visibility"] = visibility
        resolution["contributors"][0]["asserted_visibility"] = visibility
        resolution["resolution_audit"].update({
            "restricted_count": int(visibility == "restricted"),
            "project_count": int(visibility == "project"),
            "public_count": int(visibility == "public"),
        })
        return artifact

    def _active_stage_states(self):
        genesis = ORACLE.genesis_payload(self.contract, event_run_id=self.run_id)
        oracle_state = ORACLE.reduce_event(self.contract, None, "run.created", genesis, self.run_id)
        core_state = apply_reducer(None, {"run_id": self.run_id, "event_type": "run.created", "payload": genesis})
        events = [
            ("run.started", {"started_at": "2026-07-16T00:00:01Z", "executor_identity": "runner-conformance"}),
            ("run.stage.started", {"stage_id": "build", "started_at": "2026-07-16T00:00:02Z", "entry_evidence": []}),
        ]
        for event_type, payload in events:
            oracle_state = ORACLE.reduce_event(self.contract, oracle_state, event_type, payload, self.run_id)
            core_state = apply_reducer(core_state, {"run_id": self.run_id, "event_type": event_type, "payload": payload})
        return oracle_state, core_state

    def _completed_states(self, artifacts):
        oracle_state, core_state = self._active_stage_states()
        gate = {
            "stage_id": "build", "gate_id": "gate-required", "decision_id": "decision-visibility",
            "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z",
            "evaluated_by": "validator-1",
            "evidence": [{"artifact_id": "report-visibility", "artifact_kind": "validation_report"}],
        }
        oracle_state = ORACLE.reduce_event(self.contract, oracle_state, "run.gate.evaluated", gate, self.run_id)
        core_state = apply_reducer(core_state, {"run_id": self.run_id, "event_type": "run.gate.evaluated", "payload": gate})
        completed = {
            "stage_id": "build", "completed_at": "2026-07-16T00:00:04Z",
            "gate_decisions": ["decision-visibility"], "artifacts_produced": artifacts,
        }
        oracle_state = ORACLE.reduce_event(self.contract, oracle_state, "run.stage.completed", completed, self.run_id)
        core_state = apply_reducer(core_state, {"run_id": self.run_id, "event_type": "run.stage.completed", "payload": completed})
        return oracle_state, core_state

    def test_valid_visibility_traces_match_frozen_oracle(self):
        cases = {
            "no-artifact": [],
            "public": [self._artifact("public", "public")],
            "project": [self._artifact("project", "project")],
            "restricted": [self._artifact("restricted", "restricted")],
            "aggregate": [self._artifact("agg-public", "public"), self._artifact("agg-project", "project"), self._artifact("agg-restricted", "restricted")],
        }
        for name, artifacts in cases.items():
            with self.subTest(name=name):
                oracle_state, core_state = self._completed_states(copy.deepcopy(artifacts))
                self.assertEqual(core_state, oracle_state)
                self.assertEqual(compute_projection_digest(core_state), ORACLE.digest(oracle_state))

    def test_failed_stage_partial_artifacts_match_frozen_oracle(self):
        oracle_state, core_state = self._active_stage_states()
        failed = {
            "stage_id": "build", "failed_at": "2026-07-16T00:00:04Z",
            "error": {"code": "E-VIS", "message": "partial failure"},
            "failure_category": "command_failed", "failure_is_transient": False,
            "failure_is_deterministic": True,
            "artifacts_produced_before_failure": [self._artifact("partial", "project")],
            "retry_eligible": False,
        }
        oracle_state = ORACLE.reduce_event(self.contract, oracle_state, "run.stage.failed", failed, self.run_id)
        core_state = apply_reducer(core_state, {"run_id": self.run_id, "event_type": "run.stage.failed", "payload": failed})
        self.assertEqual(core_state, oracle_state)
        self.assertEqual(core_state["resolved_run_visibility"], "project")
        self.assertEqual(compute_projection_digest(core_state), ORACLE.digest(oracle_state))

    def test_runtime_artifacts_are_preserved_and_refs_are_derived(self):
        artifact = self._artifact("preserved", "restricted")
        artifact["artifact_ref"]["artifact_version"] = "1.2.3"
        original = copy.deepcopy(artifact)
        _, state = self._completed_states([artifact])
        self.assertEqual(state["runtime_artifacts"], [original])
        self.assertEqual(state["artifact_refs"], [original["artifact_ref"]])

    def test_nested_visibility_field_removals_are_rejected(self):
        context = ORACLE.genesis_payload(self.contract)["visibility_context"]
        context_paths = [
            ("trigger_visibility", "authority"),
            ("policy_contributors", 0, "classification_evidence"),
            ("contract_contributors", 0, "contributor_ref"),
            ("run_visibility_resolution", "resolved_at"),
            ("run_visibility_resolution", "resolution_audit", "public_count"),
            ("resolved_run_visibility",),
        ]
        for path in context_paths:
            with self.subTest(context_path=path):
                payload = ORACLE.genesis_payload(self.contract)
                target = payload["visibility_context"]
                for part in path[:-1]:
                    target = target[part]
                del target[path[-1]]
                self.assertFalse(validate_payload("run.created", payload)["valid"])

        artifact_paths = [
            ("artifact_ref", "artifact_id"), ("origin_run",), ("origin_stage",),
            ("produced_by",), ("visibility",), ("visibility_resolution", "resolution_id"),
            ("visibility_resolution", "contributors", 0, "contributor_kind"),
            ("visibility_resolution", "contributors", 0, "classification_evidence"),
            ("visibility_resolution", "resolution_audit", "contributor_count"),
        ]
        for path in artifact_paths:
            with self.subTest(artifact_path=path):
                artifact = self._artifact("nested-removal")
                target = artifact
                for part in path[:-1]:
                    target = target[part]
                del target[path[-1]]
                with self.assertRaises(ValueError):
                    self._completed_states([artifact])

    def test_nested_extra_fields_and_conflicting_identities_are_rejected(self):
        mutations = []
        artifact = self._artifact("extra-contributor")
        artifact["visibility_resolution"]["contributors"][0]["extra"] = True
        mutations.append(artifact)
        artifact = self._artifact("extra-audit")
        artifact["visibility_resolution"]["resolution_audit"]["extra"] = 0
        mutations.append(artifact)
        artifact = self._artifact("duplicate-identity")
        duplicate = copy.deepcopy(artifact["visibility_resolution"]["contributors"][0])
        duplicate["contributor_id"] = "different-id-same-ref"
        artifact["visibility_resolution"]["contributors"].append(duplicate)
        artifact["visibility_resolution"]["resolution_audit"].update({"contributor_count": 2, "public_count": 2})
        mutations.append(artifact)
        for artifact in mutations:
            with self.subTest(artifact=artifact["artifact_ref"]["artifact_id"]), self.assertRaises(ValueError):
                self._completed_states([artifact])

    def test_oracle_failure_codes_align_for_adversarial_artifacts(self):
        mutations = []
        artifact = self._artifact("missing-origin-stage")
        del artifact["origin_stage"]
        mutations.append(artifact)
        artifact = self._artifact("wrong-run")
        artifact["origin_run"] = "other-run"
        mutations.append(artifact)
        artifact = self._artifact("typed-wrong-run")
        artifact["origin_run"] = 7
        mutations.append(artifact)
        artifact = self._artifact("wrong-stage")
        artifact["origin_stage"] = "other-stage"
        mutations.append(artifact)
        artifact = self._artifact("typed-wrong-stage")
        artifact["origin_stage"] = 7
        mutations.append(artifact)
        artifact = self._artifact("invalid-value")
        artifact["visibility"] = "internal"
        mutations.append(artifact)
        artifact = self._artifact("empty-contributors")
        artifact["visibility_resolution"]["contributors"] = []
        mutations.append(artifact)
        for field in ("contributors", "resolved_visibility", "resolution_audit"):
            artifact = self._artifact("missing-resolution-" + field)
            del artifact["visibility_resolution"][field]
            mutations.append(artifact)
        for field in ("contributor_id", "asserted_visibility", "classification_evidence"):
            artifact = self._artifact("missing-contributor-" + field)
            del artifact["visibility_resolution"]["contributors"][0][field]
            mutations.append(artifact)
        artifact = self._artifact("audit-mismatch")
        artifact["visibility_resolution"]["resolution_audit"]["public_count"] = 2
        mutations.append(artifact)
        artifact = self._artifact("assertion-audit-order")
        artifact["visibility_resolution"]["contributors"][0]["asserted_visibility"] = "restricted"
        mutations.append(artifact)
        artifact = self._artifact("resolved-mismatch")
        artifact["visibility_resolution"]["resolved_visibility"] = "project"
        mutations.append(artifact)
        artifact = self._artifact("extra-field")
        artifact["extra"] = True
        mutations.append(artifact)
        for artifact in mutations:
            oracle_state, core_state = self._active_stage_states()
            gate = {
                "stage_id": "build", "gate_id": "gate-required", "decision_id": "decision-code",
                "outcome": "pass", "execution_mode": "full", "evaluated_at": "2026-07-16T00:00:03Z",
                "evaluated_by": "validator-1", "evidence": [{"artifact_id": "report", "artifact_kind": "validation_report"}],
            }
            oracle_state = ORACLE.reduce_event(self.contract, oracle_state, "run.gate.evaluated", gate, self.run_id)
            core_state = apply_reducer(core_state, {"run_id": self.run_id, "event_type": "run.gate.evaluated", "payload": gate})
            payload = {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": ["decision-code"], "artifacts_produced": [artifact]}
            with self.subTest(artifact=artifact["artifact_ref"]["artifact_id"]):
                with self.assertRaises(ORACLE.ConformanceError) as oracle_error:
                    ORACLE.reduce_event(self.contract, oracle_state, "run.stage.completed", payload, self.run_id)
                with self.assertRaises(ValueError) as core_error:
                    apply_reducer(core_state, {"run_id": self.run_id, "event_type": "run.stage.completed", "payload": payload})
                self.assertIn(str(oracle_error.exception), str(core_error.exception))

    def test_legacy_visibility_inputs_are_rejected_without_defaults(self):
        legacy_genesis = _make_run_created_payload()
        del legacy_genesis["visibility_context"]
        result = validate_payload("run.created", legacy_genesis)
        self.assertFalse(result["valid"])
        self.assertEqual(result["rule_id"], "visibility-context-missing")
        _, active = self._active_stage_states()
        legacy_artifact = {"artifact_id": "legacy", "artifact_kind": "stage_output"}
        payload = {"stage_id": "build", "completed_at": "2026-07-16T00:00:04Z", "gate_decisions": [], "artifacts_produced": [legacy_artifact]}
        with self.assertRaises(ValueError) as error:
            apply_reducer(active, {"run_id": self.run_id, "event_type": "run.stage.completed", "payload": payload})
        self.assertNotIn("public", str(error.exception))


class TestBindStoreEventMetadata(unittest.TestCase):
    """The shared metadata-binding helper used by both the journal
    write-path and the projection replay-path.

    These tests prove determinism, caller-input preservation, exactness for
    each event class, and strict rejection of incomplete store-assigned
    event identity (the contract forbids silent defaults).
    """

    def _base_projection(self):
        return {
            "run_id": "run-1",
            "status": "pending",
            "event_count": 1,
            "latest_event_id": None,
            "latest_event_type": None,
            "latest_event_order": None,
        }

    def _event(self, event_id="evt-A", event_type="run.started", event_order=2, payload=None):
        return {
            "event_id": event_id,
            "event_type": event_type,
            "event_order": event_order,
            "payload": payload or {},
        }

    def test_deterministic_output_for_same_inputs(self):
        projection = self._base_projection()
        event = self._event()
        first = bind_store_event_metadata(projection, event)
        second = bind_store_event_metadata(copy.deepcopy(projection), copy.deepcopy(event))
        self.assertEqual(first, second)
        self.assertEqual(
            canonical_serialize(first), canonical_serialize(second),
            "helper must be byte-deterministic under RFC 8785 canonicalization",
        )

    def test_does_not_mutate_caller_projection(self):
        projection = self._base_projection()
        original = copy.deepcopy(projection)
        event = self._event()
        bound = bind_store_event_metadata(projection, event)
        self.assertEqual(projection, original, "caller projection must not be mutated")
        self.assertNotEqual(bound, original, "returned projection must carry bound metadata")

    def test_does_not_mutate_event(self):
        projection = self._base_projection()
        event = self._event()
        event_original = copy.deepcopy(event)
        bind_store_event_metadata(projection, event)
        self.assertEqual(event, event_original, "event must not be mutated")

    def test_ordinary_event_binds_identity_and_clears_terminal_status(self):
        projection = self._base_projection()
        projection["latest_terminal_status"] = "failed"
        event = self._event(event_id="evt-9", event_type="run.started", event_order=4)
        bound = bind_store_event_metadata(projection, event)
        self.assertEqual(bound["latest_event_id"], "evt-9")
        self.assertEqual(bound["latest_event_type"], "run.started")
        self.assertEqual(bound["latest_event_order"], 4)
        self.assertNotIn("latest_terminal_status", bound)

    def test_terminated_event_binds_terminal_status(self):
        projection = self._base_projection()
        projection["status"] = "interrupted"
        event = self._event(
            event_id="evt-term", event_type="run.terminated", event_order=7,
            payload={"terminal_status": "blocked"},
        )
        bound = bind_store_event_metadata(projection, event)
        self.assertEqual(bound["latest_event_id"], "evt-term")
        self.assertEqual(bound["latest_event_type"], "run.terminated")
        self.assertEqual(bound["latest_event_order"], 7)
        self.assertEqual(bound["latest_terminal_status"], "blocked")

    def test_first_non_terminated_after_termination_clears_terminal_status(self):
        # A run can be terminated then a corrective event (e.g. intervention)
        # recorded. The terminal flag must not leak past the boundary.
        projection = self._base_projection()
        projection["latest_terminal_status"] = "failed"
        event = self._event(event_id="evt-fix", event_type="run.intervention", event_order=9)
        bound = bind_store_event_metadata(projection, event)
        self.assertNotIn("latest_terminal_status", bound)
        self.assertEqual(bound["latest_event_type"], "run.intervention")

    def test_empty_event_id_rejected(self):
        projection = self._base_projection()
        with self.assertRaises(ValueError):
            bind_store_event_metadata(projection, self._event(event_id=""))

    def test_empty_event_type_rejected(self):
        projection = self._base_projection()
        with self.assertRaises(ValueError):
            bind_store_event_metadata(projection, self._event(event_type=""))

    def test_non_string_event_id_rejected(self):
        projection = self._base_projection()
        with self.assertRaises(ValueError):
            bind_store_event_metadata(projection, {"event_id": 123, "event_type": "run.started", "event_order": 1})

    def test_boolean_event_order_rejected(self):
        projection = self._base_projection()
        with self.assertRaises(ValueError):
            bind_store_event_metadata(projection, self._event(event_order=True))

    def test_zero_event_order_rejected(self):
        projection = self._base_projection()
        with self.assertRaises(ValueError):
            bind_store_event_metadata(projection, self._event(event_order=0))

    def test_negative_event_order_rejected(self):
        projection = self._base_projection()
        with self.assertRaises(ValueError):
            bind_store_event_metadata(projection, self._event(event_order=-3))

    def test_non_int_event_order_rejected(self):
        projection = self._base_projection()
        with self.assertRaises(ValueError):
            bind_store_event_metadata(projection, self._event(event_order="2"))

    def test_terminated_without_terminal_status_rejected(self):
        projection = self._base_projection()
        event = self._event(event_type="run.terminated", event_order=5, payload={})
        with self.assertRaises(ValueError):
            bind_store_event_metadata(projection, event)

    def test_genesis_event_binds_identity(self):
        projection = initial_projection({"run_id": "run-gen", "event_type": "run.created", "payload": _make_run_created_payload()})
        event = self._event(event_id="evt-gen", event_type="run.created", event_order=1)
        bound = bind_store_event_metadata(projection, event)
        self.assertEqual(bound["latest_event_id"], "evt-gen")
        self.assertEqual(bound["latest_event_order"], 1)
        self.assertNotIn("latest_terminal_status", bound)


if __name__ == "__main__":
    unittest.main(verbosity=2)
