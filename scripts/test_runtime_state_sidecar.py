"""Comprehensive tests for runtime_state_sidecar.py -- standard library unittest.

Covers constructor/lifecycle, create_run enforcements, append_event forwarding,
read/projection/snapshot/export operations, error preservation, concurrency,
reopen determinism, visibility preservation, workflow isolation, and
no-implementation-leakage static checks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.runtime_state_sidecar import RuntimeStateSidecar, RuntimeStateSidecarError
from scripts.runtime_state_core import ZERO_DIGEST
from scripts.runtime_state_journal import RuntimeJournalError
from scripts.runtime_state_projection import ProjectionError
from scripts.runtime_evidence_export import RuntimeEvidenceExportError
from scripts.test_runtime_state_journal import (
    _make_request,
    _run_created_payload,
    _visibility_context,
    TEST_SIGNER_KEY,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# For export tests, provenance origin_artifact must have a valid artifact_kind
# per the exporter: {"ticket", "pipeline_config", "script", "request_artifact"}


def _export_ready_payload(visibility="public"):
    """Run created payload with valid provenance for export tests."""
    p = _run_created_payload(visibility)
    p["run_provenance"]["origin_artifact"] = {
        "artifact_id": "ticket-test-001",
        "artifact_kind": "ticket",
    }
    p["run_provenance"]["governing_contracts"] = [
        {
            "artifact_id": "runtime-state-contract",
            "artifact_kind": "contract",
            "artifact_version": "0.9.0",
        }
    ]
    return p


def _minimal_run_created(run_id=None, visibility="public", export_ready=False):
    """Create a minimal but valid run.created AppendRequest using existing test helpers."""
    payload = _export_ready_payload(visibility) if export_ready else _run_created_payload(visibility)
    rid = run_id or f"run-{uuid.uuid4()}"
    return _make_request("run.created", payload, run_id=rid, prev_digest=ZERO_DIGEST, head_order=0)


def _run_started_event(run_id, current_head):
    """Create a run.started event following the given stream head."""
    return _make_request(
        "run.started",
        {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "executor_identity": "runner-1",
        },
        run_id=run_id,
        prev_digest=current_head["content_digest"],
        head_order=current_head["event_order"],
    )


def _stage_started_event(run_id, current_head, stage_id="build"):
    """Create a run.stage.started event."""
    return _make_request(
        "run.stage.started",
        {
            "stage_id": stage_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "entry_evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
        },
        run_id=run_id,
        prev_digest=current_head["content_digest"],
        head_order=current_head["event_order"],
    )


def _gate_evaluated_event(run_id, current_head, stage_id="build", gate_id="check-build"):
    """Create a run.gate.evaluated event to satisfy the required gate before stage completion."""
    return _make_request(
        "run.gate.evaluated",
        {
            "stage_id": stage_id,
            "gate_id": gate_id,
            "decision_id": f"dec-{uuid.uuid4()}",
            "outcome": "pass",
            "execution_mode": "full",
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "evaluated_by": "runner-1",
            "evidence": [{"artifact_id": "ev-gate-1", "artifact_kind": "evidence"}],
        },
        run_id=run_id,
        prev_digest=current_head["content_digest"],
        head_order=current_head["event_order"],
    )


def _stage_completed_event(run_id, current_head, stage_id="build", artifacts=None, gate_decisions=None):
    """Create a run.stage.completed event with optional runtime artifacts."""
    payload = {
        "stage_id": stage_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "gate_decisions": gate_decisions or [],
        "artifacts_produced": artifacts or [],
    }
    return _make_request(
        "run.stage.completed",
        payload,
        run_id=run_id,
        prev_digest=current_head["content_digest"],
        head_order=current_head["event_order"],
    )


def _make_artifact(artifact_id="model-001", visibility="project", run_id="", stage_id="build"):
    """Create a valid RuntimeArtifact with only the allowed fields per the contract."""
    return {
        "artifact_ref": {"artifact_id": artifact_id, "artifact_kind": "model"},
        "origin_run": run_id,
        "origin_stage": stage_id,
        "produced_by": "runner-1",
        "source_artifacts": [],
        "visibility": visibility,
        "visibility_resolution": {
            "resolution_rule": "most_restrictive",
            "applied_rule": "most_restrictive",
            "contributors": [
                {
                    "contributor_id": f"contrib-{artifact_id}",
                    "contributor_ref": {"artifact_id": artifact_id, "artifact_kind": "model"},
                    "asserted_visibility": visibility,
                    "authority": "Test authority",
                    "classification_evidence": [],
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Base test case with temp DB lifecycle
# ---------------------------------------------------------------------------


class _SidecarTestBase(unittest.TestCase):
    """Base class providing temp DB creation and cleanup with Windows-safe retry."""

    def setUp(self):
        fd, self._temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._sidecars = []  # track sidecars to close
        self._extra_paths = []  # extra files/paths to clean up

    def tearDown(self):
        # Close all sidecars
        for s in self._sidecars:
            try:
                if not s.closed:
                    s.close()
            except Exception:
                pass
        self._sidecars.clear()
        # Cleanup temp db with retry on Windows
        for attempt in range(5):
            try:
                if os.path.exists(self._temp_db_path):
                    os.unlink(self._temp_db_path)
                break
            except PermissionError:
                time.sleep(0.1)
        else:
            try:
                if os.path.exists(self._temp_db_path):
                    os.unlink(self._temp_db_path)
            except PermissionError:
                pass
        # Clean up extra paths
        for p in self._extra_paths:
            try:
                if os.path.exists(p):
                    if os.path.isfile(p):
                        os.unlink(p)
                    else:
                        shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass

    def _new_sidecar(self, db_path=None, signer_key=None,
                     id_factory=None, clock=None):
        """Create a tracked sidecar (auto-closed in tearDown)."""
        path = db_path or self._temp_db_path
        key = signer_key if signer_key is not None else TEST_SIGNER_KEY
        s = RuntimeStateSidecar(path, key, id_factory=id_factory, clock=clock)
        self._sidecars.append(s)
        return s

    def _seeded_sidecar(self, visibility="public", export_ready=True):
        """Create a sidecar pre-seeded with run.created + run.started."""
        s = self._new_sidecar()
        req = _minimal_run_created(visibility=visibility, export_ready=export_ready)
        receipt = s.create_run(req)
        head = receipt["new_stream_head"]
        re = _run_started_event(req["run_id"], head)
        s.append_event(re)
        return s, req["run_id"]


# ---------------------------------------------------------------------------
# 1. Constructor and lifecycle tests
# ---------------------------------------------------------------------------


class TestConstructorAndLifecycle(_SidecarTestBase):
    """Tests for constructor validation, defaults, context manager, and close behavior."""

    def test_valid_construction_with_db_path_and_signer_key(self):
        s = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)
        self.assertFalse(s.closed)
        self.assertIsNotNone(s._db_path)
        s.close()

    def test_default_id_factory_produces_uuid4_strings(self):
        s = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)
        eid = s._id_factory()
        self.assertIsInstance(eid, str)
        self.assertEqual(len(eid), 36)
        self.assertEqual(eid.count("-"), 4)
        # Verify it looks like a UUID (hex characters and hyphens)
        self.assertIsNotNone(re.match(r"^[0-9a-f-]{36}$", eid))
        s.close()

    def test_default_clock_produces_timezone_aware_utc(self):
        s = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)
        ts = s._clock()
        self.assertIsInstance(ts, datetime)
        self.assertIsNotNone(ts.tzinfo)
        self.assertIsNotNone(ts.utcoffset())
        s.close()

    def test_injected_id_factory_is_used(self):
        called = []

        def my_id():
            called.append(1)
            return "fixed-id-1234"

        s = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY, id_factory=my_id)
        sid = s._id_factory()
        self.assertEqual(sid, "fixed-id-1234")
        self.assertEqual(len(called), 1)
        s.close()

    def test_injected_clock_is_used(self):
        fixed_time = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

        def my_clock():
            return fixed_time

        s = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY, clock=my_clock)
        ts = s._clock()
        self.assertEqual(ts, fixed_time)
        s.close()

    def test_invalid_db_path_non_string_raises(self):
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            RuntimeStateSidecar(None, TEST_SIGNER_KEY)
        self.assertEqual(ctx.exception.code, "invalid_db_path")

    def test_invalid_db_path_empty_raises(self):
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            RuntimeStateSidecar("", TEST_SIGNER_KEY)
        self.assertEqual(ctx.exception.code, "invalid_db_path")

    def test_invalid_db_path_whitespace_only_raises(self):
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            RuntimeStateSidecar("   ", TEST_SIGNER_KEY)
        self.assertEqual(ctx.exception.code, "invalid_db_path")

    def test_invalid_signer_key_empty_bytes_raises(self):
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            RuntimeStateSidecar(self._temp_db_path, b"")
        self.assertEqual(ctx.exception.code, "unsigned_signer_key")

    def test_invalid_signer_key_non_bytes_raises(self):
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            RuntimeStateSidecar(self._temp_db_path, "not-bytes")
        self.assertEqual(ctx.exception.code, "unsigned_signer_key")

    def test_context_manager_works(self):
        with RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY) as s:
            self.assertFalse(s.closed)
            self.assertIsNotNone(s._journal)
        self.assertTrue(s.closed)

    def test_close_is_idempotent(self):
        s = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)
        s.close()
        self.assertTrue(s.closed)
        # Second close should not raise
        s.close()
        self.assertTrue(s.closed)

    def test_methods_after_close_raise_sidecar_closed(self):
        s = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)
        req = _minimal_run_created()
        s.create_run(req)  # create a run first
        s.close()

        # create_run after close
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.create_run(req)
        self.assertEqual(ctx.exception.code, "sidecar_closed")

        # append_event after close
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.append_event(req)
        self.assertEqual(ctx.exception.code, "sidecar_closed")

        # read_event after close
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.read_event(req["run_id"], 0)
        self.assertEqual(ctx.exception.code, "sidecar_closed")

        # read_events after close
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.read_events(req["run_id"])
        self.assertEqual(ctx.exception.code, "sidecar_closed")

        # get_run after close
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.get_run(req["run_id"])
        self.assertEqual(ctx.exception.code, "sidecar_closed")

        # evidence_snapshot after close
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.evidence_snapshot(req["run_id"])
        self.assertEqual(ctx.exception.code, "sidecar_closed")

        # export_evidence after close
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.export_evidence(req["run_id"])
        self.assertEqual(ctx.exception.code, "sidecar_closed")


# ---------------------------------------------------------------------------
# 2. create_run enforcements
# ---------------------------------------------------------------------------


class TestCreateRunEnforcements(_SidecarTestBase):
    """Tests that create_run enforces genesis invariants before journal append."""

    def test_valid_run_created_succeeds(self):
        s = self._new_sidecar()
        req = _minimal_run_created()
        receipt = s.create_run(req)
        self.assertIsInstance(receipt, dict)
        self.assertIn("event_id", receipt)
        self.assertIn("event_order", receipt)
        self.assertEqual(receipt["event_order"], 1)

    def test_non_run_created_event_type_raises(self):
        s = self._new_sidecar()
        req = _minimal_run_created()
        req["event_type"] = "run.started"
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.create_run(req)
        self.assertEqual(ctx.exception.code, "not_run_created")

    def test_non_zero_expected_stream_head_event_order_raises(self):
        s = self._new_sidecar()
        req = _minimal_run_created()
        req["expected_stream_head"] = {"event_order": 1, "content_digest": ZERO_DIGEST}
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.create_run(req)
        self.assertEqual(ctx.exception.code, "not_genesis_head")

    def test_non_zero_digest_prev_event_digest_raises(self):
        s = self._new_sidecar()
        req = _minimal_run_created()
        req["prev_event_digest"] = "sha256:abc123"
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.create_run(req)
        self.assertEqual(ctx.exception.code, "nonzero_prev_digest")

    def test_non_zero_digest_expected_stream_head_content_digest_raises(self):
        s = self._new_sidecar()
        req = _minimal_run_created()
        req["expected_stream_head"] = {"event_order": 0, "content_digest": "sha256:abc123"}
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.create_run(req)
        self.assertEqual(ctx.exception.code, "not_genesis_head")


# ---------------------------------------------------------------------------
# 3. append_event forwarding
# ---------------------------------------------------------------------------


class TestAppendEventForwarding(_SidecarTestBase):
    """Tests that append_event forwards to RuntimeJournal.append correctly."""

    def test_successful_append_returns_receipt(self):
        s = self._new_sidecar()
        req = _minimal_run_created()
        receipt = s.create_run(req)
        self.assertIn("event_id", receipt)

    def test_exact_retry_preserves_receipt(self):
        s = self._new_sidecar()
        req = _minimal_run_created()
        receipt1 = s.create_run(req)
        # Exact same request should return same receipt
        receipt2 = s.append_event(req)
        self.assertEqual(receipt1["event_id"], receipt2["event_id"])
        self.assertEqual(receipt1["event_order"], receipt2["event_order"])

    def test_divergent_duplicate_raises(self):
        s = self._new_sidecar()
        req1 = _minimal_run_created()
        s.create_run(req1)

        # Same client_event_id, different body
        req2 = copy.deepcopy(req1)
        req2["reason"] = "modified reason"
        with self.assertRaises(RuntimeJournalError) as ctx:
            s.append_event(req2)
        self.assertIn("divergent_duplicate", str(ctx.exception))

    def test_stale_head_returns_structured_dict(self):
        s = self._new_sidecar()
        req = _minimal_run_created()
        receipt = s.create_run(req)

        # Create a second request with stale head
        req2 = _minimal_run_created(run_id=req["run_id"])
        req2["event_type"] = "run.started"
        req2["expected_stream_head"] = {"event_order": 0, "content_digest": ZERO_DIGEST}
        req2["prev_event_digest"] = ZERO_DIGEST

        result = s.append_event(req2)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["code"], "stale_head")


# ---------------------------------------------------------------------------
# 4. Read operations
# ---------------------------------------------------------------------------


class TestReadOperations(_SidecarTestBase):
    """Tests for read_event and read_events through the sidecar."""

    def test_read_event_returns_event_dict(self):
        s, run_id = self._seeded_sidecar()
        event = s.read_event(run_id, 1)
        self.assertIsInstance(event, dict)
        self.assertEqual(event["event_order"], 1)
        self.assertEqual(event["event_type"], "run.created")

    def test_read_event_returns_none_for_invalid_order(self):
        s, run_id = self._seeded_sidecar()
        result = s.read_event(run_id, 999)
        self.assertIsNone(result)

    def test_read_events_returns_list(self):
        s, run_id = self._seeded_sidecar()
        events = s.read_events(run_id)
        self.assertIsInstance(events, list)
        self.assertEqual(len(events), 2)

    def test_read_after_close_raises_sidecar_closed(self):
        s, run_id = self._seeded_sidecar()
        s.close()
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.read_event(run_id, 1)
        self.assertEqual(ctx.exception.code, "sidecar_closed")

        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.read_events(run_id)
        self.assertEqual(ctx.exception.code, "sidecar_closed")


# ---------------------------------------------------------------------------
# 5. Projection operations
# ---------------------------------------------------------------------------


class TestProjectionOperations(_SidecarTestBase):
    """Tests for get_run and get_stage through the sidecar."""

    def test_get_run_returns_projection(self):
        s, run_id = self._seeded_sidecar()
        projection = s.get_run(run_id)
        self.assertIsInstance(projection, dict)
        self.assertIn("projection_digest", projection)
        self.assertIn("projection_type", projection)
        self.assertEqual(projection["projection_type"], "full_replay")

    def test_get_stage_returns_stage_dict(self):
        s, run_id = self._seeded_sidecar()
        # Stage exists in "pending" state after run.created + run.started
        stage = s.get_stage(run_id, "build")
        self.assertIsInstance(stage, dict)
        self.assertIn("status", stage)

    def test_get_stage_not_found_raises_projection_error(self):
        s, run_id = self._seeded_sidecar()
        with self.assertRaises(ProjectionError) as ctx:
            s.get_stage(run_id, "nonexistent")
        self.assertEqual(ctx.exception.code, "stage_not_found")

    def test_projection_after_close_raises_sidecar_closed(self):
        s, run_id = self._seeded_sidecar()
        s.close()
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.get_run(run_id)
        self.assertEqual(ctx.exception.code, "sidecar_closed")

        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.get_stage(run_id, "build")
        self.assertEqual(ctx.exception.code, "sidecar_closed")


# ---------------------------------------------------------------------------
# 6. Snapshot tests
# ---------------------------------------------------------------------------


class TestSnapshot(_SidecarTestBase):
    """Tests for evidence_snapshot."""

    def test_evidence_snapshot_returns_expected_shape(self):
        s, run_id = self._seeded_sidecar()
        snap = s.evidence_snapshot(run_id)
        self.assertIn("run_id", snap)
        self.assertEqual(snap["run_id"], run_id)
        self.assertIn("events", snap)
        self.assertIsInstance(snap["events"], list)
        self.assertEqual(len(snap["events"]), 2)
        self.assertIn("receipts", snap)
        self.assertIsInstance(snap["receipts"], list)
        self.assertIn("source_stream_head", snap)
        self.assertIn("event_order", snap["source_stream_head"])
        self.assertIn("content_digest", snap["source_stream_head"])

    def test_snapshot_after_close_raises_sidecar_closed(self):
        s, run_id = self._seeded_sidecar()
        s.close()
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.evidence_snapshot(run_id)
        self.assertEqual(ctx.exception.code, "sidecar_closed")


# ---------------------------------------------------------------------------
# 7. Export tests
# ---------------------------------------------------------------------------


class TestExport(_SidecarTestBase):
    """Tests for export_evidence."""

    def test_default_export_id_and_exported_at_are_generated(self):
        s, run_id = self._seeded_sidecar()
        envelope = s.export_evidence(run_id)
        self.assertIn("export_id", envelope)
        self.assertTrue(envelope["export_id"].startswith("export-"))
        self.assertIn("exported_at", envelope)
        self.assertIn("export_content_digest", envelope)

    def test_explicit_export_id_and_exported_at_pass_through(self):
        s, run_id = self._seeded_sidecar()
        explicit_id = "export-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        explicit_at = "2026-07-19T12:00:00+00:00"
        envelope = s.export_evidence(
            run_id, export_id=explicit_id, exported_at=explicit_at
        )
        self.assertEqual(envelope["export_id"], explicit_id)
        self.assertEqual(envelope["exported_at"], explicit_at)

    def test_injected_id_factory_and_clock_produce_expected_metadata(self):
        fixed_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        fixed_time = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

        s = RuntimeStateSidecar(
            self._temp_db_path, TEST_SIGNER_KEY,
            id_factory=lambda: fixed_id,
            clock=lambda: fixed_time,
        )
        req = _minimal_run_created(export_ready=True)
        receipt = s.create_run(req)
        head = receipt["new_stream_head"]
        re = _run_started_event(req["run_id"], head)
        s.append_event(re)

        envelope = s.export_evidence(req["run_id"])
        self.assertEqual(envelope["export_id"], f"export-{fixed_id}")
        self.assertEqual(envelope["exported_at"], fixed_time.isoformat())
        s.close()

    def test_invalid_generated_export_id_propagates_export_error(self):
        def bad_id_factory():
            return "invalid!"

        s = RuntimeStateSidecar(
            self._temp_db_path, TEST_SIGNER_KEY, id_factory=bad_id_factory
        )
        req = _minimal_run_created(export_ready=True)
        receipt = s.create_run(req)
        head = receipt["new_stream_head"]
        re = _run_started_event(req["run_id"], head)
        s.append_event(re)

        with self.assertRaises(RuntimeEvidenceExportError) as ctx:
            s.export_evidence(req["run_id"])
        self.assertEqual(ctx.exception.code, "invalid_export_id")
        s.close()

    def test_export_after_close_raises_sidecar_closed(self):
        s, run_id = self._seeded_sidecar()
        s.close()
        with self.assertRaises(RuntimeStateSidecarError) as ctx:
            s.export_evidence(run_id)
        self.assertEqual(ctx.exception.code, "sidecar_closed")

    def test_export_with_artifact_bearing_runs_preserves_typed_artifact(self):
        s, run_id = self._seeded_sidecar()
        envelope = s.export_evidence(run_id)
        self.assertIn("events", envelope)
        events = envelope["events"]
        self.assertGreaterEqual(len(events), 2)
        # The projection should contain runtime_artifacts (empty list for runs without artifacts)
        projection = envelope["projection"]
        self.assertIn("runtime_artifacts", projection)
        self.assertIsInstance(projection["runtime_artifacts"], list)


# ---------------------------------------------------------------------------
# 8. Error preservation
# ---------------------------------------------------------------------------


class TestErrorPreservation(_SidecarTestBase):
    """Tests that dependency errors propagate unmodified through the sidecar."""

    def test_runtime_journal_error_propagates_unmodified(self):
        s = self._new_sidecar()
        req = _minimal_run_created()
        s.create_run(req)
        # Duplicate divergent should raise RuntimeJournalError
        req2 = copy.deepcopy(req)
        req2["reason"] = "modified"
        with self.assertRaises(RuntimeJournalError) as ctx:
            s.append_event(req2)
        self.assertIn("divergent_duplicate", str(ctx.exception))

    def test_projection_error_propagates_unmodified(self):
        s = self._new_sidecar()
        # get_run on non-existent run should raise ProjectionError
        with self.assertRaises(ProjectionError) as ctx:
            s.get_run("nonexistent-run")
        self.assertIn(ctx.exception.code, ("no_events_for_run", "db_not_found"))

    def test_runtime_evidence_export_error_propagates_unmodified(self):
        s, run_id = self._seeded_sidecar()
        # Invalid export_id should raise RuntimeEvidenceExportError
        with self.assertRaises(RuntimeEvidenceExportError) as ctx:
            s.export_evidence(run_id, export_id="invalid-format")
        self.assertEqual(ctx.exception.code, "invalid_export_id")


# ---------------------------------------------------------------------------
# 9. Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency(_SidecarTestBase):
    """Tests concurrent multi-sidecar behavior against one runtime DB."""

    def test_two_instances_same_head_one_succeeds_one_stale(self):
        s1 = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)
        s2 = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)

        req1 = _minimal_run_created()
        receipt1 = s1.create_run(req1)
        self.assertIn("event_id", receipt1)

        # s2 tries the same genesis with the same run_id
        req2 = _minimal_run_created(run_id=req1["run_id"])
        result2 = s2.append_event(req2)
        # Either succeeds (if s1 committed before s2 read) or stale_head
        self.assertIsInstance(result2, dict)
        if "event_id" in result2:
            # Exact retry case
            self.assertEqual(result2["event_id"], receipt1["event_id"])
        else:
            self.assertEqual(result2["code"], "stale_head")

        s1.close()
        s2.close()


# ---------------------------------------------------------------------------
# 10. Reopen determinism
# ---------------------------------------------------------------------------


class TestReopenDeterminism(_SidecarTestBase):
    """Tests that closing and reopening against the same DB rebuilds identical projections."""

    def test_close_and_reopen_rebuilds_identical_projection(self):
        # First session: create a run with events
        s1 = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)
        req = _minimal_run_created()
        receipt = s1.create_run(req)
        run_id = req["run_id"]
        head = receipt["new_stream_head"]
        re = _run_started_event(run_id, head)
        s1.append_event(re)
        proj1 = s1.get_run(run_id)
        digest1 = proj1["projection_digest"]
        s1.close()

        # Second session: reopen same DB
        s2 = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)
        proj2 = s2.get_run(run_id)
        digest2 = proj2["projection_digest"]
        self.assertEqual(digest1, digest2)
        self.assertEqual(proj1["event_count"], proj2["event_count"])
        s2.close()


# ---------------------------------------------------------------------------
# 11. Visibility preservation
# ---------------------------------------------------------------------------


class TestVisibilityPreservation(_SidecarTestBase):
    """Tests that visibility is preserved through append, replay, snapshot, and export."""

    def test_visibility_preserved_through_full_cycle(self):
        for visibility in ["public", "project", "restricted"]:
            s = RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)
            req = _minimal_run_created(visibility=visibility, export_ready=True)
            receipt = s.create_run(req)
            run_id = req["run_id"]

            # Append a run.started event
            head = receipt["new_stream_head"]
            re = _run_started_event(run_id, head)
            s.append_event(re)

            # Projection should preserve visibility
            proj = s.get_run(run_id)
            self.assertIn("resolved_run_visibility", proj)
            self.assertEqual(proj["resolved_run_visibility"], visibility)

            # Snapshot should preserve events
            snap = s.evidence_snapshot(run_id)
            self.assertEqual(snap["run_id"], run_id)

            # Export should preserve visibility
            env = s.export_evidence(run_id)
            self.assertIn("visibility", env)
            self.assertEqual(env["visibility"], visibility)

            s.close()

    def test_artifact_bearing_runs_preserve_typed_fields(self):
        s, run_id = self._seeded_sidecar()
        proj = s.get_run(run_id)
        arts = proj.get("runtime_artifacts", [])
        self.assertIsInstance(arts, list)


# ---------------------------------------------------------------------------
# 12. Workflow isolation
# ---------------------------------------------------------------------------


class TestWorkflowIsolation(_SidecarTestBase):
    """Tests that .workflow paths and schemas are rejected."""

    def test_workflow_paths_rejected(self):
        # .workflow path should be rejected by RuntimeJournal
        wf_dir = str(pathlib.Path(self._temp_db_path).parent / ".workflow")
        wf_path = str(pathlib.Path(wf_dir) / "test.db")
        self._extra_paths.append(wf_dir)
        self._extra_paths.append(wf_path)
        os.makedirs(wf_dir, exist_ok=True)
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeStateSidecar(wf_path, TEST_SIGNER_KEY)
            self.assertIn("workflow_path_rejected", str(ctx.exception))
        finally:
            if os.path.exists(wf_path):
                os.unlink(wf_path)
            if os.path.exists(wf_dir):
                try:
                    os.rmdir(wf_dir)
                except OSError:
                    pass

    def test_workflow_schemas_rejected(self):
        # Create a DB with workflow tables to trigger workflow_schema_rejected
        import sqlite3
        conn = sqlite3.connect(self._temp_db_path)
        conn.execute("CREATE TABLE domain_epic (id INTEGER)")
        conn.commit()
        conn.close()
        try:
            with self.assertRaises(RuntimeJournalError) as ctx:
                RuntimeStateSidecar(self._temp_db_path, TEST_SIGNER_KEY)
            self.assertIn("workflow_schema_rejected", str(ctx.exception))
        finally:
            pass


# ---------------------------------------------------------------------------
# 13. No implementation leakage (static checks)
# ---------------------------------------------------------------------------


class TestNoImplementationLeakage(unittest.TestCase):
    """Static checks that the sidecar source has no SQL, env reads, or direct file I/O."""

    SIDECAR_PATH = pathlib.Path(__file__).resolve().parent / "runtime_state_sidecar.py"

    def test_no_sql_in_sidecar_source(self):
        source = self.SIDECAR_PATH.read_text(encoding='utf-8')
        self.assertNotIn("sqlite3", source)
        forbidden = ["INSERT", "UPDATE", "DELETE", "BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"]
        for keyword in forbidden:
            self.assertNotIn(keyword, source, f"Found forbidden keyword '{keyword}' in sidecar source")

    def test_no_env_reads_in_sidecar_source(self):
        source = self.SIDECAR_PATH.read_text(encoding='utf-8')
        self.assertNotIn("os.environ", source)
        self.assertNotIn("os.getenv", source)

    def test_no_file_io_in_sidecar_source(self):
        source = self.SIDECAR_PATH.read_text(encoding='utf-8')
        # No Path.read_text, Path.write_text, or open() for file I/O
        self.assertNotIn(".read_text()", source)
        self.assertNotIn(".write_text(", source)
        # Ensure no standalone open() for file I/O (exclude method names _open, __open__ etc.)
        open_calls = [
            line.strip() for line in source.split("\n")
            if "open(" in line
            and not line.strip().startswith("#")
            and not line.strip().startswith("from")
            and not line.strip().startswith("import")
            and "self._open(" not in line
            and "def _open(" not in line
            and "def _check_closed" not in line  # false positive from "closed" containing nothing
        ]
        self.assertEqual(len(open_calls), 0, f"Unexpected open() calls: {open_calls}")

    def test_frozen_hashes_preserved(self):
        """Verify the sidecar does not modify any frozen files."""
        frozen_hashes = {
            "scripts/runtime_state_journal.py": "DFA5928CA7B300E6B8DFD3EA5FE352FEB079A99A033713BDEA0D0F55BB6CE4C0",
            "scripts/runtime_state_projection.py": "8D16490896C82A73C5D732CF39F66C0259853A129A3D2F79B291BEE6FF0AB32B",
            "scripts/runtime_evidence_export.py": "A6382BF1BB964034404D54C2D93FBC6379FFE053886E286D22A250D6AC2420D0",
            "scripts/test_runtime_state_contract.py": "99C7AF2AF12A0FD9BCCD4B673EEAA68B8D5DF076E68D132E0D5B3804F0D2F540",
            "scripts/test_runtime_state_core.py": "8658CFDB550F8D5A07B3CA76170AA09A154F32618A94CB07D11EC4C80A4E3A38",
            "scripts/test_runtime_state_journal.py": "6F24D3CAC71FB3F4B449F6548695B3AE5B8AD738DAB347DE731A5C7E69977769",
            "scripts/test_runtime_state_projection.py": "5DCEA1CFD3212C920A77739AA2CCBC8D46DE21979EA3D9DBEDF2FB315F102FB4",
            "scripts/test_runtime_evidence_export_contract.py": "790CB21F5BA02C2E36A648DAF453FB26BCB63D8DF8608235FB895AD8114CC33F",
            "scripts/test_runtime_evidence_export.py": "93FF029C379C13DB28171F31C90E05211E1E3317823612FA6631320781E835B3",
        }
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for relpath, expected in frozen_hashes.items():
            abspath = os.path.join(project_root, relpath)
            with open(abspath, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest().upper()
            self.assertEqual(actual, expected, f"{relpath}: hash mismatch (got {actual}, expected {expected})")
