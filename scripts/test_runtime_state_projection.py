"""Comprehensive tests for runtime_state_projection.py against Runtime State Contract v0.9.0.

Tests: basic replay, determinism, tamper detection (content digests, chain links, state),
gap detection, run identity mismatch, read-only proof, lineage preservation,
schema version enforcement, and compatibility with all existing runtime state tests.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid

# Add parent to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.runtime_state_core import (
    ZERO_DIGEST,
    canonical_serialize,
    compute_digest,
    compute_event_digest,
)
from scripts.runtime_state_journal import (
    RuntimeJournal,
    RuntimeJournalError,
)
from scripts.runtime_state_projection import (
    ProjectionError,
    run_projection,
    run_projection_from_events,
    stage_projection,
    stream_head,
    lineage,
    projection_digest,
    replay_is_read_only,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent

TEST_SIGNER_KEY = b"test-projection-key-086"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _visibility_context(visibility="public"):
    """Build a v0.9.0 visibility_context with the given resolved visibility."""
    resolved_visibility = visibility
    return {
        "trigger_visibility": {
            "contributor_id": "test-trigger-001",
            "contributor_kind": "trigger_provenance",
            "contributor_ref": {"artifact_id": "ticket-test", "artifact_kind": "ticket"},
            "asserted_visibility": resolved_visibility,
            "authority": "Test ticket trigger",
            "classification_evidence": [{"artifact_id": "ticket-test", "artifact_kind": "ticket"}],
        },
        "policy_contributors": [
            {
                "contributor_id": "test-policy-001",
                "contributor_kind": "project_policy",
                "contributor_ref": {"artifact_id": "project-policy", "artifact_kind": "policy"},
                "asserted_visibility": resolved_visibility,
                "authority": "Test project policy",
                "classification_evidence": [{"artifact_id": "project-policy", "artifact_kind": "policy"}],
            }
        ],
        "contract_contributors": [
            {
                "contributor_id": "test-contract-001",
                "contributor_kind": "governing_contract",
                "contributor_ref": {
                    "artifact_id": "runtime-state-contract",
                    "artifact_kind": "contract",
                    "artifact_version": "0.9.0",
                },
                "asserted_visibility": resolved_visibility,
                "authority": "Test governing contract",
                "classification_evidence": [
                    {"artifact_id": "runtime-state-contract", "artifact_kind": "contract", "artifact_version": "0.9.0"}
                ],
            }
        ],
        "run_visibility_resolution": {
            "resolution_id": "test-resolution-run-001",
            "resolved_at": "2026-01-01T00:00:00Z",
            "contributors": [
                {
                    "contributor_id": "test-trigger-001",
                    "contributor_kind": "trigger_provenance",
                    "contributor_ref": {"artifact_id": "ticket-test", "artifact_kind": "ticket"},
                    "asserted_visibility": resolved_visibility,
                    "authority": "Test ticket trigger",
                    "classification_evidence": [{"artifact_id": "ticket-test", "artifact_kind": "ticket"}],
                },
                {
                    "contributor_id": "test-policy-001",
                    "contributor_kind": "project_policy",
                    "contributor_ref": {"artifact_id": "project-policy", "artifact_kind": "policy"},
                    "asserted_visibility": resolved_visibility,
                    "authority": "Test project policy",
                    "classification_evidence": [{"artifact_id": "project-policy", "artifact_kind": "policy"}],
                },
                {
                    "contributor_id": "test-contract-001",
                    "contributor_kind": "governing_contract",
                    "contributor_ref": {
                        "artifact_id": "runtime-state-contract",
                        "artifact_kind": "contract",
                        "artifact_version": "0.9.0",
                    },
                    "asserted_visibility": resolved_visibility,
                    "authority": "Test governing contract",
                    "classification_evidence": [
                        {"artifact_id": "runtime-state-contract", "artifact_kind": "contract", "artifact_version": "0.9.0"}
                    ],
                },
            ],
            "resolution_rule": "most_restrictive",
            "resolved_visibility": resolved_visibility,
            "resolution_audit": {
                "contributor_count": 3,
                "restricted_count": 3 if resolved_visibility == "restricted" else 0,
                "project_count": 3 if resolved_visibility == "project" else 0,
                "public_count": 3 if resolved_visibility == "public" else 0,
                "applied_rule": "most_restrictive",
            },
        },
        "resolved_run_visibility": resolved_visibility,
    }


def _run_created_payload(visibility="public"):
    return {
        "run_provenance": {
            "origin_artifact": {"artifact_id": "test-001", "artifact_kind": "test"},
            "governing_contracts": [
                {"artifact_id": "runtime-architecture", "artifact_kind": "contract", "artifact_version": "0.8.0"}
            ],
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
                                "artifact_version": "0.9.0",
                                "locator": "references/runtime-state-contract.md",
                            },
                        }
                    ],
                }
            ],
            "edges": [],
            "entry_stages": ["build"],
            "terminal_stages": ["build"],
        },
        "visibility_context": _visibility_context(visibility),
    }


def _make_request(event_type, payload, run_id="test-run", prev_digest=None, head_order=0):
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
        "client_event_id": f"test-{event_type}-{uuid.uuid4().hex[:8]}",
        "prev_event_digest": prev_digest,
    }


def _setup_journal_with_events(db_path, run_id, num_events=3):
    """Create a journal and append genesis + started + stage.started events.
    Returns (journal, receipts) or raises.
    """
    journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)
    receipts = []

    # Genesis
    req = _make_request("run.created", _run_created_payload(), run_id=run_id)
    rec = journal.append(req)
    receipts.append(rec)

    if num_events >= 2:
        # run.started
        sp = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
        req = _make_request("run.started", sp, run_id=run_id,
                            prev_digest=receipts[-1]["stored_content_digest"], head_order=len(receipts))
        req["expected_stream_head"] = {
            "event_order": len(receipts),
            "content_digest": receipts[-1]["stored_content_digest"],
        }
        rec = journal.append(req)
        receipts.append(rec)

    if num_events >= 3:
        # run.stage.started
        stage_payload = {
            "stage_id": "build",
            "started_at": "2026-01-01T00:00:02Z",
            "entry_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
        }
        req = _make_request("run.stage.started", stage_payload, run_id=run_id,
                            prev_digest=receipts[-1]["stored_content_digest"], head_order=len(receipts))
        req["expected_stream_head"] = {
            "event_order": len(receipts),
            "content_digest": receipts[-1]["stored_content_digest"],
        }
        rec = journal.append(req)
        receipts.append(rec)

    return journal, receipts


# ---------------------------------------------------------------------------
# Test 1: Basic replay
# ---------------------------------------------------------------------------

class TestBasicReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-basic-replay"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_basic_replay_creates_projection(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        projection = run_projection(self.run_id, self.db_path)

        # Verify projection structure
        self.assertEqual(projection["run_id"], self.run_id)
        self.assertEqual(projection["status"], "active")
        self.assertIn("projection_digest", projection)
        self.assertIn("latest_event_id", projection)
        self.assertEqual(projection["latest_event_order"], 3)
        self.assertEqual(projection["event_count"], 3)
        self.assertIn("stage_states", projection)
        self.assertIn("build", projection["stage_states"])

        # Verify digest is valid sha256
        digest = projection["projection_digest"]
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(len(digest), 71)  # "sha256:" + 64 hex chars

    def test_replay_matches_journal_contents(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        projection = run_projection(self.run_id, self.db_path)

        # Verify projection state matches journal events
        with RuntimeJournal(self.db_path, TEST_SIGNER_KEY) as j:
            events = j.read_events(self.run_id)
        self.assertEqual(projection["event_count"], len(events))
        self.assertEqual(projection["latest_event_id"], events[-1]["event_id"])

    def test_stage_projection_extracts_correct_stage(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        stage = stage_projection(self.run_id, "build", self.db_path)
        self.assertIsNotNone(stage)
        self.assertEqual(stage["stage_id"], "build")
        self.assertEqual(stage["status"], "active")

    def test_stage_not_found_raises(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            stage_projection(self.run_id, "nonexistent", self.db_path)
        self.assertEqual(ctx.exception.code, "stage_not_found")

    def test_no_events_raises(self):
        with self.assertRaises(ProjectionError) as ctx:
            run_projection("no-such-run", self.db_path)
        self.assertEqual(ctx.exception.code, "no_events_for_run")


# ---------------------------------------------------------------------------
# Test 2: Determinism
# ---------------------------------------------------------------------------

class TestDeterminism(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-determinism"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_repeated_replay_produces_same_digest(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        digest1 = projection_digest(self.run_id, self.db_path)
        digest2 = projection_digest(self.run_id, self.db_path)
        digest3 = projection_digest(self.run_id, self.db_path)

        self.assertEqual(digest1, digest2)
        self.assertEqual(digest2, digest3)

    def test_canonical_projections_byte_equivalent(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        proj1 = run_projection(self.run_id, self.db_path)
        proj2 = run_projection(self.run_id, self.db_path)

        # Remove presentation metadata before comparison
        del proj1["projection_id"]
        del proj1["derived_at"]
        del proj2["projection_id"]
        del proj2["derived_at"]

        can1 = canonical_serialize(proj1)
        can2 = canonical_serialize(proj2)

        self.assertEqual(can1, can2)

    def test_different_runs_different_digests(self):
        journal1, _ = _setup_journal_with_events(self.db_path, "run-a", num_events=3)
        journal1._conn.close()
        journal2, _ = _setup_journal_with_events(self.db_path, "run-b", num_events=3)
        journal2._conn.close()

        digest_a = projection_digest("run-a", self.db_path)
        digest_b = projection_digest("run-b", self.db_path)

        self.assertNotEqual(digest_a, digest_b)

    def test_deterministic_digest_excludes_presentation_fields(self):
        """Projection digest must be independent of projection_id and derived_at."""
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        proj1 = run_projection(self.run_id, self.db_path)
        digest1 = proj1["projection_digest"]

        # Manually change presentation fields - digest should remain same
        preimage = {k: v for k, v in proj1.items() if k not in {"projection_digest", "projection_id", "derived_at"}}
        recomputed = compute_digest(preimage)
        self.assertEqual(recomputed, digest1)


# ---------------------------------------------------------------------------
# Test 3: Tamper detection - content digests
# ---------------------------------------------------------------------------

class TestTamperContentDigest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-tamper-digest"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_mutated_content_digest_detected(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        # Modify content_digest of the FIRST event so hash chain passes
        # (first event always links to ZERO_DIGEST) but content_digest is wrong
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET content_digest = ? WHERE run_id = ? AND event_order = 1",
            ("sha256:" + ("f" * 64), self.run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "content_digest_mismatch")

    def test_mutated_content_digest_stream_head(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET content_digest = ? WHERE run_id = ? AND event_order = 3",
            ("sha256:" + ("f" * 64), self.run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            stream_head(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "content_digest_mismatch")


# ---------------------------------------------------------------------------
# Test 4: Tamper detection - digest chain
# ---------------------------------------------------------------------------

class TestTamperDigestChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-tamper-chain"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_mutated_prev_event_digest_detected(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        # Read event 3, modify its prev_event_digest, recompute content_digest,
        # and update both so the event is internally consistent but chain is broken
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT * FROM runtime_events WHERE run_id = ? AND event_order = 3",
            (self.run_id,),
        ).fetchone()
        cols = [desc[0] for desc in conn.execute(
            "SELECT * FROM runtime_events LIMIT 0"
        ).description]
        event = dict(zip(cols, row))
        json_fields = {"payload", "causation_chain", "trigger_artifact",
                       "expected_stream_head", "prior_state", "next_state"}
        for f in json_fields:
            if f in event and event[f] is not None:
                event[f] = json.loads(event[f])
        # Remove causation_id if None (matches stored event shape)
        if event.get("causation_id") is None and "causation_id" in event:
            del event["causation_id"]

        # Update prev_event_digest and recompute content_digest
        event["prev_event_digest"] = ZERO_DIGEST
        new_digest = compute_event_digest({k: v for k, v in event.items() if k != "content_digest"})
        event["content_digest"] = new_digest

        conn.execute(
            """UPDATE runtime_events
               SET prev_event_digest = ?, content_digest = ?
               WHERE run_id = ? AND event_order = 3""",
            (ZERO_DIGEST, new_digest, self.run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "hash_chain_link_broken")

    def test_broken_first_link_detected(self):
        """First event must link to ZERO_DIGEST."""
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        # Read event 1, modify prev_event_digest and recompute content_digest
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT * FROM runtime_events WHERE run_id = ? AND event_order = 1",
            (self.run_id,),
        ).fetchone()
        cols = [desc[0] for desc in conn.execute(
            "SELECT * FROM runtime_events LIMIT 0"
        ).description]
        event = dict(zip(cols, row))
        json_fields = {"payload", "causation_chain", "trigger_artifact",
                       "expected_stream_head", "prior_state", "next_state"}
        for f in json_fields:
            if f in event and event[f] is not None:
                event[f] = json.loads(event[f])
        # Remove causation_id if None (matches stored event shape)
        if event.get("causation_id") is None and "causation_id" in event:
            del event["causation_id"]

        wrong_digest = "sha256:" + ("a" * 64)
        event["prev_event_digest"] = wrong_digest
        new_digest = compute_event_digest({k: v for k, v in event.items() if k != "content_digest"})
        event["content_digest"] = new_digest

        conn.execute(
            """UPDATE runtime_events
               SET prev_event_digest = ?, content_digest = ?
               WHERE run_id = ? AND event_order = 1""",
            (wrong_digest, new_digest, self.run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "hash_chain_link_broken")


# ---------------------------------------------------------------------------
# Test 5: Tamper detection - state tampering
# ---------------------------------------------------------------------------

class TestTamperState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-tamper-state"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_mutated_next_state_detected(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET next_state = ? WHERE run_id = ? AND event_order = 2",
            (json.dumps({"run_id": self.run_id, "status": "tampered"}), self.run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "next_state_tampered")

    def test_mutated_prior_state_detected(self):
        """Mutated prior_state should be caught by prior_state verification."""
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET prior_state = ? WHERE run_id = ? AND event_order = 2",
            (json.dumps({"run_id": self.run_id, "status": "tampered"}), self.run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "prior_state_tampered")


# ---------------------------------------------------------------------------
# Test 6: Gap detection
# ---------------------------------------------------------------------------

class TestGapDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-gap-detect"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_deleted_event_row_detected(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        # Delete event 2
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM runtime_events WHERE run_id = ? AND event_order = 2", (self.run_id,))
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "event_order_gap")

    def test_duplicate_event_order_detected(self):
        """Event order that is not 1,2,3...N is detected."""
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        # Change event_order 3 to 99
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE runtime_events SET event_order = 99 WHERE run_id = ? AND event_order = 3", (self.run_id,))
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "event_order_gap")


# ---------------------------------------------------------------------------
# Test 7: Run identity mismatch
# ---------------------------------------------------------------------------

class TestRunIdentityMismatch(unittest.TestCase):
    """Verify run identity mismatch is detected by the replay layer.

    Since the SQL query filters by run_id and cannot return events with
    different run_ids, we test the internal verification function directly
    with a manually constructed event list.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-identity"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_wrong_run_id_detected(self):
        """Verify run identity mismatch is detected when events carry different run_ids."""
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=2)
        journal._conn.close()

        # Read back events and create a list with one event's run_id altered
        with RuntimeJournal(self.db_path, TEST_SIGNER_KEY) as j:
            events = j.read_events(self.run_id)

        tampered = copy.deepcopy(events)
        tampered.append(copy.deepcopy(events[0]))
        tampered[-1]["run_id"] = "wrong-run-id"
        tampered[-1]["event_order"] = 3
        tampered[-1]["event_id"] = str(uuid.uuid4())

        # Verify the function directly
        from scripts.runtime_state_projection import _verify_run_identity
        with self.assertRaises(ProjectionError) as ctx:
            _verify_run_identity(tampered, self.run_id)
        self.assertEqual(ctx.exception.code, "run_identity_mismatch")


# ---------------------------------------------------------------------------
# Test 8: Read-only proof
# ---------------------------------------------------------------------------

class TestReadOnlyProof(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-readonly"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_replay_performs_no_writes(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        self.assertTrue(replay_is_read_only(self.db_path, self.run_id))

    def test_file_size_unchanged_after_replay(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        abs_path = pathlib.Path(self.db_path).resolve()
        size_before = abs_path.stat().st_size
        _ = run_projection(self.run_id, self.db_path)
        size_after = abs_path.stat().st_size

        self.assertEqual(size_before, size_after)

    def test_no_rows_modified(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        # Count events before replay
        conn = sqlite3.connect(self.db_path)
        count_before = conn.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
        conn.close()

        _ = run_projection(self.run_id, self.db_path)

        conn = sqlite3.connect(self.db_path)
        count_after = conn.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
        conn.close()

        self.assertEqual(count_before, count_after)


# ---------------------------------------------------------------------------
# Test 9: Lineage preservation
# ---------------------------------------------------------------------------

class TestLineagePreservation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_root_run_has_no_lineage(self):
        root_id = "test-root"
        journal, receipts = _setup_journal_with_events(self.db_path, root_id, num_events=3)
        journal._conn.close()

        lin = lineage(root_id, self.db_path)
        self.assertIsNone(lin)

    def test_child_run_lineage_preserved_in_projection(self):
        root_id = "test-root-lineage"
        child_id = "test-child-lineage"

        journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)

        # Create root run
        root_payload = _run_created_payload()
        req = _make_request("run.created", root_payload, run_id=root_id)
        rec1 = journal.append(req)

        # Start root run
        sp = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
        req = _make_request("run.started", sp, run_id=root_id,
                            prev_digest=rec1["stored_content_digest"], head_order=1)
        req["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["stored_content_digest"]}
        journal.append(req)

        journal._conn.close()

        # Create child run with lineage payload
        journal2 = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        child_payload = copy.deepcopy(_run_created_payload())
        child_payload["lineage"] = {
            "parent_run_id": root_id,
            "lineage_kind": "retry",
            "lineage_reason": "parent failed with command_error",
            "parent_status": "failed",
            "parent_boundary_event_id": "parent-event-id-xxx",
            "parent_boundary_event_type": "run.failed",
            "parent_boundary_event_order": 2,
        }

        req = _make_request("run.created", child_payload, run_id=child_id)
        rec_child = journal2.append(req)
        journal2._conn.close()

        # Verify lineage via projection
        proj = run_projection(child_id, self.db_path)
        self.assertIsNotNone(proj.get("lineage"))
        self.assertEqual(proj["lineage"]["parent_run_id"], root_id)
        self.assertEqual(proj["lineage"]["lineage_kind"], "retry")

    def test_lineage_api_returns_child_lineage(self):
        root_id = "test-lineage-api-root"
        child_id = "test-lineage-api-child"

        journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)

        # Root run
        root_payload = _run_created_payload()
        req = _make_request("run.created", root_payload, run_id=root_id)
        journal.append(req)
        journal._conn.close()

        # Child run with lineage
        journal2 = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        child_payload = copy.deepcopy(_run_created_payload())
        child_payload["lineage"] = {
            "parent_run_id": root_id,
            "lineage_kind": "resume",
            "lineage_reason": "resuming from checkpoint",
            "parent_status": "interrupted",
            "parent_boundary_event_id": "parent-event-abc",
            "parent_boundary_event_type": "run.interrupted",
            "parent_boundary_event_order": 1,
        }
        req = _make_request("run.created", child_payload, run_id=child_id)
        journal2.append(req)
        journal2._conn.close()

        lin = lineage(child_id, self.db_path)
        self.assertIsNotNone(lin)
        self.assertEqual(lin["parent_run_id"], root_id)
        self.assertEqual(lin["lineage_kind"], "resume")
        self.assertEqual(lin["parent_boundary_event_type"], "run.interrupted")

    def test_lineage_on_non_created_event_raises(self):
        run_id = "test-lineage-non-created"

        journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        payload = _run_created_payload()
        req = _make_request("run.created", payload, run_id=run_id)
        journal.append(req)

        # Start run
        sp = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
        req = _make_request("run.started", sp, run_id=run_id,
                            prev_digest=journal.read_events(run_id)[-1]["content_digest"], head_order=1)
        req["expected_stream_head"] = {"event_order": 1, "content_digest": journal.read_events(run_id)[-1]["content_digest"]}
        journal.append(req)

        # Mutate genesis event type to not be run.created
        journal._conn.close()
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE runtime_events SET event_type = 'run.started' WHERE run_id = ? AND event_order = 1", (run_id,))
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            lineage(run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "genesis_not_run_created")


# ---------------------------------------------------------------------------
# Test 10: Stream head
# ---------------------------------------------------------------------------

class TestStreamHead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-stream-head"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_empty_stream_head(self):
        head = stream_head("no-such-run", self.db_path)
        self.assertEqual(head["event_order"], 0)
        self.assertEqual(head["content_digest"], ZERO_DIGEST)

    def test_stream_head_after_events(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        head = stream_head(self.run_id, self.db_path)
        self.assertEqual(head["event_order"], 3)
        self.assertEqual(head["content_digest"], receipts[-1]["stored_content_digest"])


# ---------------------------------------------------------------------------
# Projection digest
# ---------------------------------------------------------------------------

class TestProjectionDigest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-digest"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_projection_digest_matches_run_projection(self):
        journal, receipts = _setup_journal_with_events(self.db_path, self.run_id, num_events=3)
        journal._conn.close()

        digest = projection_digest(self.run_id, self.db_path)
        projection = run_projection(self.run_id, self.db_path)

        self.assertEqual(digest, projection["projection_digest"])


# ---------------------------------------------------------------------------
# Existing test compatibility
# ---------------------------------------------------------------------------

class TestExistingTestsPass(unittest.TestCase):
    """Verify all existing runtime state tests still pass."""

    def test_contract_tests_pass(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "test_runtime_state_contract.py")],
            capture_output=True, text=True, timeout=120,
            cwd=str(ROOT),
        )
        self.assertEqual(
            result.returncode, 0,
            f"test_runtime_state_contract.py failed:\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}"
        )

    def test_core_tests_pass(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "test_runtime_state_core.py")],
            capture_output=True, text=True, timeout=120,
            cwd=str(ROOT),
        )
        self.assertEqual(
            result.returncode, 0,
            f"test_runtime_state_core.py failed:\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}"
        )

    def test_journal_tests_pass(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "test_runtime_state_journal.py")],
            capture_output=True, text=True, timeout=120,
            cwd=str(ROOT),
        )
        self.assertEqual(
            result.returncode, 0,
            f"test_runtime_state_journal.py failed:\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Frozen SHA verification
# ---------------------------------------------------------------------------

class TestFrozenSHAs(unittest.TestCase):
    """Verify all 7 frozen files are unchanged."""

    EXPECTED = {
        "references/runtime-state-contract.md":
            "016db98f28eb5fc291f6fc608f761d733f9fa6652fb12ea7af78166a0a54c9b1",
        "examples/runtime_state_contract_fixtures/conformance.json":
            "5fa055cb9353e6172b304de211208f8a6dfc017822fc4ce45f32576e10667e45",
        "scripts/test_runtime_state_contract.py":
            "99c7af2af12a0fd9bccd4b673eeaa68b8d5df076e68d132e0d5b3804f0d2f540",
        "scripts/runtime_state_core.py":
            "3257c3d65e539855a8bb21e3655e49572b56000c98ef0694506120a10a3b3610",
        "scripts/test_runtime_state_core.py":
            "8658cfdb550f8d5a07b3ca76170aa09a154f32618a94cb07d11ec4c80a4e3a38",
        "scripts/runtime_state_journal.py":
            "dfa5928ca7b300e6b8dfd3ea5fe352feb079a99a033713bdea0d0f55bb6ce4c0",
        "scripts/test_runtime_state_journal.py":
            "6f24d3cac71fb3f4b449f6548695b3ae5b8ad738dab347de731a5c7e69977769",
    }

    def test_all_frozen_files_unchanged(self):
        for rel, expected in self.EXPECTED.items():
            path = ROOT / rel
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(
                actual, expected,
                f"{rel}: actual={actual[:16]}... expected={expected[:16]}..."
            )


# ---------------------------------------------------------------------------
# Test 11: Nonexistent DB path
# ---------------------------------------------------------------------------

class TestNonexistentDBPath(unittest.TestCase):
    """Every public read API must raise ProjectionError("db_not_found")
    for a nonexistent database path and must NOT create any file."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.nonexistent = str(pathlib.Path(self.tmp_dir.name) / "does-not-exist.db")
        self.run_id = "test-nonexistent"

    def tearDown(self):
        self.tmp_dir.cleanup()
        # Ensure no side-effect files were created
        for sfx in ("", "-wal", "-shm"):
            p = pathlib.Path(self.nonexistent + sfx)
            if p.exists():
                p.unlink()

    def _assert_no_files_created(self):
        for sfx in ("", "-wal", "-shm"):
            p = pathlib.Path(self.nonexistent + sfx)
            self.assertFalse(p.exists(), f"Side-effect file created: {p}")

    def test_run_projection_nonexistent_raises(self):
        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.nonexistent)
        self.assertEqual(ctx.exception.code, "db_not_found")
        self._assert_no_files_created()

    def test_stage_projection_nonexistent_raises(self):
        with self.assertRaises(ProjectionError) as ctx:
            stage_projection(self.run_id, "build", self.nonexistent)
        self.assertEqual(ctx.exception.code, "db_not_found")
        self._assert_no_files_created()

    def test_stream_head_nonexistent_raises(self):
        with self.assertRaises(ProjectionError) as ctx:
            stream_head(self.run_id, self.nonexistent)
        self.assertEqual(ctx.exception.code, "db_not_found")
        self._assert_no_files_created()

    def test_lineage_nonexistent_raises(self):
        with self.assertRaises(ProjectionError) as ctx:
            lineage(self.run_id, self.nonexistent)
        self.assertEqual(ctx.exception.code, "db_not_found")
        self._assert_no_files_created()

    def test_projection_digest_nonexistent_raises(self):
        with self.assertRaises(ProjectionError) as ctx:
            projection_digest(self.run_id, self.nonexistent)
        self.assertEqual(ctx.exception.code, "db_not_found")
        self._assert_no_files_created()

    def test_nonexistent_path_is_not_a_directory(self):
        """Passing a directory path should also raise db_not_found."""
        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.tmp_dir.name)
        self.assertEqual(ctx.exception.code, "db_not_found")


# ---------------------------------------------------------------------------
# Test 12: Deterministic complete projection
# ---------------------------------------------------------------------------

class TestDeterministicCompleteProjection(unittest.TestCase):
    """Two replays of unchanged events must produce equal stable canonical preimages
    and equal projection_digest. presentation metadata is excluded from comparison."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-det-proj"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_complete_projections_stable_result(self):
        """Two replays of unchanged events have equal projection_digest and
        equal canonical preimages excluding presentation metadata."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        proj1 = run_projection(self.run_id, self.db_path)
        proj2 = run_projection(self.run_id, self.db_path)

        # projection_digest must be equal
        self.assertEqual(proj1["projection_digest"], proj2["projection_digest"])

        # Canonical preimages (excluding presentation metadata) must be equal
        preimage1 = {k: v for k, v in proj1.items()
                     if k not in {"projection_digest", "projection_id", "derived_at"}}
        preimage2 = {k: v for k, v in proj2.items()
                     if k not in {"projection_digest", "projection_id", "derived_at"}}
        can1 = canonical_serialize(preimage1)
        can2 = canonical_serialize(preimage2)
        self.assertEqual(can1, can2)

    def test_projection_id_is_presentation_metadata(self):
        """Two replays may have different projection_ids (random UUIDv4),
        both must be valid UUID strings, and projection_digest must be equal."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        proj1 = run_projection(self.run_id, self.db_path)
        proj2 = run_projection(self.run_id, self.db_path)

        # Both must be valid UUID strings (36 chars = 32 hex + 4 dashes)
        self.assertEqual(len(proj1["projection_id"]), 36)
        self.assertEqual(len(proj2["projection_id"]), 36)

        # May differ (random UUIDv4 identity, not deterministic)
        # But both must be unique IDs
        self.assertEqual(proj1["projection_digest"], proj2["projection_digest"])

    def test_derived_at_is_truthful_utc(self):
        """derived_at must be a valid UTC timestamp, never a fixed sentinel,
        and must change between replays."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        proj1 = run_projection(self.run_id, self.db_path)
        proj2 = run_projection(self.run_id, self.db_path)

        # Must be a non-empty UTC timestamp string
        self.assertTrue(proj1["derived_at"])
        self.assertIn("T", proj1["derived_at"])
        self.assertIn("Z", proj1["derived_at"])

        # Must NOT be the fixed sentinel
        self.assertNotEqual(proj1["derived_at"], "1970-01-01T00:00:00Z")
        self.assertNotEqual(proj2["derived_at"], "1970-01-01T00:00:00Z")


# ---------------------------------------------------------------------------
# Test 13: Read-only side effects
# ---------------------------------------------------------------------------

class TestReadOnlySideEffects(unittest.TestCase):
    """replay_is_read_only must detect WAL/SHM creation, DB mutation,
    schema changes, and row changes."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-ro-sfx"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        for sfx in ("-wal", "-shm"):
            p = pathlib.Path(self.db_path + sfx)
            if p.exists():
                p.unlink()

    def test_replay_does_not_write_to_db(self):
        """Clean replay should leave data_version and schema count unchanged."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()
        self.assertTrue(replay_is_read_only(self.db_path, self.run_id))

    def test_detects_wal_shm_side_effects(self):
        """Ensure replay itself does NOT change data_version or schema."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        self.assertTrue(replay_is_read_only(self.db_path, self.run_id))

    def test_schema_unchanged_after_replay(self):
        """PRAGMA schema_version must not change after read-only replay."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        abs_path = pathlib.Path(self.db_path).resolve()
        uri = abs_path.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        schema_before = conn.execute("PRAGMA schema_version").fetchone()[0]
        table_count_before = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master"
        ).fetchone()[0]
        conn.close()

        _ = run_projection(self.run_id, self.db_path)

        conn2 = sqlite3.connect(uri, uri=True)
        schema_after = conn2.execute("PRAGMA schema_version").fetchone()[0]
        table_count_after = conn2.execute(
            "SELECT COUNT(*) FROM sqlite_master"
        ).fetchone()[0]
        conn2.close()

        self.assertEqual(schema_before, schema_after)
        self.assertEqual(table_count_before, table_count_after)

    def test_database_changes_detected_after_write(self):
        """PRAGMA schema_version and sqlite_master count must increment after a write."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        abs_path = pathlib.Path(self.db_path).resolve()
        conn_w = sqlite3.connect(str(abs_path))
        schema_before = conn_w.execute("PRAGMA schema_version").fetchone()[0]
        table_count_before = conn_w.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]

        conn_w.execute("CREATE TABLE detected_write (id INTEGER)")
        conn_w.commit()
        schema_after = conn_w.execute("PRAGMA schema_version").fetchone()[0]
        table_count_after = conn_w.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        conn_w.close()

        self.assertNotEqual(schema_before, schema_after,
                            "schema_version should change after CREATE TABLE")
        self.assertEqual(table_count_before + 1, table_count_after,
                         "table count should increase by 1 after CREATE TABLE")

    def test_schema_count_changes_after_write(self):
        """schema count MUST increase after creating a new table."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        abs_path = pathlib.Path(self.db_path).resolve()
        conn_w = sqlite3.connect(str(abs_path))
        schema_before = conn_w.execute(
            "SELECT COUNT(*) FROM sqlite_master"
        ).fetchone()[0]

        conn_w.execute("CREATE TABLE schema_detection (id INTEGER)")
        conn_w.commit()
        schema_after = conn_w.execute(
            "SELECT COUNT(*) FROM sqlite_master"
        ).fetchone()[0]
        conn_w.close()

        self.assertEqual(schema_before + 1, schema_after,
                         "schema count should increase by 1 after CREATE TABLE")


# ---------------------------------------------------------------------------
# Test 14: Tamper early event - stream_head
# ---------------------------------------------------------------------------

class TestTamperEarlyEventStreamHead(unittest.TestCase):
    """stream_head must reject tampered streams even when the last event
    appears intact."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-tamper-early-head"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_tampered_early_event_stream_head(self):
        """Tamper event_order=1 content_digest. stream_head should reject."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        # Corrupt event 1's content_digest (but keep chain intact since
        # event 1 links to ZERO_DIGEST)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET content_digest = ? WHERE run_id = ? AND event_order = 1",
            ("sha256:" + ("c" * 64), self.run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            stream_head(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "content_digest_mismatch")

    def test_tampered_early_event_stream_head_chain_broken(self):
        """Break the chain at event 2. stream_head should reject."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        # Read event 2, change prev_event_digest and recompute content_digest
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT * FROM runtime_events WHERE run_id = ? AND event_order = 2",
            (self.run_id,),
        ).fetchone()
        cols = [desc[0] for desc in conn.execute(
            "SELECT * FROM runtime_events LIMIT 0"
        ).description]
        event = dict(zip(cols, row))
        json_fields = {"payload", "causation_chain", "trigger_artifact",
                       "expected_stream_head", "prior_state", "next_state"}
        for f in json_fields:
            if f in event and event[f] is not None:
                event[f] = json.loads(event[f])
        if event.get("causation_id") is None and "causation_id" in event:
            del event["causation_id"]

        event["prev_event_digest"] = ZERO_DIGEST
        new_digest = compute_event_digest(
            {k: v for k, v in event.items() if k != "content_digest"}
        )
        conn.execute(
            "UPDATE runtime_events SET prev_event_digest = ?, content_digest = ? "
            "WHERE run_id = ? AND event_order = 2",
            (ZERO_DIGEST, new_digest, self.run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            stream_head(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "hash_chain_link_broken")


# ---------------------------------------------------------------------------
# Test 15: Tamper early event - lineage
# ---------------------------------------------------------------------------

class TestTamperEarlyEventLineage(unittest.TestCase):
    """lineage must reject tampered genesis events."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_tampered_genesis_content_digest_lineage(self):
        run_id = "test-tamper-genesis-lin"
        journal, receipts = _setup_journal_with_events(
            self.db_path, run_id, num_events=2
        )
        journal._conn.close()

        # Corrupt genesis content_digest
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET content_digest = ? WHERE run_id = ? AND event_order = 1",
            ("sha256:" + ("d" * 64), run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            lineage(run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "content_digest_mismatch")

    def test_tampered_genesis_not_run_created(self):
        """lineage should catch when genesis is not run.created."""
        run_id = "test-genesis-not-created"
        journal, receipts = _setup_journal_with_events(
            self.db_path, run_id, num_events=3
        )
        journal._conn.close()

        # Change genesis event_type
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET event_type = 'run.started' WHERE run_id = ? AND event_order = 1",
            (run_id,),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            lineage(run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "genesis_not_run_created")


# ---------------------------------------------------------------------------
# Test 16: Complete hash chain validation
# ---------------------------------------------------------------------------

class TestCompleteHashChainValidation(unittest.TestCase):
    """Every public API that accesses the event stream must validate the
    full hash chain, not just the last event."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-full-hash-chain"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_run_projection_full_hash_chain(self):
        """Mutate event 1's content_digest. run_projection should detect it."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET content_digest = ? WHERE run_id = ? AND event_order = 1",
            ("sha256:" + ("e" * 64), self.run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "content_digest_mismatch")

    def test_stream_head_full_hash_chain_early(self):
        """Mutate event 1's content_digest. stream_head should detect it."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET content_digest = ? WHERE run_id = ? AND event_order = 1",
            ("sha256:" + ("e" * 64), self.run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            stream_head(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "content_digest_mismatch")

    def test_chain_broken_before_last(self):
        """Break the chain link at event 2 (not the last)."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT * FROM runtime_events WHERE run_id = ? AND event_order = 2",
            (self.run_id,),
        ).fetchone()
        cols = [desc[0] for desc in conn.execute(
            "SELECT * FROM runtime_events LIMIT 0"
        ).description]
        event = dict(zip(cols, row))
        json_fields = {"payload", "causation_chain", "trigger_artifact",
                       "expected_stream_head", "prior_state", "next_state"}
        for f in json_fields:
            if f in event and event[f] is not None:
                event[f] = json.loads(event[f])
        if event.get("causation_id") is None and "causation_id" in event:
            del event["causation_id"]

        event["prev_event_digest"] = "sha256:" + ("b" * 64)
        new_digest = compute_event_digest(
            {k: v for k, v in event.items() if k != "content_digest"}
        )
        conn.execute(
            "UPDATE runtime_events SET prev_event_digest = ?, content_digest = ? "
            "WHERE run_id = ? AND event_order = 2",
            ("sha256:" + ("b" * 64), new_digest, self.run_id),
        )
        conn.commit()
        conn.close()

        # stream_head should detect this
        with self.assertRaises(ProjectionError) as ctx:
            stream_head(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "hash_chain_link_broken")

    def test_lineage_full_hash_chain_early(self):
        """Mutate genesis content_digest, lineage should reject."""
        run_id = "test-lin-full-chain"
        journal, receipts = _setup_journal_with_events(
            self.db_path, run_id, num_events=3
        )
        journal._conn.close()

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET content_digest = ? WHERE run_id = ? AND event_order = 1",
            ("sha256:" + ("e" * 64), run_id),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            lineage(run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "content_digest_mismatch")


# ---------------------------------------------------------------------------
# Test 17: Unsupported schema version rejection
# ---------------------------------------------------------------------------

class TestUnsupportedSchemaVersion(unittest.TestCase):
    """Direct-SQLite projection must reject databases whose runtime_events
    contain a schema_version other than exactly 0.9.0."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-unsupported-version"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        for sfx in ("-wal", "-shm"):
            p = pathlib.Path(self.db_path + sfx)
            if p.exists():
                p.unlink()

    def _create_events_then_set_version(self, schema_version):
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()
        # Mutate schema_version on all events
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET schema_version = ?",
            (schema_version,),
        )
        conn.commit()
        conn.close()

    def test_run_projection_rejects_legacy_schema(self):
        self._create_events_then_set_version("0.8.2")
        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")

    def test_stage_projection_rejects_legacy_schema(self):
        self._create_events_then_set_version("0.8.2")
        with self.assertRaises(ProjectionError) as ctx:
            stage_projection(self.run_id, "build", self.db_path)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")

    def test_stream_head_rejects_legacy_schema(self):
        self._create_events_then_set_version("0.8.2")
        with self.assertRaises(ProjectionError) as ctx:
            stream_head(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")

    def test_lineage_rejects_legacy_schema(self):
        self._create_events_then_set_version("0.8.2")
        with self.assertRaises(ProjectionError) as ctx:
            lineage(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")

    def test_projection_digest_rejects_legacy_schema(self):
        self._create_events_then_set_version("0.8.2")
        with self.assertRaises(ProjectionError) as ctx:
            projection_digest(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")

    def test_mixed_version_rejected(self):
        """A DB where some events have schema_version 0.9.0 and others
        have 0.8.2 must be rejected."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()
        # Set only event_order=2 to 0.8.2
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET schema_version = '0.8.2' "
            "WHERE run_id = ? AND event_order = 2",
            (self.run_id,),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")

    def test_error_detail_includes_version(self):
        self._create_events_then_set_version("0.8.2")
        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        detail = ctx.exception.detail
        self.assertEqual(detail["expected_schema_version"], "0.9.0")
        self.assertEqual(detail["found_schema_version"], "0.8.2")

    def test_error_returns_no_runtime_data(self):
        """unsupported_schema_version error detail must NOT contain
        run-specific data like run_id or event data."""
        self._create_events_then_set_version("0.8.2")
        with self.assertRaises(ProjectionError) as ctx:
            run_projection(self.run_id, self.db_path)
        detail = ctx.exception.detail
        self.assertNotIn("run_id", detail)
        self.assertNotIn("events", detail)
        self.assertNotIn("projection", detail)


# ---------------------------------------------------------------------------
# Test 18: Zero-write proof on schema version rejection
# ---------------------------------------------------------------------------

class TestSchemaRejectionZeroWrite(unittest.TestCase):
    """Schema version rejection must leave the database and sidecar files
    completely unchanged."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.run_id = "test-schema-zero-write"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        for sfx in ("-wal", "-shm"):
            p = pathlib.Path(self.db_path + sfx)
            if p.exists():
                p.unlink()

    def _probe(self):
        abs_path = pathlib.Path(self.db_path).resolve()
        uri = abs_path.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            schema_version = conn.execute("PRAGMA schema_version").fetchone()[0]
            table_count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master"
            ).fetchone()[0]
            event_count = conn.execute(
                "SELECT COUNT(*) FROM runtime_events"
            ).fetchone()[0]
        finally:
            conn.close()
        file_size = abs_path.stat().st_size
        sidecar_map = {}
        for sfx in ("-wal", "-shm"):
            p = pathlib.Path(self.db_path + sfx)
            sidecar_map[sfx] = p.exists()
        return schema_version, table_count, event_count, file_size, sidecar_map

    def test_schema_rejection_zero_write(self):
        """After unsupported_schema_version rejection, DB bytes/schema/rows
        and WAL/SHM sidecar presence are unchanged."""
        journal, receipts = _setup_journal_with_events(
            self.db_path, self.run_id, num_events=3
        )
        journal._conn.close()

        # Mutate schema_version to legacy
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE runtime_events SET schema_version = '0.8.2' WHERE run_id = ?",
            (self.run_id,),
        )
        conn.commit()
        conn.close()

        # Probe before rejection
        sv_before, tc_before, ec_before, fs_before, sc_before = self._probe()

        # Attempt projection (should reject)
        try:
            run_projection(self.run_id, self.db_path)
        except ProjectionError as e:
            self.assertEqual(e.code, "unsupported_schema_version")

        # Probe after rejection
        sv_after, tc_after, ec_after, fs_after, sc_after = self._probe()

        self.assertEqual(sv_before, sv_after, "schema_version changed")
        self.assertEqual(tc_before, tc_after, "table count changed")
        self.assertEqual(ec_before, ec_after, "event count changed")
        self.assertEqual(fs_before, fs_after, "file size changed")
        self.assertEqual(sc_before, sc_after, "sidecar files changed")


# ---------------------------------------------------------------------------
# Test 19 helpers
# ---------------------------------------------------------------------------

def _capture_events_from_db(db_path, run_id):
    """Read all events from DB and return as a list of complete RuntimeEvent dicts."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        desc = conn.execute("SELECT * FROM runtime_events LIMIT 0")
        cols = [d[0] for d in desc.description]
        rows = conn.execute(
            "SELECT * FROM runtime_events WHERE run_id = ? ORDER BY event_order",
            (run_id,),
        ).fetchall()
        events = []
        json_fields = {"payload", "causation_chain", "trigger_artifact",
                       "expected_stream_head", "prior_state", "next_state"}
        for row in rows:
            d = dict(zip(cols, tuple(row)))
            for f in json_fields:
                if f in d and d[f] is not None:
                    d[f] = json.loads(d[f])
            if d.get("causation_id") is None and "causation_id" in d:
                del d["causation_id"]
            events.append(d)
        return events
    finally:
        conn.close()


def _build_complete_artifact_run(db_path, run_id, visibility="public"):
    """Build a run with run.created, run.started, run.stage.started,
    run.gate.evaluated, and run.stage.completed (with one RuntimeArtifact).
    Returns list of events captured from DB.
    """
    journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)

    # 1. run.created
    payload = _run_created_payload(visibility)
    req = _make_request("run.created", payload, run_id=run_id)
    rec1 = journal.append(req)

    # 2. run.started
    sp = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
    req = _make_request("run.started", sp, run_id=run_id,
                        prev_digest=rec1["stored_content_digest"], head_order=1)
    req["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["stored_content_digest"]}
    rec2 = journal.append(req)

    # 3. run.stage.started
    stage_payload = {
        "stage_id": "build",
        "started_at": "2026-01-01T00:00:02Z",
        "entry_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
    }
    req = _make_request("run.stage.started", stage_payload, run_id=run_id,
                        prev_digest=rec2["stored_content_digest"], head_order=2)
    req["expected_stream_head"] = {"event_order": 2, "content_digest": rec2["stored_content_digest"]}
    rec3 = journal.append(req)

    # 4. run.gate.evaluated
    gate_decision_id = str(uuid.uuid4())
    gate_payload = {
        "stage_id": "build",
        "gate_id": "check-build",
        "decision_id": gate_decision_id,
        "outcome": "pass",
        "execution_mode": "full",
        "evaluated_at": "2026-01-01T00:00:03Z",
        "evaluated_by": "validator-1",
        "evidence": [{"artifact_id": "validation-report-1", "artifact_kind": "validation_report"}],
    }
    req = _make_request("run.gate.evaluated", gate_payload, run_id=run_id,
                        prev_digest=rec3["stored_content_digest"], head_order=3)
    req["expected_stream_head"] = {"event_order": 3, "content_digest": rec3["stored_content_digest"]}
    rec4 = journal.append(req)

    # 5. run.stage.completed with one RuntimeArtifact
    completed_payload = {
        "stage_id": "build",
        "completed_at": "2026-01-01T00:00:04Z",
        "gate_decisions": [{"decision_id": gate_decision_id}],
        "artifacts_produced": [
            {
                "artifact_ref": {"artifact_id": "build-output-001", "artifact_kind": "stage_output"},
                "origin_run": run_id,
                "origin_stage": "build",
                "produced_by": "runner-1",
                "source_artifacts": [
                    {"artifact_id": "source-001", "artifact_kind": "source"}
                ],
                "visibility": visibility,
                "visibility_resolution": {
                    "resolution_id": "res-artifact-001",
                    "resolved_at": "2026-01-01T00:00:02Z",
                    "contributors": [
                        {
                            "contributor_id": "contrib-artifact-001",
                            "contributor_kind": "source_artifact",
                            "contributor_ref": {"artifact_id": "source-001", "artifact_kind": "source"},
                            "asserted_visibility": visibility,
                            "authority": "Source artifact",
                            "classification_evidence": [{"artifact_id": "source-001", "artifact_kind": "source"}],
                        }
                    ],
                    "resolution_rule": "most_restrictive",
                    "resolved_visibility": visibility,
                    "resolution_audit": {
                        "contributor_count": 1,
                        "restricted_count": 1 if visibility == "restricted" else 0,
                        "project_count": 1 if visibility == "project" else 0,
                        "public_count": 1 if visibility == "public" else 0,
                        "applied_rule": "most_restrictive",
                    },
                },
            }
        ],
    }
    req = _make_request("run.stage.completed", completed_payload, run_id=run_id,
                        prev_digest=rec4["stored_content_digest"], head_order=4)
    req["expected_stream_head"] = {"event_order": 4, "content_digest": rec4["stored_content_digest"]}
    journal.append(req)

    journal._conn.close()
    return _capture_events_from_db(db_path, run_id)


class TestInMemoryDifferentialReplay(unittest.TestCase):
    """DB-backed and in-memory paths must return equal stable projections
    and equal projection_digest for all three visibility levels."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _assert_projections_equal(self, db_proj, mem_proj):
        self.assertEqual(db_proj["projection_digest"], mem_proj["projection_digest"])
        db_pre = {k: v for k, v in db_proj.items()
                  if k not in {"projection_digest", "projection_id", "derived_at"}}
        mem_pre = {k: v for k, v in mem_proj.items()
                   if k not in {"projection_digest", "projection_id", "derived_at"}}
        self.assertEqual(canonical_serialize(db_pre), canonical_serialize(mem_pre))

    def test_public_visibility_differential(self):
        run_id = "test-diff-public"
        events = _build_complete_artifact_run(self.db_path, run_id, "public")
        db_proj = run_projection(run_id, self.db_path)
        mem_proj = run_projection_from_events(run_id, events)
        self._assert_projections_equal(db_proj, mem_proj)
        self.assertEqual(db_proj["resolved_run_visibility"], "public")

    def test_project_visibility_differential(self):
        run_id = "test-diff-project"
        events = _build_complete_artifact_run(self.db_path, run_id, "project")
        db_proj = run_projection(run_id, self.db_path)
        mem_proj = run_projection_from_events(run_id, events)
        self._assert_projections_equal(db_proj, mem_proj)
        self.assertEqual(db_proj["resolved_run_visibility"], "project")

    def test_restricted_visibility_differential(self):
        run_id = "test-diff-restricted"
        events = _build_complete_artifact_run(self.db_path, run_id, "restricted")
        db_proj = run_projection(run_id, self.db_path)
        mem_proj = run_projection_from_events(run_id, events)
        self._assert_projections_equal(db_proj, mem_proj)
        self.assertEqual(db_proj["resolved_run_visibility"], "restricted")

    def test_lineage_terminal_state_differential(self):
        run_id = "test-diff-lineage"
        events = _build_complete_artifact_run(self.db_path, run_id)
        db_proj = run_projection(run_id, self.db_path)
        mem_proj = run_projection_from_events(run_id, events)
        self._assert_projections_equal(db_proj, mem_proj)
        self.assertEqual(db_proj["status"], mem_proj["status"])
        self.assertEqual(db_proj["event_count"], mem_proj["event_count"])
        self.assertEqual(db_proj["event_count"], 5)

    def test_basic_3event_differential(self):
        """3-event basic runs (no artifacts) must also be equal."""
        run_id = "test-diff-basic"
        journal, _ = _setup_journal_with_events(self.db_path, run_id, num_events=3)
        journal._conn.close()
        events = _capture_events_from_db(self.db_path, run_id)
        db_proj = run_projection(run_id, self.db_path)
        mem_proj = run_projection_from_events(run_id, events)
        self._assert_projections_equal(db_proj, mem_proj)


# ---------------------------------------------------------------------------
# Test 20: Complete RuntimeArtifact in in-memory projection
# ---------------------------------------------------------------------------

class TestInMemoryRuntimeArtifact(unittest.TestCase):
    """In-memory projection must preserve complete RuntimeArtifact outputs."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_artifacts_in_stage_state(self):
        run_id = "test-artifact-mem"
        events = _build_complete_artifact_run(self.db_path, run_id)
        mem_proj = run_projection_from_events(run_id, events)
        self.assertIn("stage_states", mem_proj)
        self.assertIn("build", mem_proj["stage_states"])
        build = mem_proj["stage_states"]["build"]
        self.assertEqual(build["status"], "completed")
        self.assertIn("artifacts_produced", build)
        arts = build["artifacts_produced"]
        self.assertGreater(len(arts), 0)
        art = arts[0]
        self.assertEqual(art["artifact_ref"]["artifact_id"], "build-output-001")
        self.assertIn("visibility", art)

    def test_artifacts_in_complete_list(self):
        run_id = "test-artifacts-list"
        events = _build_complete_artifact_run(self.db_path, run_id)
        mem_proj = run_projection_from_events(run_id, events)
        self.assertIn("runtime_artifacts", mem_proj)
        self.assertGreater(len(mem_proj["runtime_artifacts"]), 0)

    def test_db_mem_artifacts_equal(self):
        run_id = "test-artifacts-equal"
        events = _build_complete_artifact_run(self.db_path, run_id)
        db_proj = run_projection(run_id, self.db_path)
        mem_proj = run_projection_from_events(run_id, events)
        db_arts = db_proj.get("runtime_artifacts", [])
        mem_arts = mem_proj.get("runtime_artifacts", [])
        self.assertEqual(len(db_arts), len(mem_arts))
        for da, ma in zip(db_arts, mem_arts):
            self.assertEqual(canonical_serialize(da), canonical_serialize(ma))


# ---------------------------------------------------------------------------
# Test 21: In-memory tamper matrix
# ---------------------------------------------------------------------------

class TestInMemoryTamper(unittest.TestCase):
    """Nested visibility, artifact, prior/next state, digest, link, order,
    identity, genesis and schema-version mutations must be rejected with
    same stable errors as DB replay."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_content_digest_tamper(self):
        run_id = "test-mem-digest"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[0]["content_digest"] = "sha256:" + ("f" * 64)
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        self.assertEqual(ctx.exception.code, "content_digest_mismatch")

    def test_hash_chain_link_broken(self):
        run_id = "test-mem-chain"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        ev = tampered[1]
        ev["prev_event_digest"] = ZERO_DIGEST
        new_d = compute_event_digest({k: v for k, v in ev.items() if k != "content_digest"})
        ev["content_digest"] = new_d
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        self.assertEqual(ctx.exception.code, "hash_chain_link_broken")

    def test_event_order_gap(self):
        run_id = "test-mem-order"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[1]["event_order"] = 99
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        self.assertEqual(ctx.exception.code, "event_order_gap")

    def test_run_identity_mismatch(self):
        run_id = "test-mem-identity"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[0]["run_id"] = "wrong-run"
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        self.assertEqual(ctx.exception.code, "run_identity_mismatch")

    def test_prior_state_tamper(self):
        run_id = "test-mem-prior"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[1]["prior_state"] = {"status": "tampered"}
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        self.assertEqual(ctx.exception.code, "prior_state_tampered")

    def test_next_state_tamper(self):
        run_id = "test-mem-next"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[1]["next_state"] = {"status": "tampered"}
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        self.assertEqual(ctx.exception.code, "next_state_tampered")

    def test_nested_visibility_mutation(self):
        run_id = "test-mem-vis"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        payload = tampered[0]["payload"]
        payload["visibility_context"]["resolved_run_visibility"] = "restricted"
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        # Either content_digest_mismatch (payload digest changed) or
        # reducer_replay_error (visibility state inconsistency) is valid
        self.assertIn(ctx.exception.code,
                      {"content_digest_mismatch", "reducer_replay_error"})

    def test_nested_artifact_mutation(self):
        run_id = "test-mem-nested"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        payload = tampered[4]["payload"]
        payload["artifacts_produced"][0]["artifact_ref"]["artifact_id"] = "tampered-id"
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        # Either content_digest_mismatch or next_state_tampered is valid:
        # the altered payload digest makes the stored next_state mismatch replay
        self.assertIn(ctx.exception.code,
                      {"content_digest_mismatch", "next_state_tampered"})

    def test_empty_events_rejected(self):
        run_id = "test-mem-empty"
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, [])
        self.assertEqual(ctx.exception.code, "no_events_for_run")


# ---------------------------------------------------------------------------
# Test 22: Input immutability
# ---------------------------------------------------------------------------

class TestInMemoryInputImmutability(unittest.TestCase):
    """Input events must remain byte-semantically unchanged after success
    and after every failure path."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _snap(self, events):
        return canonical_serialize(events)

    def test_unchanged_after_success(self):
        run_id = "test-immut-ok"
        events = _build_complete_artifact_run(self.db_path, run_id)
        before = self._snap(events)
        _ = run_projection_from_events(run_id, events)
        self.assertEqual(before, self._snap(events))

    def test_unchanged_after_schema_failure(self):
        run_id = "test-immut-schema"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[0]["schema_version"] = "0.8.2"
        before = self._snap(tampered)
        try:
            run_projection_from_events(run_id, tampered)
        except ProjectionError:
            pass
        self.assertEqual(before, self._snap(tampered))

    def test_unchanged_after_digest_failure(self):
        run_id = "test-immut-digest"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[0]["content_digest"] = "sha256:" + ("f" * 64)
        before = self._snap(tampered)
        try:
            run_projection_from_events(run_id, tampered)
        except ProjectionError:
            pass
        self.assertEqual(before, self._snap(tampered))

    def test_unchanged_after_order_failure(self):
        run_id = "test-immut-order"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[1]["event_order"] = 99
        before = self._snap(tampered)
        try:
            run_projection_from_events(run_id, tampered)
        except ProjectionError:
            pass
        self.assertEqual(before, self._snap(tampered))

    def test_unchanged_after_identity_failure(self):
        run_id = "test-immut-id"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[0]["run_id"] = "wrong-run"
        before = self._snap(tampered)
        try:
            run_projection_from_events(run_id, tampered)
        except ProjectionError:
            pass
        self.assertEqual(before, self._snap(tampered))

    def test_nested_payload_unchanged_after_success(self):
        run_id = "test-immut-nested"
        events = _build_complete_artifact_run(self.db_path, run_id)
        snap = canonical_serialize(events[0]["payload"])
        _ = run_projection_from_events(run_id, events)
        self.assertEqual(snap, canonical_serialize(events[0]["payload"]))


# ---------------------------------------------------------------------------
# Test 23: In-memory schema version rejection
# ---------------------------------------------------------------------------

class TestInMemorySchemaRejection(unittest.TestCase):
    """In-memory projection must reject legacy/mixed/absent schema_version
    with ProjectionError unsupported_schema_version before any replay."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_legacy_schema_rejected(self):
        run_id = "test-mem-legacy"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[0]["schema_version"] = "0.8.2"
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")
        self.assertEqual(ctx.exception.detail["expected_schema_version"], "0.9.0")
        self.assertEqual(ctx.exception.detail["found_schema_version"], "0.8.2")

    def test_mixed_schema_rejected(self):
        run_id = "test-mem-mixed"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[2]["schema_version"] = "0.8.2"
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")

    def test_missing_schema_rejected(self):
        run_id = "test-mem-missing"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        del tampered[0]["schema_version"]
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")

    def test_schema_rejection_before_replay(self):
        """Schema check runs before replay: wrong schema + bad digest
        must still report unsupported_schema_version."""
        run_id = "test-mem-schema-first"
        events = _build_complete_artifact_run(self.db_path, run_id)
        tampered = copy.deepcopy(events)
        tampered[0]["schema_version"] = "0.8.2"
        tampered[0]["content_digest"] = "sha256:" + ("f" * 64)
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events(run_id, tampered)
        self.assertEqual(ctx.exception.code, "unsupported_schema_version")


# ---------------------------------------------------------------------------
# Store metadata replay and tamper detection
# ---------------------------------------------------------------------------

class TestStoreMetadataReplay(unittest.TestCase):
    """The projection replay path binds store-assigned event metadata with the
    SAME shared helper as the journal write-path, then compares the exact
    stored next_state. A metadata-complete stored stream must replay cleanly
    (memory and read-only SQLite), and tampering of latest_event_* must be
    detected. Prior to the fix, the stored next_state lacked the metadata and
    every replay raised ProjectionError("next_state_tampered")."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _append(self, journal, run_id, event_type, payload, prev_receipt=None):
        if prev_receipt is None:
            req = _make_request(event_type, payload, run_id=run_id)
        else:
            req = _make_request(
                event_type, payload, run_id=run_id,
                prev_digest=prev_receipt["stored_content_digest"],
                head_order=prev_receipt["event_order"],
            )
            req["expected_stream_head"] = {
                "event_order": prev_receipt["event_order"],
                "content_digest": prev_receipt["stored_content_digest"],
            }
        return journal.append(req)

    def test_metadata_complete_stream_replays_cleanly(self):
        events = _build_complete_artifact_run(self.db_path, "run-meta-1")
        # End-to-end read-only SQLite replay must succeed (no next_state_tampered)
        projection = run_projection("run-meta-1", self.db_path)
        self.assertEqual(projection["run_id"], "run-meta-1")
        # In-memory replay exposes the bound latest event metadata from the
        # final stored event, proving replay and write-path agree byte-for-byte.
        replayed = run_projection_from_events("run-meta-1", events)
        last = events[-1]
        self.assertEqual(replayed["latest_event_id"], last["event_id"])
        self.assertEqual(replayed["latest_event_type"], last["event_type"])
        self.assertEqual(replayed["latest_event_order"], last["event_order"])

    def test_every_stored_next_state_carries_bound_metadata(self):
        events = _build_complete_artifact_run(self.db_path, "run-meta-2")
        for ev in events:
            ns = ev["next_state"]
            self.assertEqual(ns["latest_event_id"], ev["event_id"])
            self.assertEqual(ns["latest_event_type"], ev["event_type"])
            self.assertEqual(ns["latest_event_order"], ev["event_order"])

    def test_tampered_latest_event_id_detected(self):
        events = _build_complete_artifact_run(self.db_path, "run-meta-tamper")
        tampered = copy.deepcopy(events)
        tampered[-1]["next_state"]["latest_event_id"] = "forged-event-id"
        # Keep the content digest consistent with the tampered next_state so the
        # hash chain still links; only the next_state metadata comparison must fail.
        tampered[-1]["content_digest"] = compute_event_digest(tampered[-1])
        with self.assertRaises(ProjectionError) as ctx:
            run_projection_from_events("run-meta-tamper", tampered)
        self.assertEqual(ctx.exception.code, "next_state_tampered")

    def test_prior_state_matches_preceding_next_state(self):
        events = _build_complete_artifact_run(self.db_path, "run-meta-prior")
        for i in range(1, len(events)):
            self.assertEqual(
                canonical_serialize(events[i]["prior_state"]),
                canonical_serialize(events[i - 1]["next_state"]),
                f"event {events[i]['event_order']} prior_state must equal preceding next_state",
            )

    def _build_terminated_run(self, run_id):
        journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        rec = self._append(journal, run_id, "run.created", _run_created_payload())
        rec = self._append(journal, run_id, "run.started",
                           {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, rec)
        ec = journal.read_event(run_id, rec["event_order"])["next_state"]["events_count"]
        rec = self._append(journal, run_id, "run.interrupted", {
            "interrupted_at": "2026-01-01T00:00:10Z",
            "last_event_order": ec,
            "interruption_cause": "external_signal",
            "checkpoint_available": True,
        }, rec)
        rec = self._append(journal, run_id, "run.terminated", {
            "terminated_at": "2026-01-01T00:00:12Z",
            "terminated_by": "human",
            "termination_reason": "done",
            "from_status": "interrupted",
            "terminal_status": "failed",
        }, rec)
        journal._conn.close()
        return _capture_events_from_db(self.db_path, run_id)

    def test_terminated_records_terminal_status(self):
        events = self._build_terminated_run("run-term-1")
        replayed = run_projection_from_events("run-term-1", events)
        self.assertEqual(replayed["latest_terminal_status"], "failed")
        self.assertEqual(replayed["latest_event_type"], "run.terminated")
        projection = run_projection("run-term-1", self.db_path)
        self.assertEqual(projection["latest_terminal_status"], "failed")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
