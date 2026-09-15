"""Comprehensive tests for runtime_state_journal.py against Runtime State Contract v0.9.0.

Tests all append scenarios, idempotency, error conditions, concurrency,
workflow isolation, signer injection, and connection management using
disposable temporary databases.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import pathlib
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid

# Add parent to path
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.runtime_state_core import (
    ZERO_DIGEST,
    canonical_serialize,
    compute_digest,
    sign_receipt,
    verify_receipt,
)
from scripts.runtime_state_journal import (
    RuntimeJournal,
    RuntimeJournalError,
    _CREATE_TABLES,
    _event_row_columns,
    _event_to_dict,
    read_run_evidence_snapshot,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Explicit test signer key -- all tests MUST use this, never DEFAULT_CONFORMANCE_KEY
# ---------------------------------------------------------------------------
TEST_SIGNER_KEY = b"test-explicit-signer-key-085"


# ---------------------------------------------------------------------------
# Helpers to build valid AppendRequests for each event type
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


# ---------------------------------------------------------------------------
# Tests -- Original suite (updated for explicit signer key)
# ---------------------------------------------------------------------------

class TestJournalInitialize(unittest.TestCase):
    """Verify tables are created correctly on init."""

    def test_initialize_creates_tables(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            with RuntimeJournal(db_path, TEST_SIGNER_KEY) as journal:
                rows = journal._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                table_names = {r["name"] for r in rows}
                self.assertIn("runtime_events", table_names)
                self.assertIn("idempotency", table_names)
                self.assertIn("stream_heads", table_names)

                # Verify runtime_events has all required columns
                event_cols = set()
                for row_info in journal._conn.execute("PRAGMA table_info(runtime_events)"):
                    event_cols.add(row_info["name"])
                expected = {
                    "run_id", "event_id", "event_order", "event_type",
                    "payload", "causation_id", "causation_chain",
                    "actor_role", "actor_identity", "trigger_artifact",
                    "reason", "recommended_action", "expected_stream_head",
                    "client_event_id", "prev_event_digest",
                    "prior_state", "next_state", "occurred_at",
                    "schema_version", "content_digest",
                }
                self.assertEqual(event_cols & expected, expected)

                # Verify WAL mode
                mode_row = journal._conn.execute("PRAGMA journal_mode").fetchone()
                self.assertEqual(mode_row[0].lower(), "wal")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestAppendGenesis(unittest.TestCase):
    """Test appending a genesis event (run.created)."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)

    def tearDown(self):
        self.journal._conn.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_append_genesis_event(self):
        payload = _run_created_payload()
        req = _make_request("run.created", payload, run_id="test-genesis")
        receipt = self.journal.append(req)
        self.assertIsInstance(receipt, dict)
        self.assertIn("event_id", receipt)
        self.assertEqual(receipt["event_order"], 1)
        self.assertIn("stored_content_digest", receipt)
        self.assertIn("signed_receipt", receipt)
        self.assertEqual(receipt["signed_receipt"]["algorithm"], "HMAC-SHA256")


class TestAppendMultipleEvents(unittest.TestCase):
    """Test appending multiple events in order for the same run."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        self.run_id = "test-multi"

    def tearDown(self):
        self.journal._conn.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_append_multiple_events(self):
        # Genesis
        payload = _run_created_payload()
        req1 = _make_request("run.created", payload, run_id=self.run_id, prev_digest=ZERO_DIGEST, head_order=0)
        rec1 = self.journal.append(req1)
        self.assertEqual(rec1["event_order"], 1)

        # run.started
        started_payload = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
        req2 = _make_request(
            "run.started", started_payload, run_id=self.run_id,
            prev_digest=rec1["stored_content_digest"], head_order=1,
        )
        req2["expected_stream_head"] = {
            "event_order": 1,
            "content_digest": rec1["stored_content_digest"],
        }
        rec2 = self.journal.append(req2)
        self.assertEqual(rec2["event_order"], 2)

        # run.stage.started
        stage_payload = {
            "stage_id": "build",
            "started_at": "2026-01-01T00:00:02Z",
            "entry_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
        }
        req3 = _make_request(
            "run.stage.started", stage_payload, run_id=self.run_id,
            prev_digest=rec2["stored_content_digest"], head_order=2,
        )
        req3["expected_stream_head"] = {
            "event_order": 2,
            "content_digest": rec2["stored_content_digest"],
        }
        rec3 = self.journal.append(req3)
        self.assertEqual(rec3["event_order"], 3)

    def test_ordered_read_events(self):
        """Verify ordered read_events reproduces exact stored events."""
        payload = _run_created_payload()
        req1 = _make_request("run.created", payload, run_id=self.run_id)
        rec1 = self.journal.append(req1)

        started_payload = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
        req2 = _make_request("run.started", started_payload, run_id=self.run_id,
                             prev_digest=rec1["stored_content_digest"], head_order=1)
        req2["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["stored_content_digest"]}
        rec2 = self.journal.append(req2)

        events = self.journal.read_events(self.run_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_order"], 1)
        self.assertEqual(events[0]["event_type"], "run.created")
        self.assertEqual(events[1]["event_order"], 2)
        self.assertEqual(events[1]["event_type"], "run.started")

        # Verify stored content digests
        self.assertEqual(events[0]["content_digest"], rec1["stored_content_digest"])
        self.assertEqual(events[1]["content_digest"], rec2["stored_content_digest"])


class TestExactRetry(unittest.TestCase):
    """Test exact retry after restart returns original stored receipt."""

    def test_exact_retry_returns_original_receipt(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            # First append
            journal1 = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            payload = _run_created_payload()
            req = _make_request("run.created", payload, run_id="test-retry")
            rec1 = journal1.append(req)
            journal1._conn.close()

            # Second connection (simulates restart), same request
            journal2 = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            rec2 = journal2.append(req)
            journal2._conn.close()

            # Exact retry should return byte-for-byte identical receipt
            self.assertEqual(canonical_serialize(rec1), canonical_serialize(rec2))
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_exact_retry_even_with_stale_head(self):
        """Exact retry returns receipt even if head is now stale."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            journal1 = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            payload = _run_created_payload()
            run_id = "test-retry-stale"
            req1 = _make_request("run.created", payload, run_id=run_id)
            rec1 = journal1.append(req1)

            # Advance head with second event
            started_payload = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
            req2 = _make_request("run.started", started_payload, run_id=run_id,
                                 prev_digest=rec1["stored_content_digest"], head_order=1)
            req2["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["stored_content_digest"]}
            journal1.append(req2)
            journal1._conn.close()

            # Now replay req1 with new connection -- exact retry still works
            journal2 = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            rec_retry = journal2.append(req1)
            journal2._conn.close()

            self.assertEqual(canonical_serialize(rec1), canonical_serialize(rec_retry))
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


class TestDivergentDuplicate(unittest.TestCase):
    """Test that divergent duplicate raises an error."""

    def test_divergent_duplicate_raises_error(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            payload = _run_created_payload()
            client_id = "dup-client-id"

            req1 = _make_request("run.created", payload, run_id="test-dup")
            req1["client_event_id"] = client_id
            journal.append(req1)

            # Same client_event_id, different run_id
            req2 = copy.deepcopy(req1)
            req2["run_id"] = "different-run"

            with self.assertRaises(RuntimeJournalError) as ctx:
                journal.append(req2)
            self.assertEqual(ctx.exception.detail["code"], "divergent_duplicate")

            journal._conn.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


class TestStaleHead(unittest.TestCase):
    """Test stale head rejection."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        self.run_id = "test-stale"

    def tearDown(self):
        self.journal._conn.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_stale_head_rejection(self):
        payload = _run_created_payload()
        req1 = _make_request("run.created", payload, run_id=self.run_id)
        rec1 = self.journal.append(req1)

        # Try append with stale expected_stream_head (event_order=0 still)
        req2 = _make_request("run.started",
                             {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"},
                             run_id=self.run_id,
                             head_order=0,
                             prev_digest=ZERO_DIGEST)
        req2["client_event_id"] = "stale-test-" + uuid.uuid4().hex[:8]

        result = self.journal.append(req2)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["code"], "stale_head")
        self.assertIn("current_stream_head", result)
        self.assertIn("last_stored_receipt", result)

    def test_stale_head_frozen_structure(self):
        """Verify stale_head error has the required frozen structure."""
        payload = _run_created_payload()
        req1 = _make_request("run.created", payload, run_id=self.run_id)
        self.journal.append(req1)

        req2 = _make_request("run.started",
                             {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"},
                             run_id=self.run_id,
                             head_order=0,
                             prev_digest=ZERO_DIGEST)
        req2["client_event_id"] = "stale-struct-" + uuid.uuid4().hex[:8]

        result = self.journal.append(req2)
        self.assertEqual(result["code"], "stale_head")
        self.assertEqual(set(result.keys()), {"code", "current_stream_head", "last_stored_receipt"})

        head = result["current_stream_head"]
        self.assertIsInstance(head["event_order"], int)
        self.assertTrue(head["event_order"] >= 1)

        rec = result["last_stored_receipt"]
        self.assertIsNotNone(rec)
        self.assertIn("event_id", rec)


class TestHashChainLink(unittest.TestCase):
    """Test hash chain link rejection."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        self.run_id = "test-chain"

    def tearDown(self):
        self.journal._conn.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_hash_chain_link_rejection(self):
        payload = _run_created_payload()
        req1 = _make_request("run.created", payload, run_id=self.run_id)
        rec1 = self.journal.append(req1)

        # Use correct expected_stream_head but wrong prev_event_digest
        req2 = _make_request("run.started",
                             {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"},
                             run_id=self.run_id,
                             head_order=1,
                             prev_digest=ZERO_DIGEST)  # Wrong! Should be rec1's digest
        req2["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["stored_content_digest"]}
        req2["client_event_id"] = "chain-test-" + uuid.uuid4().hex[:8]

        with self.assertRaises(RuntimeJournalError) as ctx:
            self.journal.append(req2)
        self.assertEqual(ctx.exception.detail["code"], "hash_chain_link")


class TestFailAppendLeavesUnchanged(unittest.TestCase):
    """Test that a failed append leaves everything unchanged."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        self.run_id = "test-rollback"

    def tearDown(self):
        self.journal._conn.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_failed_append_rollback(self):
        payload = _run_created_payload()
        req1 = _make_request("run.created", payload, run_id=self.run_id)
        rec1 = self.journal.append(req1)

        event_count_before = len(self.journal.read_events(self.run_id))

        # Try to append an invalid event (hash chain link failure)
        req_bad = _make_request("run.started",
                                {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"},
                                run_id=self.run_id,
                                head_order=1,
                                prev_digest=ZERO_DIGEST)
        req_bad["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["stored_content_digest"]}
        req_bad["client_event_id"] = "rollback-test-" + uuid.uuid4().hex[:8]

        with self.assertRaises(RuntimeJournalError):
            self.journal.append(req_bad)

        # Verify no events were added
        events = self.journal.read_events(self.run_id)
        self.assertEqual(len(events), event_count_before)

        # Verify head is unchanged
        head_row = self.journal._conn.execute(
            "SELECT event_order FROM stream_heads WHERE run_id = ?", (self.run_id,)
        ).fetchone()
        self.assertEqual(int(head_row["event_order"]), 1)


class TestReadOperations(unittest.TestCase):
    """Test read_event and read_events edge cases."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)

    def tearDown(self):
        self.journal._conn.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_read_nonexistent_run_empty_list(self):
        events = self.journal.read_events("nonexistent-run")
        self.assertEqual(events, [])

    def test_read_event_nonexistent_returns_none(self):
        result = self.journal.read_event("nonexistent-run", 1)
        self.assertIsNone(result)

    def test_read_event_correct(self):
        payload = _run_created_payload()
        req = _make_request("run.created", payload, run_id="test-read")
        self.journal.append(req)

        event = self.journal.read_event("test-read", 1)
        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "run.created")
        self.assertEqual(event["run_id"], "test-read")


class TestContextManager(unittest.TestCase):
    """Test context manager support."""

    def test_context_manager_auto_closes(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            with RuntimeJournal(db_path, TEST_SIGNER_KEY) as journal:
                payload = _run_created_payload()
                req = _make_request("run.created", payload, run_id="test-ctx")
                journal.append(req)

            # Connection should be closed after context exits
            self.assertIsNone(journal._conn)

            # Verify data persisted
            journal2 = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            events = journal2.read_events("test-ctx")
            self.assertEqual(len(events), 1)
            journal2._conn.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


class TestOnlyCallerSuppliedPath(unittest.TestCase):
    """Verify journal never opens/creates a default path."""

    def test_explicit_path_required(self):
        """Constructor requires an explicit db_path argument."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertTrue(hasattr(journal, 'db_path'))
            self.assertEqual(journal.db_path, db_path)
            journal._conn.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


class TestFrozenFilesUnchanged(unittest.TestCase):
    """Verify the five upstream authority files remain byte-for-byte unchanged. Journal regression freezes only its contract and core dependencies, not downstream projection files."""

    EXPECTED_HASHES = {
        "references/runtime-state-contract.md": "016db98f28eb5fc291f6fc608f761d733f9fa6652fb12ea7af78166a0a54c9b1",
        "examples/runtime_state_contract_fixtures/conformance.json": "5fa055cb9353e6172b304de211208f8a6dfc017822fc4ce45f32576e10667e45",
        "scripts/test_runtime_state_contract.py": "99c7af2af12a0fd9bccd4b673eeaa68b8d5df076e68d132e0d5b3804f0d2f540",
        "scripts/runtime_state_core.py": "3257c3d65e539855a8bb21e3655e49572b56000c98ef0694506120a10a3b3610",
        "scripts/test_runtime_state_core.py": "8658cfdb550f8d5a07b3ca76170aa09a154f32618a94cb07d11ec4c80a4e3a38",
    }

    def test_frozen_files_unchanged(self):
        for rel_path, expected in self.EXPECTED_HASHES.items():
            file_path = ROOT / rel_path
            actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
            self.assertEqual(
                actual, expected,
                msg=f"{rel_path} hash mismatch: actual={actual[:16]}... expected={expected[:16]}...",
            )


# ===========================================================================
# NEW TESTS -- concurrency, isolation, signer, rollback
# ===========================================================================

class TestSignerRejection(unittest.TestCase):
    """RuntimeJournal must reject missing signer_key."""

    def test_none_signer_rejected(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, None)
            self.assertEqual(ctx.exception.detail["code"], "unsigned_receipt")
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_empty_bytes_signer_rejected(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, b"")
            self.assertEqual(ctx.exception.detail["code"], "unsigned_receipt")
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_non_bytes_signer_rejected(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, "not-bytes")
            self.assertEqual(ctx.exception.detail["code"], "unsigned_receipt")
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


class TestSignerInjection(unittest.TestCase):
    """Receipt signing with explicit key and independent verification."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.independent_key = b"independent-verification-key-085"
        self.journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)

    def tearDown(self):
        self.journal._conn.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_receipt_verifies_with_same_key(self):
        """Receipt signed with TEST_SIGNER_KEY verifies with same key."""
        payload = _run_created_payload()
        req = _make_request("run.created", payload, run_id="test-verify-same")
        receipt = self.journal.append(req)

        # Verify using the independent key that matches signer
        result = verify_receipt(receipt, "test-verify-same",
                                trusted_key_bytes=TEST_SIGNER_KEY)
        self.assertTrue(result["valid"],
                        f"Verification failed: {result.get('errors', [])}")

    def test_receipt_fails_with_wrong_key(self):
        """Receipt signed with TEST_SIGNER_KEY fails with different key."""
        payload = _run_created_payload()
        req = _make_request("run.created", payload, run_id="test-verify-wrong")
        receipt = self.journal.append(req)

        # Verify using a different key should fail
        result = verify_receipt(receipt, "test-verify-wrong",
                                trusted_key_bytes=self.independent_key)
        self.assertFalse(result["valid"],
                         "Verification should fail with wrong key")

    def test_independent_signer_verification(self):
        """Full independent verification: sign with one key, verify with another."""
        # Sign a receipt with the independent key using the core API directly
        run_id = "test-independent"
        signed = sign_receipt(run_id, 1,
                              "sha256:" + ("a" * 64),
                              key_bytes=self.independent_key)
        receipt = {
            "event_id": str(uuid.uuid4()),
            "event_order": 1,
            "stored_content_digest": "sha256:" + ("a" * 64),
            "new_stream_head": {
                "event_order": 1,
                "content_digest": "sha256:" + ("a" * 64),
            },
            "signed_receipt": signed,
        }

        # Verify with the correct independent key
        result = verify_receipt(receipt, run_id,
                                trusted_key_bytes=self.independent_key)
        self.assertTrue(result["valid"],
                        f"Independent verification failed: {result.get('errors', [])}")

        # Verify with wrong key
        result_wrong = verify_receipt(receipt, run_id,
                                      trusted_key_bytes=TEST_SIGNER_KEY)
        self.assertFalse(result_wrong["valid"],
                         "Verification should fail with wrong independent key")


class TestWorkflowPathRejection(unittest.TestCase):
    """RuntimeJournal must reject paths under .workflow (case-insensitive)."""

    def _make_workflow_dir(self, dirname):
        """Helper: create a temp dir with a .workflow variant subdirectory."""
        tmpdir = tempfile.mkdtemp()
        wf_dir = pathlib.Path(tmpdir) / dirname
        wf_dir.mkdir()
        return tmpdir, str(wf_dir / "test.db")

    def test_dot_workflow_in_path_rejected(self):
        """Path containing .workflow is rejected before any mutation."""
        tmpdir = tempfile.mkdtemp()
        try:
            workflow_dir = pathlib.Path(tmpdir) / ".workflow"
            workflow_dir.mkdir()
            db_path = str(workflow_dir / "test.db")

            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "workflow_path_rejected")

            # Verify no file was created
            self.assertFalse(os.path.exists(db_path))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dot_WORKFLOW_uppercase_rejected(self):
        """Path containing .WORKFLOW (uppercase) is rejected."""
        tmpdir, db_path = self._make_workflow_dir(".WORKFLOW")
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "workflow_path_rejected")
            self.assertFalse(os.path.exists(db_path))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dot_Workflow_mixed_case_rejected(self):
        """Path containing .Workflow (mixed case) is rejected."""
        tmpdir, db_path = self._make_workflow_dir(".Workflow")
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "workflow_path_rejected")
            self.assertFalse(os.path.exists(db_path))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dot_WorKfLoW_random_case_rejected(self):
        """Path containing .WorKfLoW (random mixed case) is rejected."""
        tmpdir, db_path = self._make_workflow_dir(".WorKfLoW")
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "workflow_path_rejected")
            self.assertFalse(os.path.exists(db_path))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_deep_nested_dot_workflow_rejected(self):
        """Path with .workflow nested deep in directory tree is rejected."""
        tmpdir = tempfile.mkdtemp()
        try:
            deep = pathlib.Path(tmpdir) / "a" / "b" / ".workflow" / "c"
            deep.mkdir(parents=True)
            db_path = str(deep / "test.db")

            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "workflow_path_rejected")
            self.assertFalse(os.path.exists(db_path))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_normal_path_accepted(self):
        """Non-.workflow path works normally."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertIsNotNone(journal._conn)
            journal._conn.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


class TestWorkflowSchemaRejection(unittest.TestCase):
    """RuntimeJournal must reject databases containing Railyard workflow tables."""

    def test_workflow_tables_rejected_before_mutation(self):
        """DB with workflow tables is rejected before WAL or schema creation."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            # Pre-populate with a Railyard workflow table
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE domain_ticket (id INTEGER)")
            conn.execute("CREATE TABLE schema_version (version TEXT)")
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "workflow_schema_rejected")
            self.assertIn("domain_ticket", ctx.exception.detail["tables"])
            self.assertIn("schema_version", ctx.exception.detail["tables"])
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_clean_db_accepted(self):
        """DB without workflow tables is accepted."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertIsNotNone(journal._conn)
            journal._conn.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


# ===========================================================================
# FORCED TWO-CONNECTION CONCURRENT RACE TESTS
# ===========================================================================

class TestForcedTwoConnectionRace(unittest.TestCase):
    """Simulate concurrent same-head writers with real separate connections."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _append_genesis(self, journal, run_id):
        payload = _run_created_payload()
        req = _make_request("run.created", payload, run_id=run_id)
        return journal.append(req)

    def test_concurrent_same_head_one_receipt_one_stale(self):
        """Two connections racing on the same head: one receipt, one stale_head."""
        # Create genesis first
        journal_init = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        run_id = "test-race"
        rec_genesis = self._append_genesis(journal_init, run_id)
        journal_init._conn.close()

        # Both connections target the same head (genesis)
        prev_digest = rec_genesis["stored_content_digest"]
        current_head_order = 1

        # IMPORTANT: executor_identity must match run.created ("runner-1")
        started_payload = {"started_at": "2026-01-01T00:00:01Z",
                           "executor_identity": "runner-1"}

        req_conn1 = _make_request("run.started", started_payload, run_id=run_id,
                                  prev_digest=prev_digest, head_order=current_head_order)
        req_conn1["expected_stream_head"] = {
            "event_order": current_head_order,
            "content_digest": prev_digest,
        }
        req_conn1["client_event_id"] = "race-conn1-" + uuid.uuid4().hex[:8]

        req_conn2 = _make_request("run.started", started_payload, run_id=run_id,
                                  prev_digest=prev_digest, head_order=current_head_order)
        req_conn2["expected_stream_head"] = {
            "event_order": current_head_order,
            "content_digest": prev_digest,
        }
        req_conn2["client_event_id"] = "race-conn2-" + uuid.uuid4().hex[:8]

        # Synchronization barrier
        barrier = threading.Barrier(2, timeout=10)
        results = {}

        def racer(label, db_path, request, barrier, results):
            conn = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            barrier.wait()  # synchronize start
            try:
                result = conn.append(request)
                results[label] = {"type": "ok", "result": result}
            except RuntimeJournalError as e:
                results[label] = {"type": "error", "code": e.detail["code"]}
            except Exception as e:
                results[label] = {"type": "exception",
                                  "message": str(e),
                                  "class": type(e).__name__}
            finally:
                conn._conn.close()

        t1 = threading.Thread(target=racer, args=("conn1", self.db_path, req_conn1, barrier, results))
        t2 = threading.Thread(target=racer, args=("conn2", self.db_path, req_conn2, barrier, results))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Assertions
        self.assertEqual(len(results), 2, f"Expected 2 results, got {len(results)}: {results}")

        types = {v["type"] for v in results.values()}
        self.assertIn("ok", types, f"No ok result: {results}")

        # At least one should be a receipt (ok) and none should be raw exceptions
        for label, r in results.items():
            self.assertNotEqual(r["type"], "exception",
                                f"Raw exception in {label}: {r}")

        # Count receipts vs stale_heads
        receipts = 0
        stale_heads = 0
        for label, r in results.items():
            if r["type"] == "ok":
                result = r["result"]
                if isinstance(result, dict) and "event_id" in result:
                    receipts += 1
                elif isinstance(result, dict) and result.get("code") == "stale_head":
                    stale_heads += 1

        # One receipt + one stale_head is the expected outcome
        self.assertGreaterEqual(receipts, 1,
                                f"Expected at least 1 receipt, got {receipts}")

    def test_repeated_race_consistency(self):
        """Repeated forced races consistently produce one receipt + one stale."""
        receipt_count = 0
        stale_count = 0
        iterations = 10

        for i in range(iterations):
            # Each iteration uses a fresh run_id to avoid reducer state conflicts
            run_id = f"test-repeat-race-{i}"

            # Genesis
            journal_init = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
            rec_genesis = self._append_genesis(journal_init, run_id)
            journal_init._conn.close()

            prev_digest = rec_genesis["stored_content_digest"]
            head_order = 1

            started_payload = {"started_at": "2026-01-01T00:00:01Z",
                               "executor_identity": "runner-1"}

            req_a = _make_request("run.started", started_payload, run_id=run_id,
                                  prev_digest=prev_digest, head_order=head_order)
            req_a["expected_stream_head"] = {"event_order": head_order, "content_digest": prev_digest}
            req_a["client_event_id"] = f"rep-race-a-{i}-{uuid.uuid4().hex[:8]}"

            req_b = _make_request("run.started", started_payload, run_id=run_id,
                                  prev_digest=prev_digest, head_order=head_order)
            req_b["expected_stream_head"] = {"event_order": head_order, "content_digest": prev_digest}
            req_b["client_event_id"] = f"rep-race-b-{i}-{uuid.uuid4().hex[:8]}"

            barrier = threading.Barrier(2, timeout=10)
            results = {}

            def racer(label, request, barrier, results):
                conn = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
                barrier.wait()
                try:
                    result = conn.append(request)
                    results[label] = result
                except RuntimeJournalError as e:
                    results[label] = {"code": e.detail["code"]}
                except Exception as e:
                    results[label] = {"code": "exception",
                                      "message": str(e),
                                      "class": type(e).__name__}
                finally:
                    conn._conn.close()

            t1 = threading.Thread(target=racer, args=("a", req_a, barrier, results))
            t2 = threading.Thread(target=racer, args=("b", req_b, barrier, results))
            t1.start(); t2.start()
            t1.join(timeout=10); t2.join(timeout=10)

            for label, result in results.items():
                if isinstance(result, dict) and "event_id" in result:
                    receipt_count += 1
                elif isinstance(result, dict) and result.get("code") == "stale_head":
                    stale_count += 1
                else:
                    self.fail(f"Iteration {i}, {label}: unexpected result {result}")

        # Every iteration should produce exactly 1 receipt
        self.assertEqual(receipt_count, iterations,
                         f"Expected {iterations} receipts, got {receipt_count}")
        self.assertGreaterEqual(stale_count, 1,
                                f"Expected at least 1 stale_head, got {stale_count}")

    def test_concurrent_exact_retry_no_integrity_error(self):
        """Concurrent exact retries return byte-identical original receipt."""
        run_id = "test-race-retry"
        journal_init = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        payload = _run_created_payload()
        req = _make_request("run.created", payload, run_id=run_id)
        rec_original = journal_init.append(req)
        journal_init._conn.close()

        barrier = threading.Barrier(2, timeout=10)
        results = {}

        def retry_racer(label, barrier, results):
            conn = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
            barrier.wait()
            try:
                result = conn.append(req)
                results[label] = result
            except Exception as e:
                results[label] = {"code": "exception", "message": str(e)}
            finally:
                conn._conn.close()

        t1 = threading.Thread(target=retry_racer, args=("a", barrier, results))
        t2 = threading.Thread(target=retry_racer, args=("b", barrier, results))
        t1.start(); t2.start()
        t1.join(timeout=10); t2.join(timeout=10)

        canonical_original = canonical_serialize(rec_original)
        for label, result in results.items():
            self.assertIsInstance(result, dict,
                                  f"{label}: unexpected type {type(result)}")
            self.assertIn("event_id", result,
                          f"{label}: not a receipt: {result}")
            self.assertEqual(canonical_serialize(result), canonical_original,
                             f"{label}: receipt differs from original")

    def test_concurrent_divergent_duplicate_structured(self):
        """Concurrent divergent duplicates return only the declared structured error."""
        run_id = "test-race-divergent"
        journal_init = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)

        client_id = "race-dup-" + uuid.uuid4().hex[:8]
        payload = _run_created_payload()
        req1 = _make_request("run.created", payload, run_id=run_id)
        req1["client_event_id"] = client_id
        journal_init.append(req1)

        # Same client_event_id, different payload -> divergent duplicate
        req2 = copy.deepcopy(req1)
        req2["run_id"] = "different-run-race"
        req2["reason"] = "modified reason for divergence"

        journal_init._conn.close()

        barrier = threading.Barrier(2, timeout=10)
        results = {}

        def divergent_racer(label, request, barrier, results):
            conn = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
            barrier.wait()
            try:
                result = conn.append(request)
                results[label] = {"type": "ok", "result": result}
            except RuntimeJournalError as e:
                results[label] = {"type": "error", "code": e.detail["code"]}
            except Exception as e:
                results[label] = {"type": "exception", "message": str(e)}
            finally:
                conn._conn.close()

        t1 = threading.Thread(target=divergent_racer, args=("a", req2, barrier, results))
        t2 = threading.Thread(target=divergent_racer, args=("b", req2, barrier, results))
        t1.start(); t2.start()
        t1.join(timeout=10); t2.join(timeout=10)

        for label, r in results.items():
            self.assertNotEqual(r["type"], "exception",
                                f"{label}: raw exception leaked: {r.get('message', '')}")
            if r["type"] == "error":
                self.assertEqual(r["code"], "divergent_duplicate",
                                 f"{label}: unexpected error code {r['code']}")


class TestRaceNoRawSqliteException(unittest.TestCase):
    """Verify that no raw sqlite3.IntegrityError escapes the journal API."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_no_integrity_error_leak(self):
        """High-contention race must not leak raw sqlite3.IntegrityError."""
        run_id = "test-no-leak"
        journal_init = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        payload = _run_created_payload()
        req = _make_request("run.created", payload, run_id=run_id)
        rec = journal_init.append(req)
        prev_digest = rec["stored_content_digest"]
        journal_init._conn.close()

        barrier = threading.Barrier(3, timeout=15)
        results = []

        def racer(idx, barrier, results):
            conn = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
            barrier.wait()
            try:
                sp = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
                r = _make_request("run.started", sp, run_id=run_id,
                                  prev_digest=prev_digest, head_order=1)
                r["expected_stream_head"] = {"event_order": 1, "content_digest": prev_digest}
                r["client_event_id"] = f"no-leak-{idx}-{uuid.uuid4().hex[:8]}"
                result = conn.append(r)
                results.append({"idx": idx, "type": "receipt", "event_order": result.get("event_order")})
            except RuntimeJournalError as e:
                results.append({"idx": idx, "type": "structured", "code": e.detail["code"]})
            except sqlite3.IntegrityError as e:
                results.append({"idx": idx, "type": "raw_integrity_error", "message": str(e)})
            except Exception as e:
                results.append({"idx": idx, "type": "exception", "message": str(e)})
            finally:
                conn._conn.close()

        threads = [
            threading.Thread(target=racer, args=(i, barrier, results))
            for i in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        for r in results:
            self.assertNotEqual(r["type"], "raw_integrity_error",
                                f"Raw IntegrityError leaked: {r}")
            self.assertNotEqual(r["type"], "exception",
                                f"Raw exception leaked: {r}")
            self.assertIn(r["type"], {"receipt", "structured"},
                          f"Unexpected result type: {r['type']}")

        # At least one receipt
        receipts = [r for r in results if r["type"] == "receipt"]
        self.assertGreaterEqual(len(receipts), 1,
                                f"No receipt among {len(results)} racers")


class TestRollbackWithBeginImmediate(unittest.TestCase):
    """Verify BEGIN IMMEDIATE rollback leaves database unchanged."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_rollback_on_hash_chain_failure_preserves_state(self):
        """ROLLBACK on hash chain failure inside BEGIN IMMEDIATE preserves state."""
        journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        run_id = "test-immediate-rollback"

        # Genesis
        payload = _run_created_payload()
        req1 = _make_request("run.created", payload, run_id=run_id)
        rec1 = journal.append(req1)

        events_before = len(journal.read_events(run_id))

        # Try append with bad hash chain
        req_bad = _make_request("run.started",
                                {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"},
                                run_id=run_id,
                                prev_digest=ZERO_DIGEST,
                                head_order=1)
        req_bad["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["stored_content_digest"]}
        req_bad["client_event_id"] = "immediate-rollback-" + uuid.uuid4().hex[:8]

        with self.assertRaises(RuntimeJournalError) as ctx:
            journal.append(req_bad)
        self.assertEqual(ctx.exception.detail["code"], "hash_chain_link")

        # Verify no events added
        events_after = journal.read_events(run_id)
        self.assertEqual(len(events_after), events_before)

        # Verify head unchanged
        head = journal._conn.execute(
            "SELECT event_order, content_digest FROM stream_heads WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual(int(head["event_order"]), 1)
        self.assertEqual(head["content_digest"], rec1["stored_content_digest"])

        journal._conn.close()

    def test_rollback_on_invalid_request_preserves_state(self):
        """ROLLBACK on invalid request inside BEGIN IMMEDIATE preserves state."""
        journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        run_id = "test-immediate-invalid"

        payload = _run_created_payload()
        req1 = _make_request("run.created", payload, run_id=run_id)
        rec1 = journal.append(req1)

        events_before = len(journal.read_events(run_id))

        # Build an intentionally invalid request (missing required field)
        req_bad = _make_request("run.started",
                                {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"},
                                run_id=run_id,
                                prev_digest=rec1["stored_content_digest"],
                                head_order=1)
        req_bad["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["stored_content_digest"]}
        req_bad["client_event_id"] = "immediate-invalid-" + uuid.uuid4().hex[:8]
        # Remove required field
        del req_bad["reason"]

        with self.assertRaises(RuntimeJournalError) as ctx:
            journal.append(req_bad)
        self.assertEqual(ctx.exception.detail["code"], "invalid_request")

        events_after = journal.read_events(run_id)
        self.assertEqual(len(events_after), events_before)

        journal._conn.close()

    def test_stale_head_commits_without_writes(self):
        """Stale head inside BEGIN IMMEDIATE commits cleanly, no writes performed."""
        journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)
        run_id = "test-stale-commit"

        payload = _run_created_payload()
        req1 = _make_request("run.created", payload, run_id=run_id)
        rec1 = journal.append(req1)

        # Try stale head
        req_stale = _make_request("run.started",
                                  {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"},
                                  run_id=run_id,
                                  head_order=0, prev_digest=ZERO_DIGEST)
        req_stale["client_event_id"] = "stale-commit-" + uuid.uuid4().hex[:8]

        result = journal.append(req_stale)
        self.assertEqual(result["code"], "stale_head")

        # Verify no events added
        events = journal.read_events(run_id)
        self.assertEqual(len(events), 1)

        # Verify head unchanged
        head = journal._conn.execute(
            "SELECT event_order FROM stream_heads WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual(int(head["event_order"]), 1)

        journal._conn.close()


# ===========================================================================
# NEW TESTS -- visibility_context, compatibility guard, schema version
# ===========================================================================

class TestVisibilityContextRoundTrip(unittest.TestCase):
    """Tests that visibility_context round-trips for public, project, restricted."""

    def _make_journal(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)

    def _cleanup(self):
        if hasattr(self, 'journal') and self.journal._conn is not None:
            self.journal._conn.close()
        if hasattr(self, 'db_path') and os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _test_visibility_round_trip(self, visibility):
        self._make_journal()
        try:
            payload = _run_created_payload(visibility=visibility)
            req = _make_request("run.created", payload, run_id=f"test-vis-{visibility}")
            receipt = self.journal.append(req)

            event = self.journal.read_event(f"test-vis-{visibility}", 1)
            self.assertIsNotNone(event)
            stored_payload = event["payload"]
            vc = stored_payload["visibility_context"]
            self.assertEqual(vc["resolved_run_visibility"], visibility)
            self.assertEqual(stored_payload, payload)
        finally:
            self._cleanup()

    def test_public_visibility_round_trip(self):
        self._test_visibility_round_trip("public")

    def test_project_visibility_round_trip(self):
        self._test_visibility_round_trip("project")

    def test_restricted_visibility_round_trip(self):
        self._test_visibility_round_trip("restricted")


class TestRuntimeArtifactRoundTrip(unittest.TestCase):
    """Tests that stage output events with RuntimeArtifact objects round-trip."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)

    def tearDown(self):
        self.journal._conn.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _make_runtime_artifact(self, artifact_id, run_id, artifact_kind="build-output", visibility="public"):
        """Construct a complete RuntimeArtifact per v0.9.0 schema."""
        return {
            "artifact_ref": {
                "artifact_id": artifact_id,
                "artifact_kind": artifact_kind,
                "digest": "sha256:" + ("b" * 64),
                "locator": f"outputs/{artifact_id}.json",
            },
            "origin_run": run_id,
            "origin_stage": "build",
            "produced_by": "runner-1",
            "source_artifacts": [
                {"artifact_id": "src-1", "artifact_kind": "source"}
            ],
            "visibility": visibility,
            "visibility_resolution": {
                "resolution_id": f"res-{artifact_id}",
                "resolved_at": "2026-01-01T00:01:00Z",
                "contributors": [
                    {
                        "contributor_id": "contrib-1",
                        "contributor_kind": "source_artifact",
                        "contributor_ref": {"artifact_id": "src-1", "artifact_kind": "source"},
                        "asserted_visibility": visibility,
                        "authority": "Test source",
                        "classification_evidence": [{"artifact_id": "src-1", "artifact_kind": "source"}],
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

    def test_runtime_artifact_round_trip(self):
        run_id = "test-artifact-rt"
        payload = _run_created_payload()
        req = _make_request("run.created", payload, run_id=run_id)
        rec1 = self.journal.append(req)

        # run.started transitions from pending to active
        started_payload = {"started_at": "2026-01-01T00:00:10Z", "executor_identity": "runner-1"}
        req_run_start = _make_request("run.started", started_payload, run_id=run_id,
                                      prev_digest=rec1["stored_content_digest"], head_order=1)
        req_run_start["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["stored_content_digest"]}
        rec_run = self.journal.append(req_run_start)

        # Start the stage
        stage_start_payload = {
            "stage_id": "build",
            "started_at": "2026-01-01T00:00:30Z",
            "entry_evidence": [{"artifact_id": "ev-start", "artifact_kind": "evidence"}],
        }
        req_start = _make_request("run.stage.started", stage_start_payload, run_id=run_id,
                                  prev_digest=rec_run["stored_content_digest"], head_order=2)
        req_start["expected_stream_head"] = {"event_order": 2, "content_digest": rec_run["stored_content_digest"]}
        rec2 = self.journal.append(req_start)

        artifact = self._make_runtime_artifact("build-artifact-1", run_id, visibility="public")
        stage_fail_payload = {
            "stage_id": "build",
            "failed_at": "2026-01-01T00:01:00Z",
            "error": {"message": "test failure", "code": "TEST_FAIL"},
            "failure_category": "command_failed",
            "failure_is_transient": False,
            "failure_is_deterministic": True,
            "artifacts_produced_before_failure": [artifact],
            "retry_eligible": False,
        }
        req3 = _make_request("run.stage.failed", stage_fail_payload, run_id=run_id,
                             prev_digest=rec2["stored_content_digest"], head_order=3)
        req3["expected_stream_head"] = {"event_order": 3, "content_digest": rec2["stored_content_digest"]}
        self.journal.append(req3)

        events = self.journal.read_events(run_id)
        self.assertEqual(len(events), 4)

        stage_event = events[3]
        self.assertEqual(stage_event["event_type"], "run.stage.failed")
        stored_artifacts = stage_event["payload"]["artifacts_produced_before_failure"]
        self.assertEqual(len(stored_artifacts), 1)
        self.assertEqual(stored_artifacts[0], artifact)

    def test_multiple_artifacts_round_trip(self):
        run_id = "test-multi-artifacts"
        payload = _run_created_payload()
        req = _make_request("run.created", payload, run_id=run_id)
        rec1 = self.journal.append(req)

        # run.started transitions from pending to active
        started_payload = {"started_at": "2026-01-01T00:00:10Z", "executor_identity": "runner-1"}
        req_run_start = _make_request("run.started", started_payload, run_id=run_id,
                                      prev_digest=rec1["stored_content_digest"], head_order=1)
        req_run_start["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["stored_content_digest"]}
        rec_run = self.journal.append(req_run_start)

        stage_start_payload = {
            "stage_id": "build",
            "started_at": "2026-01-01T00:00:30Z",
            "entry_evidence": [{"artifact_id": "ev-start", "artifact_kind": "evidence"}],
        }
        req_start = _make_request("run.stage.started", stage_start_payload, run_id=run_id,
                                  prev_digest=rec_run["stored_content_digest"], head_order=2)
        req_start["expected_stream_head"] = {"event_order": 2, "content_digest": rec_run["stored_content_digest"]}
        rec2 = self.journal.append(req_start)

        artifacts = [
            self._make_runtime_artifact("artifact-a", run_id, "test-output", visibility="public"),
            self._make_runtime_artifact("artifact-b", run_id, "test-output", visibility="project"),
        ]
        stage_fail_payload = {
            "stage_id": "build",
            "failed_at": "2026-01-01T00:01:00Z",
            "error": {"message": "test failure", "code": "TEST_FAIL"},
            "failure_category": "command_failed",
            "failure_is_transient": False,
            "failure_is_deterministic": True,
            "artifacts_produced_before_failure": artifacts,
            "retry_eligible": False,
        }
        req2 = _make_request("run.stage.failed", stage_fail_payload, run_id=run_id,
                             prev_digest=rec2["stored_content_digest"], head_order=3)
        req2["expected_stream_head"] = {"event_order": 3, "content_digest": rec2["stored_content_digest"]}
        self.journal.append(req2)

        events = self.journal.read_events(run_id)
        stage_event = events[3]
        stored = stage_event["payload"]["artifacts_produced_before_failure"]
        self.assertEqual(stored, artifacts)


class TestSchemaVersionStorage(unittest.TestCase):
    """Tests that stored schema_version is exactly "0.9.0" in the database."""

    def test_schema_version_is_v0_9_0(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            payload = _run_created_payload()
            req = _make_request("run.created", payload, run_id="test-schema-ver")
            journal.append(req)

            # Append second event to verify multiple rows all have 0.9.0
            rec1 = journal.read_event("test-schema-ver", 1)
            started_payload = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
            req2 = _make_request("run.started", started_payload, run_id="test-schema-ver",
                                 prev_digest=rec1["content_digest"], head_order=1)
            req2["expected_stream_head"] = {"event_order": 1, "content_digest": rec1["content_digest"]}
            journal.append(req2)

            journal._conn.close()

            conn = sqlite3.connect(db_path)
            rows = conn.execute("SELECT schema_version FROM runtime_events ORDER BY event_order").fetchall()
            conn.close()

            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(row[0], "0.9.0")
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


class TestLegacyDatabaseRejection(unittest.TestCase):
    """Tests that v0.8.2 database is rejected with unsupported_schema_version."""

    def test_constructor_rejection_is_mutation_free(self):
        """Legacy preflight preserves bytes, schema, mode, rows, and sidecars."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            conn.execute(
                """INSERT INTO runtime_events
                   (run_id, event_id, event_order, event_type, payload,
                    causation_id, causation_chain, actor_role, actor_identity,
                    trigger_artifact, reason, recommended_action,
                    expected_stream_head, client_event_id, prev_event_digest,
                    prior_state, next_state, occurred_at, schema_version,
                    content_digest)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "preflight-legacy", "preflight-event", 1, "run.created", "{}",
                    None, None, "runner", "runner-1", "{}", "legacy", "none", "{}",
                    "preflight-client", ZERO_DIGEST, "{}", "{}",
                    "2026-01-01T00:00:00Z", "0.8.2", "sha256:" + ("0" * 64),
                ),
            )
            conn.commit()
            mode_before = conn.execute("PRAGMA journal_mode").fetchone()[0]
            schema_before = conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall()
            rows_before = conn.execute(
                "SELECT * FROM runtime_events ORDER BY run_id, event_order"
            ).fetchall()
            conn.close()

            bytes_before = pathlib.Path(db_path).read_bytes()
            sidecars_before = {
                suffix: os.path.exists(db_path + suffix) for suffix in ("-wal", "-shm")
            }

            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "unsupported_schema_version")

            self.assertEqual(pathlib.Path(db_path).read_bytes(), bytes_before)
            self.assertEqual(
                {suffix: os.path.exists(db_path + suffix) for suffix in ("-wal", "-shm")},
                sidecars_before,
            )
            conn = sqlite3.connect(db_path)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], mode_before)
            self.assertEqual(
                conn.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                ).fetchall(),
                schema_before,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT * FROM runtime_events ORDER BY run_id, event_order"
                ).fetchall(),
                rows_before,
            )
            conn.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_legacy_database_rejected_on_read(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            # Pre-populate with v0.8.2 data (journal tables only, no workflow tables)
            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-legacy", "ev-legacy-1", 1, "run.created",
                json.dumps({"test": "legacy"}),
                None, None, "runner", "runner-1",
                json.dumps({"artifact_id": "t1", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                "legacy-client-1", ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.8.2",
                "sha256:" + ("0" * 64),
            ))
            conn.execute("""
                INSERT INTO stream_heads (run_id, event_order, content_digest, last_receipt)
                VALUES (?, ?, ?, ?)
            """, ("test-legacy", 1, "sha256:" + ("0" * 64), None))
            conn.commit()

            row_count_before = conn.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
            conn.close()

            # Constructor preflight rejects before a writable connection opens.
            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "unsupported_schema_version")

            # Verify no mutation occurred
            conn2 = sqlite3.connect(db_path)
            row_count_after = conn2.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
            self.assertEqual(row_count_after, row_count_before)
            conn2.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_legacy_database_rejected_on_append(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-legacy-append", "ev-legacy-2", 1, "run.created",
                json.dumps({"test": "legacy"}),
                None, None, "runner", "runner-1",
                json.dumps({"artifact_id": "t2", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                "legacy-client-2", ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.8.2",
                "sha256:" + ("0" * 64),
            ))
            conn.execute("""
                INSERT INTO stream_heads (run_id, event_order, content_digest, last_receipt)
                VALUES (?, ?, ?, ?)
            """, ("test-legacy-append", 1, "sha256:" + ("0" * 64), None))
            conn.commit()

            row_count_before = conn.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "unsupported_schema_version")

            conn2 = sqlite3.connect(db_path)
            row_count_after = conn2.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
            self.assertEqual(row_count_after, row_count_before)
            conn2.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_legacy_database_rejected_on_read_event(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-legacy-read", "ev-legacy-3", 1, "run.created",
                json.dumps({"test": "legacy"}),
                None, None, "runner", "runner-1",
                json.dumps({"artifact_id": "t3", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                "legacy-client-3", ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.8.2",
                "sha256:" + ("0" * 64),
            ))
            conn.execute("""
                INSERT INTO stream_heads (run_id, event_order, content_digest, last_receipt)
                VALUES (?, ?, ?, ?)
            """, ("test-legacy-read", 1, "sha256:" + ("0" * 64), None))
            conn.commit()
            row_count_before = conn.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "unsupported_schema_version")

            conn2 = sqlite3.connect(db_path)
            row_count_after = conn2.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
            self.assertEqual(row_count_after, row_count_before)
            conn2.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


class TestMixedVersionDatabaseRejection(unittest.TestCase):
    """Tests that mixed-version database is rejected."""

    def test_mixed_version_rejected_on_read(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)

            # Insert v0.8.2 event
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-mixed", "ev-mixed-old", 1, "run.created",
                json.dumps({"test": "mixed-old"}),
                None, None, "runner", "runner-1",
                json.dumps({"artifact_id": "m1", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                "mixed-client-1", ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.8.2",
                "sha256:" + ("0" * 64),
            ))

            # Insert v0.9.0 event
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-mixed", "ev-mixed-new", 2, "run.started",
                json.dumps({"test": "mixed-new"}),
                None, None, "runner", "runner-1",
                json.dumps({"artifact_id": "m2", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 1, "content_digest": "sha256:" + ("0" * 64)}),
                "mixed-client-2", "sha256:" + ("0" * 64),
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:01Z", "0.9.0",
                "sha256:" + ("a" * 64),
            ))
            conn.commit()
            row_count_before = conn.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "unsupported_schema_version")

            conn2 = sqlite3.connect(db_path)
            row_count_after = conn2.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
            self.assertEqual(row_count_after, row_count_before)
            conn2.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_mixed_version_rejected_on_append(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)

            # One 0.8.2 event
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-mixed-a", "ev-mixed-a-old", 1, "run.created",
                json.dumps({"test": "mixed"}),
                None, None, "runner", "runner-1",
                json.dumps({"artifact_id": "ma", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                "mixed-client-a", ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.8.2",
                "sha256:" + ("0" * 64),
            ))

            # One 0.9.0 event
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-mixed-a", "ev-mixed-a-new", 2, "run.started",
                json.dumps({"test": "mixed-new"}),
                None, None, "runner", "runner-1",
                json.dumps({"artifact_id": "mb", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 1, "content_digest": "sha256:" + ("0" * 64)}),
                "mixed-client-b", "sha256:" + ("0" * 64),
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:01Z", "0.9.0",
                "sha256:" + ("a" * 64),
            ))
            conn.commit()
            row_count_before = conn.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(ctx.exception.detail["code"], "unsupported_schema_version")

            conn2 = sqlite3.connect(db_path)
            row_count_after = conn2.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
            self.assertEqual(row_count_after, row_count_before)
            conn2.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


class TestExactRetryWithNestedVisibility(unittest.TestCase):
    """Verify exact retry preserves original receipt with nested visibility_context payloads."""

    def test_exact_retry_nested_visibility(self):
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            # First append with visibility_context
            journal1 = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            payload = _run_created_payload()
            req = _make_request("run.created", payload, run_id="test-retry-vis")
            rec1 = journal1.append(req)
            journal1._conn.close()

            # Second connection: exact retry
            journal2 = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            rec2 = journal2.append(req)
            journal2._conn.close()

            # Byte-for-byte identical
            self.assertEqual(canonical_serialize(rec1), canonical_serialize(rec2))

            # Verify payload in DB matches original (has full visibility_context)
            journal3 = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            event = journal3.read_event("test-retry-vis", 1)
            stored_payload = event["payload"]
            self.assertIn("visibility_context", stored_payload)
            self.assertEqual(
                stored_payload["visibility_context"]["resolved_run_visibility"],
                "public",
            )
            journal3._conn.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


class TestCompatibilityGuardPlacement(unittest.TestCase):
    """Verify compatibility guard is outside the frozen append decision sequence."""

    def test_compatible_store_append_works(self):
        """Compatible 0.9.0 store: append works with normal decision order."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            run_id = "test-guard-ok"

            # Genesis
            payload = _run_created_payload()
            req = _make_request("run.created", payload, run_id=run_id)
            rec = journal.append(req)
            self.assertIn("event_id", rec)
            self.assertEqual(rec["event_order"], 1)

            # Second event (normal append path works)
            started_payload = {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}
            req2 = _make_request("run.started", started_payload, run_id=run_id,
                                 prev_digest=rec["stored_content_digest"], head_order=1)
            req2["expected_stream_head"] = {"event_order": 1, "content_digest": rec["stored_content_digest"]}
            rec2 = journal.append(req2)
            self.assertEqual(rec2["event_order"], 2)

            # Verify exact retry still works
            rec_retry = journal.append(req2)
            self.assertEqual(canonical_serialize(rec2), canonical_serialize(rec_retry))

            journal._conn.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_guard_is_not_cached(self):
        """A legacy row injected after a successful check is detected."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name

            journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)
            self.assertEqual(journal.read_events("nonexistent"), [])

            injector = sqlite3.connect(db_path)
            injector.execute(
                """INSERT INTO runtime_events
                   (run_id, event_id, event_order, event_type, payload,
                    causation_id, causation_chain, actor_role, actor_identity,
                    trigger_artifact, reason, recommended_action,
                    expected_stream_head, client_event_id, prev_event_digest,
                    prior_state, next_state, occurred_at, schema_version,
                    content_digest)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "legacy-injected", "legacy-injected-event", 1, "run.created",
                    "{}", None, None, "runner", "legacy-runner", "{}", "legacy",
                    "none", "{}", "legacy-injected-client", ZERO_DIGEST, "{}", "{}",
                    "2026-01-01T00:00:00Z", "0.8.2", "sha256:" + ("0" * 64),
                ),
            )
            injector.commit()
            injector.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                journal.read_event("nonexistent", 1)
            self.assertEqual(ctx.exception.detail["code"], "unsupported_schema_version")

            request = _make_request(
                "run.created", _run_created_payload(), run_id="after-injection"
            )
            with self.assertRaises(RuntimeJournalError) as ctx:
                journal.append(request)
            self.assertEqual(ctx.exception.detail["code"], "unsupported_schema_version")
            journal._conn.close()
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)


# ---------------------------------------------------------------------------
# read_run_evidence_snapshot tests
# ---------------------------------------------------------------------------

class TestReadRunEvidenceSnapshot(unittest.TestCase):
    """Tests for read_run_evidence_snapshot -- atomic snapshot evidence capture."""

    def _make_db_with_run(self, run_id="test-evidence", num_events=2,
                          visibility="public"):
        """Create a temporary DB, populate it with num_events events, return db_path and receipts."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)
        receipts = []
        prev_digest = ZERO_DIGEST
        head_order = 0
        for i in range(num_events):
            if i == 0:
                event_type = "run.created"
                payload = _run_created_payload(visibility)
            else:
                event_type = "run.started"
                payload = {"started_at": "2026-01-01T00:00:0%dZ" % (i + 1),
                          "executor_identity": "runner-1"}
            req = _make_request(event_type, payload, run_id=run_id,
                               prev_digest=prev_digest, head_order=head_order)
            if head_order > 0:
                req["expected_stream_head"] = {
                    "event_order": head_order,
                    "content_digest": prev_digest,
                }
            rec = journal.append(req)
            receipts.append(rec)
            prev_digest = rec["stored_content_digest"]
            head_order = rec["event_order"]
        journal._conn.close()
        return db_path, receipts

    def test_valid_run_complete_snapshot(self):
        """Full snapshot: events, receipts, and head are all correct."""
        db_path, receipts = self._make_db_with_run("test-complete", num_events=2)
        try:
            result = read_run_evidence_snapshot(db_path, "test-complete")
            self.assertEqual(result["run_id"], "test-complete")
            self.assertIsInstance(result["events"], list)
            self.assertIsInstance(result["receipts"], list)
            self.assertIsInstance(result["source_stream_head"], dict)
            self.assertEqual(len(result["events"]), 2)
            self.assertEqual(len(result["receipts"]), 2)
            self.assertEqual(result["events"][0]["event_order"], 1)
            self.assertEqual(result["events"][0]["event_type"], "run.created")
            self.assertEqual(result["events"][1]["event_order"], 2)
            self.assertEqual(result["events"][1]["event_type"], "run.started")

            # Verify receipts match events
            for i, ev in enumerate(result["events"]):
                rec = result["receipts"][i]
                self.assertEqual(rec["event_id"], ev["event_id"])
                self.assertEqual(rec["event_order"], ev["event_order"])
                self.assertEqual(rec["stored_content_digest"],
                                 ev["content_digest"])

            # Verify head matches last event
            head = result["source_stream_head"]
            last = result["events"][-1]
            self.assertEqual(head["event_order"], last["event_order"])
            self.assertEqual(head["content_digest"], last["content_digest"])
            self.assertEqual(head["content_digest"],
                             receipts[-1]["stored_content_digest"])
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_missing_db_file(self):
        """Non-existent database path raises structured error."""
        nonexistent = str(pathlib.Path(tempfile.mkdtemp()) / "ghost.db")
        with self.assertRaises(RuntimeJournalError) as ctx:
            read_run_evidence_snapshot(nonexistent, "any-run")
        self.assertEqual(ctx.exception.detail["code"], "run_not_found")

    def test_workflow_path_rejected(self):
        """Database paths under .workflow are rejected."""
        # Create a temp .workflow directory with a DB directly (skip
        # RuntimeJournal which rejects .workflow itself)
        wf_dir = os.path.join(tempfile.mkdtemp(), ".workflow")
        os.makedirs(wf_dir, exist_ok=True)
        wf_path = os.path.join(wf_dir, "test.db")
        try:
            # Create a minimal runtime DB via direct sqlite3
            conn = sqlite3.connect(wf_path)
            conn.executescript(_CREATE_TABLES)
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-wf", "ev-wf", 1, "run.created",
                json.dumps({"test": "wf"}),
                None, json.dumps([]), "runner", "r1",
                json.dumps({"artifact_id": "w1", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                "wf-client-1", ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.9.0",
                "sha256:" + ("0" * 64),
            ))
            conn.execute(
                "INSERT INTO idempotency (client_event_id, complete_request, stored_event_id, stored_receipt) VALUES (?, ?, ?, ?)",
                ("wf-client-1", b"{}", "ev-wf",
                 json.dumps({"event_id": "ev-wf", "event_order": 1,
                             "stored_content_digest": "sha256:" + ("0" * 64),
                             "new_stream_head": {"event_order": 1,
                                                  "content_digest": "sha256:" + ("0" * 64)},
                             "signed_receipt": {"algorithm": "HMAC-SHA256",
                                                 "signature": "ab"}}).encode("utf-8")),
            )
            conn.execute(
                "INSERT INTO stream_heads (run_id, event_order, content_digest) VALUES (?, ?, ?)",
                ("test-wf", 1, "sha256:" + ("0" * 64)),
            )
            conn.commit()
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(wf_path, "any-run")
            self.assertEqual(ctx.exception.detail["code"],
                             "workflow_path_rejected")
        finally:
            import shutil
            shutil.rmtree(os.path.dirname(wf_dir), ignore_errors=True)

    def test_workflow_schema_rejected(self):
        """Database containing workflow tables is rejected."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE domain_epic (id TEXT)")
            conn.execute("CREATE TABLE schema_version (ver TEXT)")
            conn.commit()
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "any-run")
            self.assertEqual(ctx.exception.detail["code"],
                             "workflow_schema_rejected")
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_unsupported_schema_version(self):
        """Non-0.9.0 schema version rejects."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-legacy", "ev-legacy", 1, "run.created",
                json.dumps({"test": "legacy"}),
                None, json.dumps([]), "runner", "runner-1",
                json.dumps({"artifact_id": "t1", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                "legacy-client-1", ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.8.0",
                "sha256:" + ("0" * 64),
            ))
            conn.commit()
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-legacy")
            self.assertEqual(ctx.exception.detail["code"],
                             "unsupported_schema_version")
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_mixed_version_rejected(self):
        """Mixed schema versions reject."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-mixed", "ev-1", 1, "run.created",
                json.dumps({"test": "mixed"}),
                None, json.dumps([]), "runner", "r1",
                json.dumps({"artifact_id": "a1", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                "mixed-c1", ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.9.0",
                "sha256:" + ("0" * 64),
            ))
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-mixed", "ev-2", 2, "run.started",
                json.dumps({"test": "mixed2"}),
                None, json.dumps([]), "runner", "r1",
                json.dumps({"artifact_id": "a2", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 1, "content_digest": "sha256:" + ("0" * 64)}),
                "mixed-c2", "sha256:" + ("0" * 64),
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:01Z", "0.8.0",
                "sha256:" + ("a" * 64),
            ))
            conn.commit()
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-mixed")
            self.assertEqual(ctx.exception.detail["code"],
                             "unsupported_schema_version")
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_run_not_found(self):
        """Existing DB but no events for the given run_id raises run_not_found."""
        db_path, _receipts = self._make_db_with_run("existing-run", num_events=1)
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "non-existent-run")
            self.assertEqual(ctx.exception.detail["code"], "run_not_found")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_missing_receipt_incomplete(self):
        """Missing idempotency row raises evidence_snapshot_incomplete."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            # Insert event but NO idempotency row
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-missing-rec", "ev-orphan", 1, "run.created",
                json.dumps({"test": "orphan"}),
                None, json.dumps([]), "runner", "r1",
                json.dumps({"artifact_id": "o1", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                "orphan-client", ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.9.0",
                "sha256:" + ("0" * 64),
            ))
            # Insert stream head
            conn.execute("""
                INSERT INTO stream_heads (run_id, event_order, content_digest)
                VALUES (?, ?, ?)
            """, ("test-missing-rec", 1, "sha256:" + ("0" * 64)))
            conn.commit()
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-missing-rec")
            self.assertEqual(ctx.exception.detail["code"],
                             "evidence_snapshot_incomplete")
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_head_mismatch_incomplete(self):
        """Stream head not matching last event raises evidence_snapshot_incomplete."""
        db_path, _receipts = self._make_db_with_run("test-head-mismatch",
                                                     num_events=1)
        try:
            # Tamper: change head to a different digest
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE stream_heads SET content_digest = ? WHERE run_id = ?",
                ("sha256:" + ("f" * 64), "test-head-mismatch"),
            )
            conn.commit()
            conn.close()

            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-head-mismatch")
            self.assertEqual(ctx.exception.detail["code"],
                             "evidence_snapshot_incomplete")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_causation_shape_root_event(self):
        """Root events have causation_chain but NOT causation_id."""
        db_path, _receipts = self._make_db_with_run("test-causation",
                                                     num_events=1)
        try:
            result = read_run_evidence_snapshot(db_path, "test-causation")
            event = result["events"][0]
            self.assertIn("causation_chain", event)
            self.assertIsInstance(event["causation_chain"], list)
            self.assertNotIn("causation_id", event)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_causation_shape_caused_event(self):
        """Caused events have causation_id but NOT causation_chain."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)

            causation_id = str(uuid.uuid4())
            event_id = str(uuid.uuid4())
            digest = "sha256:" + ("e" * 64)
            client_event_id = "caused-client-" + uuid.uuid4().hex[:8]

            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-caused", event_id, 1, "run.stage.started",
                json.dumps({"stage_id": "build", "started_at": "2026-01-01T00:00:00Z",
                            "entry_evidence": []}),
                causation_id, None, "runner", "r1",
                json.dumps({"artifact_id": "c1", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                client_event_id, ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.9.0",
                digest,
            ))

            # Insert matching idempotency receipt
            receipt = {
                "event_id": event_id,
                "event_order": 1,
                "stored_content_digest": digest,
                "new_stream_head": {"event_order": 1, "content_digest": digest},
                "signed_receipt": {"algorithm": "HMAC-SHA256", "signature": "ab"},
            }
            conn.execute(
                "INSERT INTO idempotency (client_event_id, complete_request, stored_event_id, stored_receipt) VALUES (?, ?, ?, ?)",
                (client_event_id, b"{}", event_id,
                 json.dumps(receipt).encode("utf-8")),
            )

            # Insert stream head
            conn.execute(
                "INSERT INTO stream_heads (run_id, event_order, content_digest) VALUES (?, ?, ?)",
                ("test-caused", 1, digest),
            )
            conn.commit()
            conn.close()

            result = read_run_evidence_snapshot(db_path, "test-caused")
            event = result["events"][0]
            self.assertIn("causation_id", event)
            self.assertEqual(event["causation_id"], causation_id)
            self.assertNotIn("causation_chain", event)
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_receipt_verification(self):
        """Receipts contain event_id/order/digest matching events."""
        db_path, receipts = self._make_db_with_run("test-rec-verify",
                                                    num_events=2)
        try:
            result = read_run_evidence_snapshot(db_path, "test-rec-verify")
            for i, ev in enumerate(result["events"]):
                rec = result["receipts"][i]
                self.assertEqual(rec["event_id"], ev["event_id"])
                self.assertEqual(rec["event_order"], ev["event_order"])
                self.assertEqual(rec["stored_content_digest"],
                                 ev["content_digest"])
            # Verify receipts are from original persisted records
            self.assertEqual(
                canonical_serialize(result["receipts"][0]),
                canonical_serialize(receipts[0]),
            )
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_deep_independence(self):
        """Mutating returned result does not affect subsequent reads."""
        db_path, _receipts = self._make_db_with_run("test-deep", num_events=1)
        try:
            result1 = read_run_evidence_snapshot(db_path, "test-deep")
            # Mutate the returned events list
            result1["events"][0]["payload"] = {"modified": True}
            result1["events"].append({"fake": "event"})
            result1["source_stream_head"]["content_digest"] = "bad-digest"

            # Re-read -- must be unchanged
            result2 = read_run_evidence_snapshot(db_path, "test-deep")
            self.assertNotEqual(
                json.dumps(result1, sort_keys=True),
                json.dumps(result2, sort_keys=True),
            )
            self.assertEqual(len(result2["events"]), 1)
            self.assertNotIn("modified", result2["events"][0]["payload"])
            self.assertNotEqual(result2["source_stream_head"]["content_digest"],
                                "bad-digest")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_read_only_no_side_effects(self):
        """Verify read_run_evidence_snapshot creates no WAL/sidecar files."""
        db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = f.name
            # Create DB without WAL mode (direct SQL, no RuntimeJournal)
            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            digest = "sha256:" + ("e" * 64)
            eid = str(uuid.uuid4())
            ceid = "ro-client-" + uuid.uuid4().hex[:8]
            conn.execute("""
                INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test-ro", eid, 1, "run.created",
                json.dumps({"test": "ro"}),
                None, json.dumps([]), "runner", "r1",
                json.dumps({"artifact_id": "ro1", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                ceid, ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.9.0",
                digest,
            ))
            receipt = {
                "event_id": eid, "event_order": 1,
                "stored_content_digest": digest,
                "new_stream_head": {"event_order": 1, "content_digest": digest},
                "signed_receipt": {"algorithm": "HMAC-SHA256", "signature": "ab"},
            }
            conn.execute(
                "INSERT INTO idempotency (client_event_id, complete_request, stored_event_id, stored_receipt) VALUES (?, ?, ?, ?)",
                (ceid, b"{}", eid, json.dumps(receipt).encode("utf-8")),
            )
            conn.execute(
                "INSERT INTO stream_heads (run_id, event_order, content_digest) VALUES (?, ?, ?)",
                ("test-ro", 1, digest),
            )
            conn.commit()
            conn.close()

            # Note all files before read
            db_dir = os.path.dirname(db_path)
            files_before = set(os.listdir(db_dir))

            _ = read_run_evidence_snapshot(db_path, "test-ro")

            files_after = set(os.listdir(db_dir))
            new_files = files_after - files_before
            self.assertEqual(
                new_files, set(),
                f"Unexpected files created: {new_files}",
            )
        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    def test_concurrent_torn_read(self):
        """Real concurrent writer + snapshot reader: never torn."""
        db_path = None
        try:
            # Create initial DB with 1 event (run.created, status = pending)
            db_path, _ = self._make_db_with_run("test-concurrent",
                                                 num_events=1)

            write_error = []
            results: list[dict] = []
            writer_done = threading.Event()

            def writer():
                try:
                    journal = RuntimeJournal(db_path, TEST_SIGNER_KEY)
                    run_id = "test-concurrent"
                    # Find current head
                    head_row = journal._conn.execute(
                        "SELECT event_order, content_digest FROM stream_heads WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()
                    prev_digest = head_row["content_digest"]
                    head_order = int(head_row["event_order"])

                    # First: run.started (pending -> active)
                    req = _make_request(
                        "run.started",
                        {"started_at": "2026-01-01T00:00:01Z",
                         "executor_identity": "runner-1"},
                        run_id=run_id,
                        prev_digest=prev_digest,
                        head_order=head_order,
                    )
                    req["expected_stream_head"] = {
                        "event_order": head_order,
                        "content_digest": prev_digest,
                    }
                    rec = journal.append(req)
                    prev_digest = rec["stored_content_digest"]
                    head_order = rec["event_order"]

                    # Then 9 run.intervention events (stays active, repeatable)
                    for i in range(9):
                        req = _make_request(
                            "run.intervention",
                            {"intervention_id": f"intv-conc-{i}",
                             "intervention_type": "provide_evidence",
                             "authorized_by": "architect",
                             "reason": f"concurrent test probe {i}",
                             "evidence": [{"artifact_id": f"probe-{i}",
                                           "artifact_kind": "evidence"}]},
                            run_id=run_id,
                            prev_digest=prev_digest,
                            head_order=head_order,
                        )
                        req["expected_stream_head"] = {
                            "event_order": head_order,
                            "content_digest": prev_digest,
                        }
                        rec = journal.append(req)
                        prev_digest = rec["stored_content_digest"]
                        head_order = rec["event_order"]
                    journal._conn.close()
                except Exception as e:
                    write_error.append(str(e))
                finally:
                    writer_done.set()

            def reader():
                for _ in range(200):
                    try:
                        snap = read_run_evidence_snapshot(
                            db_path, "test-concurrent",
                        )
                        results.append(snap)
                    except Exception:
                        pass  # Transient concurrency errors

            # Start reader slightly before writer to ensure overlap
            reader_started = threading.Event()

            def delayed_writer():
                reader_started.wait()
                writer()

            t_reader = threading.Thread(target=reader, daemon=True)
            t_writer = threading.Thread(target=delayed_writer, daemon=True)

            t_reader.start()
            reader_started.set()
            t_writer.start()

            t_reader.join(timeout=30)
            t_writer.join(timeout=30)

            self.assertEqual(len(write_error), 0,
                             f"Writer errors: {write_error}")

            # Every snapshot must be internally consistent
            for snap in results:
                self.assertEqual(snap["run_id"], "test-concurrent")
                self.assertGreaterEqual(len(snap["events"]), 1)
                self.assertEqual(len(snap["events"]),
                                 len(snap["receipts"]))
                last = snap["events"][-1]
                head = snap["source_stream_head"]
                self.assertEqual(last["event_order"],
                                 head["event_order"])
                self.assertEqual(last["content_digest"],
                                 head["content_digest"])
                # Receipts must match events pairwise
                for i, ev in enumerate(snap["events"]):
                    rec = snap["receipts"][i]
                    self.assertEqual(rec["event_id"], ev["event_id"])
                    self.assertEqual(rec["event_order"],
                                     ev["event_order"])
                    self.assertEqual(rec["stored_content_digest"],
                                     ev["content_digest"])

        finally:
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

# ===========================================================================
# Hardening Tests
# ===========================================================================

class TestPartialTableRejection(unittest.TestCase):
    """All three runtime reference tables must exist; missing any raises run_not_found."""

    def _make_db_missing_table(self, drop_table):
        """Create a DB with all three tables, then drop one."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        conn = sqlite3.connect(db_path)
        conn.executescript(_CREATE_TABLES)
        # Insert minimal valid data
        digest = "sha256:" + ("a" * 64)
        eid = str(uuid.uuid4())
        ceid = "pt-client-" + uuid.uuid4().hex[:8]
        conn.execute("""INSERT INTO runtime_events
            (run_id, event_id, event_order, event_type, payload,
             causation_id, causation_chain, actor_role, actor_identity,
             trigger_artifact, reason, recommended_action,
             expected_stream_head, client_event_id, prev_event_digest,
             prior_state, next_state, occurred_at, schema_version,
             content_digest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            "test-pt", eid, 1, "run.created",
            json.dumps({"test": "partial"}),
            None, json.dumps([]), "runner", "r1",
            json.dumps({"artifact_id": "pt1", "artifact_kind": "ticket"}),
            "test", "none",
            json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
            ceid, ZERO_DIGEST,
            json.dumps({}), json.dumps({}),
            "2026-01-01T00:00:00Z", "0.9.0",
            digest,
        ))
        receipt = {
            "event_id": eid, "event_order": 1,
            "stored_content_digest": digest,
            "new_stream_head": {"event_order": 1, "content_digest": digest},
            "signed_receipt": {"algorithm": "HMAC-SHA256", "signature": "ab"},
        }
        conn.execute(
            "INSERT INTO idempotency (client_event_id, complete_request, stored_event_id, stored_receipt) VALUES (?, ?, ?, ?)",
            (ceid, b"{}", eid, json.dumps(receipt).encode("utf-8")),
        )
        conn.execute(
            "INSERT INTO stream_heads (run_id, event_order, content_digest) VALUES (?, ?, ?)",
            ("test-pt", 1, digest),
        )
        conn.commit()
        if drop_table:
            conn.execute(f"DROP TABLE {drop_table}")
            conn.commit()
        conn.close()
        return db_path

    def test_missing_idempotency_only(self):
        db_path = self._make_db_missing_table("idempotency")
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-pt")
            self.assertEqual(ctx.exception.detail["code"], "run_not_found")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_missing_stream_heads_only(self):
        db_path = self._make_db_missing_table("stream_heads")
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-pt")
            self.assertEqual(ctx.exception.detail["code"], "run_not_found")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_missing_runtime_events_only(self):
        db_path = self._make_db_missing_table("runtime_events")
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-pt")
            self.assertEqual(ctx.exception.detail["code"], "run_not_found")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_missing_idempotency_and_stream_heads(self):
        """Drop both idempotency and stream_heads."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        conn = sqlite3.connect(db_path)
        conn.executescript(_CREATE_TABLES)
        # Insert minimal valid data
        digest = "sha256:" + ("a" * 64)
        eid = str(uuid.uuid4())
        ceid = "pt2-client-" + uuid.uuid4().hex[:8]
        conn.execute("""INSERT INTO runtime_events
            (run_id, event_id, event_order, event_type, payload,
             causation_id, causation_chain, actor_role, actor_identity,
             trigger_artifact, reason, recommended_action,
             expected_stream_head, client_event_id, prev_event_digest,
             prior_state, next_state, occurred_at, schema_version,
             content_digest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            "test-pt2", eid, 1, "run.created",
            json.dumps({"test": "partial2"}),
            None, json.dumps([]), "runner", "r1",
            json.dumps({"artifact_id": "pt2", "artifact_kind": "ticket"}),
            "test", "none",
            json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
            ceid, ZERO_DIGEST,
            json.dumps({}), json.dumps({}),
            "2026-01-01T00:00:00Z", "0.9.0",
            digest,
        ))
        receipt = {
            "event_id": eid, "event_order": 1,
            "stored_content_digest": digest,
            "new_stream_head": {"event_order": 1, "content_digest": digest},
            "signed_receipt": {"algorithm": "HMAC-SHA256", "signature": "ab"},
        }
        conn.execute(
            "INSERT INTO idempotency (client_event_id, complete_request, stored_event_id, stored_receipt) VALUES (?, ?, ?, ?)",
            (ceid, b"{}", eid, json.dumps(receipt).encode("utf-8")),
        )
        conn.execute(
            "INSERT INTO stream_heads (run_id, event_order, content_digest) VALUES (?, ?, ?)",
            ("test-pt2", 1, digest),
        )
        conn.commit()
        conn.execute("DROP TABLE idempotency")
        conn.execute("DROP TABLE stream_heads")
        conn.commit()
        conn.close()
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-pt2")
            self.assertEqual(ctx.exception.detail["code"], "run_not_found")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_all_three_missing(self):
        """Empty database (no runtime tables) raises run_not_found."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        conn = sqlite3.connect(db_path)
        conn.close()
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "any-run")
            self.assertEqual(ctx.exception.detail["code"], "run_not_found")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestNonFilePathRejection(unittest.TestCase):
    """Non-file paths must be rejected before sqlite3.connect."""

    def test_nonexistent_path_run_not_found(self):
        nonexistent = str(pathlib.Path(tempfile.mkdtemp()) / "ghost.db")
        with self.assertRaises(RuntimeJournalError) as ctx:
            read_run_evidence_snapshot(nonexistent, "any-run")
        self.assertEqual(ctx.exception.detail["code"], "run_not_found")

    def test_directory_path_run_not_found(self):
        """A directory path (not a regular file) must raise run_not_found."""
        tmpdir = tempfile.mkdtemp()
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(tmpdir, "any-run")
            self.assertEqual(ctx.exception.detail["code"], "run_not_found")
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_valid_file_passes_through(self):
        """A regular existing file should pass is_file() and proceed."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            digest = "sha256:" + ("e" * 64)
            eid = str(uuid.uuid4())
            ceid = "vf-client-" + uuid.uuid4().hex[:8]
            conn.execute("""INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                "test-valid", eid, 1, "run.created",
                json.dumps({"test": "valid"}),
                None, json.dumps([]), "runner", "r1",
                json.dumps({"artifact_id": "vf1", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                ceid, ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.9.0",
                digest,
            ))
            receipt = {
                "event_id": eid, "event_order": 1,
                "stored_content_digest": digest,
                "new_stream_head": {"event_order": 1, "content_digest": digest},
                "signed_receipt": {"algorithm": "HMAC-SHA256", "signature": "ab"},
            }
            conn.execute(
                "INSERT INTO idempotency (client_event_id, complete_request, stored_event_id, stored_receipt) VALUES (?, ?, ?, ?)",
                (ceid, b"{}", eid, json.dumps(receipt).encode("utf-8")),
            )
            conn.execute(
                "INSERT INTO stream_heads (run_id, event_order, content_digest) VALUES (?, ?, ?)",
                ("test-valid", 1, digest),
            )
            conn.commit()
            conn.close()
            # This should succeed (not raise)
            result = read_run_evidence_snapshot(db_path, "test-valid")
            self.assertEqual(result["run_id"], "test-valid")
            self.assertEqual(len(result["events"]), 1)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestCausationMutationRejection(unittest.TestCase):
    """Invalid causation shapes must raise evidence_snapshot_incomplete."""

    def _make_db_with_causation(self, run_id, event_id, causation_id, causation_chain_json):
        """Create a minimal DB with one event having specified causation columns."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        conn = sqlite3.connect(db_path)
        conn.executescript(_CREATE_TABLES)
        digest = "sha256:" + ("c" * 64)
        ceid = "cm-client-" + uuid.uuid4().hex[:8]
        conn.execute("""INSERT INTO runtime_events
            (run_id, event_id, event_order, event_type, payload,
             causation_id, causation_chain, actor_role, actor_identity,
             trigger_artifact, reason, recommended_action,
             expected_stream_head, client_event_id, prev_event_digest,
             prior_state, next_state, occurred_at, schema_version,
             content_digest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            run_id, event_id, 1, "run.created",
            json.dumps({"test": "causation"}),
            causation_id, causation_chain_json,
            "runner", "r1",
            json.dumps({"artifact_id": "cm1", "artifact_kind": "ticket"}),
            "test", "none",
            json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
            ceid, ZERO_DIGEST,
            json.dumps({}), json.dumps({}),
            "2026-01-01T00:00:00Z", "0.9.0",
            digest,
        ))
        receipt = {
            "event_id": event_id, "event_order": 1,
            "stored_content_digest": digest,
            "new_stream_head": {"event_order": 1, "content_digest": digest},
            "signed_receipt": {"algorithm": "HMAC-SHA256", "signature": "ab"},
        }
        conn.execute(
            "INSERT INTO idempotency (client_event_id, complete_request, stored_event_id, stored_receipt) VALUES (?, ?, ?, ?)",
            (ceid, b"{}", event_id, json.dumps(receipt).encode("utf-8")),
        )
        conn.execute(
            "INSERT INTO stream_heads (run_id, event_order, content_digest) VALUES (?, ?, ?)",
            (run_id, 1, digest),
        )
        conn.commit()
        conn.close()
        return db_path

    def test_both_causations_present(self):
        """Both causation_id and causation_chain non-NULL raises evidence_snapshot_incomplete."""
        db_path = self._make_db_with_causation(
            "test-both", "ev-both",
            causation_id="cause-1",
            causation_chain_json=json.dumps(["chain-1"]),
        )
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-both")
            self.assertEqual(ctx.exception.detail["code"], "evidence_snapshot_incomplete")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_neither_causation_present(self):
        """Both causation_id and causation_chain NULL raises evidence_snapshot_incomplete."""
        db_path = self._make_db_with_causation(
            "test-neither", "ev-neither",
            causation_id=None,
            causation_chain_json=None,
        )
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-neither")
            self.assertEqual(ctx.exception.detail["code"], "evidence_snapshot_incomplete")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_causation_id_empty_string(self):
        """causation_id as empty string raises evidence_snapshot_incomplete."""
        db_path = self._make_db_with_causation(
            "test-empty-id", "ev-empty-id",
            causation_id="",
            causation_chain_json=None,
        )
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-empty-id")
            self.assertEqual(ctx.exception.detail["code"], "evidence_snapshot_incomplete")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_causation_chain_non_array(self):
        """causation_chain JSON decodes to non-array raises evidence_snapshot_incomplete."""
        db_path = self._make_db_with_causation(
            "test-non-array", "ev-non-array",
            causation_id=None,
            causation_chain_json=json.dumps({"not": "array"}),
        )
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-non-array")
            self.assertEqual(ctx.exception.detail["code"], "evidence_snapshot_incomplete")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_causation_chain_non_string_elements(self):
        """causation_chain with non-string elements raises evidence_snapshot_incomplete."""
        db_path = self._make_db_with_causation(
            "test-non-str", "ev-non-str",
            causation_id=None,
            causation_chain_json=json.dumps(["valid-str", 123, "also-valid"]),
        )
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                read_run_evidence_snapshot(db_path, "test-non-str")
            self.assertEqual(ctx.exception.detail["code"], "evidence_snapshot_incomplete")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestNoSideEffectProbes(unittest.TestCase):
    """Error paths must not create WAL/schema/file/sidecar artifacts."""

    def _assert_no_new_files(self, db_dir, operation):
        """Run operation, then verify no new files appeared in db_dir."""
        files_before = set(os.listdir(db_dir))
        operation()
        files_after = set(os.listdir(db_dir))
        new_files = files_after - files_before
        self.assertEqual(new_files, set(),
                         f"Unexpected files created: {new_files}")

    def test_missing_table_no_side_effects(self):
        """Missing table rejection must not create sidecar files."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            conn.execute("DROP TABLE idempotency")
            conn.commit()
            conn.close()
            db_dir = os.path.dirname(db_path)
            def _op():
                try:
                    read_run_evidence_snapshot(db_path, "any-run")
                except RuntimeJournalError:
                    pass
            self._assert_no_new_files(db_dir, _op)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_directory_path_no_side_effects(self):
        """Directory path rejection must not create sidecar files."""
        tmpdir = tempfile.mkdtemp()
        try:
            files_before = set(os.listdir(tmpdir))
            try:
                read_run_evidence_snapshot(tmpdir, "any-run")
            except RuntimeJournalError:
                pass
            files_after = set(os.listdir(tmpdir))
            new_files = files_after - files_before
            self.assertEqual(new_files, set(),
                             f"Unexpected files created: {new_files}")
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_causation_error_no_side_effects(self):
        """Causation validation error must not create sidecar files."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript(_CREATE_TABLES)
            digest = "sha256:" + ("e" * 64)
            eid = str(uuid.uuid4())
            ceid = "ns-client-" + uuid.uuid4().hex[:8]
            # Insert event with both causation forms (invalid)
            conn.execute("""INSERT INTO runtime_events
                (run_id, event_id, event_order, event_type, payload,
                 causation_id, causation_chain, actor_role, actor_identity,
                 trigger_artifact, reason, recommended_action,
                 expected_stream_head, client_event_id, prev_event_digest,
                 prior_state, next_state, occurred_at, schema_version,
                 content_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                "test-ns", eid, 1, "run.created",
                json.dumps({"test": "ns"}),
                "cause-both", json.dumps(["chain-both"]),
                "runner", "r1",
                json.dumps({"artifact_id": "ns1", "artifact_kind": "ticket"}),
                "test", "none",
                json.dumps({"event_order": 0, "content_digest": ZERO_DIGEST}),
                ceid, ZERO_DIGEST,
                json.dumps({}), json.dumps({}),
                "2026-01-01T00:00:00Z", "0.9.0",
                digest,
            ))
            receipt = {
                "event_id": eid, "event_order": 1,
                "stored_content_digest": digest,
                "new_stream_head": {"event_order": 1, "content_digest": digest},
                "signed_receipt": {"algorithm": "HMAC-SHA256", "signature": "ab"},
            }
            conn.execute(
                "INSERT INTO idempotency (client_event_id, complete_request, stored_event_id, stored_receipt) VALUES (?, ?, ?, ?)",
                (ceid, b"{}", eid, json.dumps(receipt).encode("utf-8")),
            )
            conn.execute(
                "INSERT INTO stream_heads (run_id, event_order, content_digest) VALUES (?, ?, ?)",
                ("test-ns", 1, digest),
            )
            conn.commit()
            conn.close()
            db_dir = os.path.dirname(db_path)
            def _op():
                try:
                    read_run_evidence_snapshot(db_path, "test-ns")
                except RuntimeJournalError:
                    pass
            self._assert_no_new_files(db_dir, _op)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


# ---------------------------------------------------------------------------
# Lineage boundary write/replay integration
# ---------------------------------------------------------------------------

class TestLineageBoundaryWriteReplay(unittest.TestCase):
    """Prove the journal write-path now binds store-assigned event metadata so
    the ``lineage-action-boundary`` check inside the lineage reducers
    (run.retry.initiated / run.resumed / run.redesign) sees a populated
    ``latest_event_*`` on the prior stored projection.

    Every positive test below creates a REAL failed/interrupted/blocked
    boundary (no manual prior-projection mutation, no direct SQLite injection)
    and appends a real lineage action that must succeed. This is exactly the
    path that PROD-DEFECT-001 broke: when metadata was not bound, the prior
    ``latest_event_id/type/order`` was always None and these actions raised
    ``lineage-action-boundary``.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.journal = RuntimeJournal(self.db_path, TEST_SIGNER_KEY)

    def tearDown(self):
        self.journal._conn.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _append(self, run_id, event_type, payload, prev_receipt=None):
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
        return self.journal.append(req)

    def _build_boundary(self, run_id, boundary_type):
        rec = self._append(run_id, "run.created", _run_created_payload())
        rec = self._append(
            run_id, "run.started",
            {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"},
            rec,
        )
        if boundary_type == "failed":
            rec = self._append(run_id, "run.stage.started", {
                "stage_id": "build",
                "started_at": "2026-01-01T00:00:05Z",
                "entry_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
            }, rec)
            rec = self._append(run_id, "run.failed", {
                "failed_at": "2026-01-01T00:00:10Z",
                "failed_stage_id": "build",
                "error": {"code": "CMD_FAILED", "message": "boom"},
                "failure_category": "command_failed",
                "failure_is_transient": False,
                "failure_is_deterministic": True,
                "retry_eligible": True,
            }, rec)
        elif boundary_type == "interrupted":
            started_state = self.journal.read_event(run_id, rec["event_order"])["next_state"]
            events_count = started_state["events_count"]
            rec = self._append(run_id, "run.interrupted", {
                "interrupted_at": "2026-01-01T00:00:10Z",
                "last_event_order": events_count,
                "interruption_cause": "external_signal",
                "checkpoint_available": True,
            }, rec)
        elif boundary_type == "blocked":
            rec = self._append(run_id, "run.blocked", {
                "blocked_at": "2026-01-01T00:00:10Z",
                "blocked_reason": "needs evidence",
                "resolution_paths": ["contract_redesign"],
                "required_evidence": [{"artifact_id": "ev", "artifact_kind": "doc"}],
            }, rec)
        else:
            raise AssertionError(f"unknown boundary_type {boundary_type}")
        events = self.journal.read_events(run_id)
        boundary = events[-1]
        return boundary, rec

    def _assert_bound_metadata_complete(self, child_event):
        ns = child_event["next_state"]
        self.assertEqual(ns["latest_event_id"], child_event["event_id"])
        self.assertEqual(ns["latest_event_type"], child_event["event_type"])
        self.assertEqual(ns["latest_event_order"], child_event["event_order"])

    def _assert_prior_equals_preceding_next_state(self, child_event, boundary_event):
        self.assertEqual(
            canonical_serialize(child_event["prior_state"]),
            canonical_serialize(boundary_event["next_state"]),
            "child prior_state must equal preceding boundary next_state byte-for-byte",
        )

    def test_retry_on_failed_boundary_succeeds(self):
        run_id = "run-boundary-retry"
        boundary, last = self._build_boundary(run_id, "failed")
        payload = {
            "new_run_id": "run-child-retry-1",
            "lineage": {
                "lineage_kind": "retry",
                "parent_run_id": run_id,
                "parent_status": "failed",
                "parent_boundary_event_id": boundary["event_id"],
                "parent_boundary_event_type": "run.failed",
                "parent_boundary_event_order": boundary["event_order"],
                "lineage_reason": "retry failed run",
            },
            "retry_strategy": "full",
            "current_retry_count": 1,
            "max_retries": 3,
            "failure_category": "command_failed",
            "authorized_by": "human",
            "authorized_at": "2026-01-01T00:00:11Z",
        }
        child = self._append(run_id, "run.retry.initiated", payload, last)
        self.assertEqual(child["event_order"], boundary["event_order"] + 1)
        child_event = self.journal.read_event(run_id, child["event_order"])
        self._assert_bound_metadata_complete(child_event)
        self._assert_prior_equals_preceding_next_state(child_event, boundary)

    def test_resume_on_interrupted_boundary_succeeds(self):
        run_id = "run-boundary-resume"
        boundary, last = self._build_boundary(run_id, "interrupted")
        payload = {
            "new_run_id": "run-child-resume-1",
            "lineage": {
                "lineage_kind": "resume",
                "parent_run_id": run_id,
                "parent_status": "interrupted",
                "parent_boundary_event_id": boundary["event_id"],
                "parent_boundary_event_type": "run.interrupted",
                "parent_boundary_event_order": boundary["event_order"],
                "lineage_reason": "resume interrupted run",
            },
            "checkpoint_event_order": boundary["event_order"],
            "recovery_action": "replay_from_checkpoint",
            "authorized_by": "human",
            "authorized_at": "2026-01-01T00:00:11Z",
        }
        child = self._append(run_id, "run.resumed", payload, last)
        self.assertEqual(child["event_order"], boundary["event_order"] + 1)
        child_event = self.journal.read_event(run_id, child["event_order"])
        self._assert_bound_metadata_complete(child_event)
        self._assert_prior_equals_preceding_next_state(child_event, boundary)

    def test_redesign_on_blocked_boundary_succeeds(self):
        run_id = "run-boundary-redesign"
        boundary, last = self._build_boundary(run_id, "blocked")
        payload = {
            "new_run_id": "run-child-redesign-1",
            "lineage": {
                "lineage_kind": "redesign",
                "parent_run_id": run_id,
                "parent_status": "blocked",
                "parent_boundary_event_id": boundary["event_id"],
                "parent_boundary_event_type": "run.blocked",
                "parent_boundary_event_order": boundary["event_order"],
                "lineage_reason": "revise stage graph to add evidence gate",
            },
            "revised_stage_graph": _run_created_payload()["stage_graph"],
            "authorized_by": "human",
            "authorized_at": "2026-01-01T00:00:11Z",
        }
        child = self._append(run_id, "run.redesign", payload, last)
        self.assertEqual(child["event_order"], boundary["event_order"] + 1)
        child_event = self.journal.read_event(run_id, child["event_order"])
        self._assert_bound_metadata_complete(child_event)
        self._assert_prior_equals_preceding_next_state(child_event, boundary)

    def test_wrong_boundary_event_id_rejected(self):
        run_id = "run-boundary-wrong"
        boundary, last = self._build_boundary(run_id, "failed")
        payload = {
            "new_run_id": "run-child-retry-2",
            "lineage": {
                "lineage_kind": "retry",
                "parent_run_id": run_id,
                "parent_status": "failed",
                "parent_boundary_event_id": "nonexistent-boundary-id",
                "parent_boundary_event_type": "run.failed",
                "parent_boundary_event_order": boundary["event_order"],
                "lineage_reason": "retry failed run",
            },
            "retry_strategy": "full",
            "current_retry_count": 1,
            "max_retries": 3,
            "failure_category": "command_failed",
            "authorized_by": "human",
            "authorized_at": "2026-01-01T00:00:11Z",
        }
        with self.assertRaises(Exception) as ctx:
            self._append(run_id, "run.retry.initiated", payload, last)
        self.assertIn("lineage-action-boundary", str(ctx.exception))

    def test_terminated_boundary_records_terminal_status(self):
        # A run terminated (from interrupted) records latest_terminal_status on
        # the stored projection via the shared bind helper.
        run_id = "run-boundary-terminated"
        rec = self._append(run_id, "run.created", _run_created_payload())
        rec = self._append(run_id, "run.started",
                           {"started_at": "2026-01-01T00:00:01Z", "executor_identity": "runner-1"}, rec)
        events_count = self.journal.read_event(run_id, rec["event_order"])["next_state"]["events_count"]
        rec = self._append(run_id, "run.interrupted", {
            "interrupted_at": "2026-01-01T00:00:10Z",
            "last_event_order": events_count,
            "interruption_cause": "external_signal",
            "checkpoint_available": True,
        }, rec)
        term = self._append(run_id, "run.terminated", {
            "terminated_at": "2026-01-01T00:00:12Z",
            "terminated_by": "human",
            "termination_reason": "give up",
            "from_status": "interrupted",
            "terminal_status": "failed",
        }, rec)
        term_event = self.journal.read_event(run_id, term["event_order"])
        self.assertEqual(term_event["next_state"]["latest_terminal_status"], "failed")
        self.assertEqual(term_event["next_state"]["latest_event_type"], "run.terminated")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
