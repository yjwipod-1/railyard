"""E2E conformance: strict pipeline with independent oracle cryptographic verification.

stdlib unittest only. All 8 operations through RuntimeAdapter.process().
Deterministic id_factory and clock. 5-event pipeline creating a RuntimeArtifact
via stage.completed. Independent oracle: content_digest (event minus
content_digest and causation_id), prev_event_digest chain, HMAC,
projection_digest, export_content_digest -- all recomputed with local
_oracle_canonical. No production oracle imports. No permissive assertions.
"""
import hashlib, hmac, json, os, pathlib, shutil, sys, tempfile, unittest, uuid
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.runtime_adapter import RuntimeAdapter, _FROZEN_OPERATIONS
from scripts.runtime_state_sidecar import RuntimeStateSidecar, RuntimeStateSidecarError
from scripts.runtime_state_core import ZERO_DIGEST
from scripts.test_runtime_state_journal import _make_request, _run_created_payload

TEST_SIGNER_KEY = b"e2e-strict-key-32bytes!!"
ALL_FULL = [{"capability": op, "state": "full"} for op in _FROZEN_OPERATIONS]

# Deterministic factories
_ID_COUNTER = [0]
def _det_id():
    _ID_COUNTER[0] += 1
    return f"id-{_ID_COUNTER[0]:04d}"

_DET_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CLOCK_TICK = [0]
def _det_clock():
    _CLOCK_TICK[0] += 1
    return (_DET_EPOCH.replace(minute=_CLOCK_TICK[0] % 60,
                                second=_CLOCK_TICK[0] % 60)).isoformat()


# ---------------------------------------------------------------------------
# Independent oracle -- no dependency on production digest helpers
# ---------------------------------------------------------------------------

def _oracle_canonical(value):
    """Canonicalize a JSON value for deterministic digest computation.

    Sorts keys, uses compact separators. E2E test data is constrained:
    ASCII-only object keys, no float values, no NaN.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _oracle_digest(value):
    """SHA-256 digest over _oracle_canonical bytes."""
    return "sha256:" + hashlib.sha256(_oracle_canonical(value)).hexdigest()


def _oracle_hmac(key, payload):
    """HMAC-SHA256 of _oracle_canonical(payload) with key."""
    return hmac.new(key, _oracle_canonical(payload), "sha256").hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk(op, params, rid="r1"):
    return {
        "protocol_version": "1.0.0",
        "request_id": rid,
        "operation": op,
        "payload": {"operation": op, "params": params},
    }


def _db_sha(db_path):
    h = hashlib.sha256()
    with open(db_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Canonical RuntimeArtifact (mirrors sidecar test helpers)
# ---------------------------------------------------------------------------

def _make_artifact(artifact_id="model-001", visibility="project", run_id="", stage_id="build"):
    return {
        "artifact_ref": {"artifact_id": artifact_id, "artifact_kind": "model"},
        "origin_run": run_id,
        "origin_stage": stage_id,
        "produced_by": "runner-1",
        "source_artifacts": [],
        "visibility": visibility,
        "visibility_resolution": {
            "resolution_id": f"res-{artifact_id}",
            "resolved_at": _det_clock(),
            "contributors": [
                {
                    "contributor_id": f"contrib-{artifact_id}",
                    "contributor_kind": "source_artifact",
                    "contributor_ref": {"artifact_id": artifact_id, "artifact_kind": "model"},
                    "asserted_visibility": visibility,
                    "authority": "Test authority",
                    "classification_evidence": [
                        {"artifact_id": f"clf-{artifact_id}", "artifact_kind": "classification",
                         "artifact_version": "1.0.0", "locator": f"file://{run_id}/class.yaml"}
                    ],
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


def _stage_completed_request(run_id, current_head, stage_id, artifacts, gate_decisions):
    payload = {
        "stage_id": stage_id,
        "completed_at": _det_clock(),
        "gate_decisions": gate_decisions,
        "artifacts_produced": artifacts,
    }
    return _make_request(
        "run.stage.completed", payload,
        run_id=run_id,
        prev_digest=current_head["content_digest"],
        head_order=current_head["event_order"],
    )


# =============================================================================
# Positive E2E Pipeline
# =============================================================================

class E2EStrictPipeline(unittest.TestCase):
    """All 8 operations through adapter. 5-event pipeline. RuntimeArtifact.

    Every assertion is strict single-outcome. All cryptographic verification
    is mandatory -- no conditional or optional checks.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="e2e-pipe-")
        self._db = str(pathlib.Path(self._tmp) / "pipe.db")
        self._sc = RuntimeStateSidecar(
            self._db, TEST_SIGNER_KEY, id_factory=_det_id, clock=_det_clock,
        )
        self._adapter = RuntimeAdapter(self._sc, ALL_FULL)

    def tearDown(self):
        self._sc.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _assert_ascii_keys(self, data, label="data"):
        """Assert all object keys are ASCII (precondition for oracle)."""
        if isinstance(data, dict):
            for k in data:
                self.assertTrue(all(ord(c) < 128 for c in k),
                                f"Non-ASCII key {k!r} in {label}")
                self._assert_ascii_keys(data[k], f"{label}.{k}")
        elif isinstance(data, list):
            for i, item in enumerate(data):
                self._assert_ascii_keys(item, f"{label}[{i}]")

    def _run_pipeline(self, visibility, rid):
        # ---- Event 1: create_run (via adapter) ----
        payload = _run_created_payload(visibility)
        payload["run_provenance"]["origin_artifact"] = {
            "artifact_id": f"ticket-{rid}", "artifact_kind": "ticket",
        }
        payload["run_provenance"]["governing_contracts"] = [
            {"artifact_id": "runtime-adapter-contract", "artifact_kind": "contract",
             "artifact_version": "1.0.0"}
        ]
        payload["created_at"] = _det_clock()
        req = _make_request("run.created", payload, run_id=rid,
                            prev_digest=ZERO_DIGEST, head_order=0)
        r = self._adapter.process(_mk("create_run", req, rid))
        self.assertEqual(r["status"], "success", f"create_run failed")
        self.assertIsInstance(r["data"], dict)
        self.assertEqual(r["data"]["event_order"], 1)

        # ---- Event 2: run.started (via adapter) ----
        events = self._sc.read_events(rid)
        self._assert_ascii_keys(events[-1], f"event{len(events)}")
        last = events[-1]
        r2 = _make_request("run.started",
            {"started_at": _det_clock(), "executor_identity": "runner-1"},
            run_id=rid, prev_digest=last["content_digest"],
            head_order=last["event_order"])
        r = self._adapter.process(_mk("append_event", r2))
        self.assertEqual(r["status"], "success", "run.started failed")
        self.assertEqual(r["data"]["event_order"], 2)

        # ---- Event 3: run.stage.started (via adapter) ----
        events = self._sc.read_events(rid)
        last = events[-1]
        r3 = _make_request("run.stage.started",
            {"stage_id": "build", "started_at": _det_clock(),
             "entry_evidence": [
                 {"artifact_id": f"ev-{rid}", "artifact_kind": "snapshot",
                  "artifact_version": "1.0.0", "locator": f"file://{rid}/evidence"}
             ]},
            run_id=rid, prev_digest=last["content_digest"],
            head_order=last["event_order"])
        r = self._adapter.process(_mk("append_event", r3))
        self.assertEqual(r["status"], "success", "stage.started failed")
        self.assertEqual(r["data"]["event_order"], 3)

        # ---- Event 4: run.gate.evaluated (via adapter) ----
        events = self._sc.read_events(rid)
        last = events[-1]
        gate_did = f"dec-{rid}"
        r4 = _make_request("run.gate.evaluated",
            {"stage_id": "build", "gate_id": "check-build",
             "decision_id": gate_did, "outcome": "pass",
             "execution_mode": "full",
             "evaluated_at": _det_clock(), "evaluated_by": "validator-1",
             "evidence": [
                 {"artifact_id": f"gate-ev-{rid}", "artifact_kind": "report",
                  "artifact_version": "1.0.0"}
             ]},
            run_id=rid, prev_digest=last["content_digest"],
            head_order=last["event_order"])
        r = self._adapter.process(_mk("append_event", r4))
        self.assertEqual(r["status"], "success", "gate.evaluated failed")
        self.assertEqual(r["data"]["event_order"], 4)

        # ---- Event 5: run.stage.completed with RuntimeArtifact ----
        events = self._sc.read_events(rid)
        last = events[-1]
        artifact = _make_artifact(
            artifact_id=f"art-{rid}", visibility=visibility,
            run_id=rid, stage_id="build",
        )
        gate_decisions = [{"decision_id": gate_did}]
        r5 = _stage_completed_request(rid, last, "build", [artifact], gate_decisions)
        r = self._adapter.process(_mk("append_event", r5))
        self.assertEqual(r["status"], "success", "stage.completed failed")
        self.assertIsInstance(r["data"], dict)
        self.assertEqual(r["data"]["event_order"], 5)

        # ---- read_events: exactly 5 events, orders [1,2,3,4,5] ----
        r = self._adapter.process(_mk("read_events", {"run_id": rid}))
        self.assertEqual(r["status"], "success", "read_events failed")
        self.assertIsInstance(r["data"], list)
        self.assertEqual(len(r["data"]), 5)
        actual_orders = [ev["event_order"] for ev in r["data"]]
        self.assertEqual(actual_orders, [1, 2, 3, 4, 5])

        # ---- read_event object ----
        r = self._adapter.process(_mk("read_event", {"run_id": rid, "event_order": 1}))
        self.assertEqual(r["status"], "success")
        self.assertIsInstance(r["data"], dict)
        self.assertEqual(r["data"]["event_order"], 1)

        # ---- read_event null ----
        r = self._adapter.process(_mk("read_event", {"run_id": rid, "event_order": 999}))
        self.assertEqual(r["status"], "success")
        self.assertIsNone(r["data"])

        # ---- get_run projection ----
        r = self._adapter.process(_mk("get_run", {"run_id": rid}))
        self.assertEqual(r["status"], "success")
        self.assertIsInstance(r["data"], dict)
        self.assertEqual(r["data"]["run_id"], rid)

        # ---- get_stage status=completed ----
        r = self._adapter.process(_mk("get_stage", {"run_id": rid, "stage_id": "build"}))
        self.assertEqual(r["status"], "success", "get_stage failed")
        self.assertIsInstance(r["data"], dict)
        self.assertEqual(r["data"]["stage_id"], "build")
        self.assertEqual(r["data"]["status"], "completed")

        # ---- evidence_snapshot (events=5, receipts=5, head=5) ----
        r = self._adapter.process(_mk("evidence_snapshot", {"run_id": rid}))
        self.assertEqual(r["status"], "success", "evidence_snapshot failed")
        snapshot = r["data"]
        self.assertIsInstance(snapshot, dict)
        self.assertEqual(snapshot["run_id"], rid)
        self.assertIn("events", snapshot)
        self.assertIn("receipts", snapshot)
        self.assertEqual(len(snapshot["events"]), 5)
        self.assertEqual(len(snapshot["receipts"]), 5)
        self.assertIn("source_stream_head", snapshot)
        head = snapshot["source_stream_head"]
        self.assertIsInstance(head, dict)
        self.assertEqual(head["event_order"], 5)

        # ---- export_evidence success ----
        exp_id = f"export-{uuid.uuid4().hex}"
        r = self._adapter.process(_mk("export_evidence", {
            "run_id": rid,
            "export_id": exp_id,
            "exported_at": _det_clock(),
        }))
        self.assertEqual(r["status"], "success", "export_evidence failed")
        export = r["data"]
        self.assertIsInstance(export, dict)
        self.assertEqual(export["run_id"], rid)
        self.assertIn("export_content_digest", export)
        self.assertIn("projection", export)
        self.assertIn("projection_digest", export["projection"])
        self.assertIn("events", export)
        self.assertIn("provenance", export)
        self.assertEqual(len(export["events"]), 5)

        # ---- RuntimeArtifact in projection, snapshot, export ----
        proj_data = r["data"].get("projection", {}) if "projection" in export else {}
        proj_arts = proj_data.get("runtime_artifacts", [])
        self.assertTrue(
            any(a.get("artifact_ref", {}).get("artifact_id") == f"art-{rid}"
                for a in proj_arts if isinstance(a, dict)),
            f"RuntimeArtifact art-{rid} not found in projection runtime_artifacts",
        )

        snap_arts = []
        for ev in snapshot["events"]:
            payload = ev.get("event_payload", ev.get("payload", {}))
            arts = payload.get("artifacts_produced", [])
            snap_arts.extend(arts)
        self.assertTrue(
            any(a.get("artifact_ref", {}).get("artifact_id") == f"art-{rid}"
                for a in snap_arts if isinstance(a, dict)),
            f"RuntimeArtifact art-{rid} not found in snapshot events",
        )

        export_arts = []
        for ev in export["events"]:
            payload = ev.get("event_payload", ev.get("payload", {}))
            arts = payload.get("artifacts_produced", [])
            export_arts.extend(arts)
        self.assertTrue(
            any(a.get("artifact_ref", {}).get("artifact_id") == f"art-{rid}"
                for a in export_arts if isinstance(a, dict)),
            f"RuntimeArtifact art-{rid} not found in export events",
        )

        # Verify visibility preserved in artifact
        found_art = None
        for a in export_arts:
            if (isinstance(a, dict) and
                    a.get("artifact_ref", {}).get("artifact_id") == f"art-{rid}"):
                found_art = a
                break
        self.assertIsNotNone(found_art, "RuntimeArtifact not in export artifacts")
        self.assertEqual(found_art["visibility"], visibility)

        # ================================================================
        # Mandatory independent cryptographic verification
        # ================================================================

        events = self._sc.read_events(rid)
        self.assertEqual(len(events), 5)

        prev_digest = ZERO_DIGEST
        for idx, ev in enumerate(events):
            # -- 1. Independent content_digest --
            # Preimage: event minus content_digest and causation_id
            # (causation_id is normalized by the journal after digest
            #  computation and is not part of the original preimage)
            preimage = {k: v for k, v in ev.items()
                        if k not in ("content_digest", "causation_id")}
            expected_cd = _oracle_digest(preimage)
            self.assertEqual(
                ev["content_digest"], expected_cd,
                f"Event {ev['event_order']} content_digest mismatch",
            )

            # -- 2. prev_event_digest chain --
            self.assertEqual(
                ev["prev_event_digest"], prev_digest,
                f"Event {ev['event_order']} prev_digest chain broken",
            )
            prev_digest = ev["content_digest"]

            # -- 3. Receipt must exist and exactly one in snapshot --
            matching_receipts = [
                rec for rec in snapshot["receipts"]
                if rec["event_order"] == ev["event_order"]
            ]
            self.assertEqual(
                len(matching_receipts), 1,
                f"Event {ev['event_order']}: expected 1 receipt, "
                f"got {len(matching_receipts)}",
            )
            receipt = matching_receipts[0]

            # -- 4. Independent HMAC verification --
            sr = receipt["signed_receipt"]
            sp = sr["signed_payload"]
            expected_hmac = _oracle_hmac(TEST_SIGNER_KEY, sp)
            self.assertTrue(
                hmac.compare_digest(expected_hmac, sr["signature"]),
                f"Event {ev['event_order']} HMAC verification failed",
            )

        # -- 5. source_stream_head equals event 5 --
        self.assertIn("source_stream_head", snapshot)
        native_head = snapshot["source_stream_head"]
        self.assertEqual(native_head["event_order"], 5)
        self.assertEqual(native_head["content_digest"], events[4]["content_digest"])

        # -- 6. Independent projection_digest --
        projection = export["projection"]
        proj_preimage = {
            k: v for k, v in projection.items()
            if k not in ("projection_digest", "projection_id", "derived_at")
        }
        expected_pd = _oracle_digest(proj_preimage)
        self.assertEqual(
            expected_pd, projection.get("projection_digest", ""),
            "projection_digest mismatch",
        )

        # -- 7. Independent export_content_digest --
        export_preimage = {
            k: v for k, v in export.items()
            if k != "export_content_digest"
        }
        expected_ed = _oracle_digest(export_preimage)
        self.assertEqual(
            expected_ed, export["export_content_digest"],
            "export_content_digest mismatch",
        )

    def test_pipeline_public(self):
        self._run_pipeline("public", f"pub-{uuid.uuid4().hex[:6]}")

    def test_pipeline_project(self):
        self._run_pipeline("project", f"prj-{uuid.uuid4().hex[:6]}")

    def test_pipeline_restricted(self):
        self._run_pipeline("restricted", f"res-{uuid.uuid4().hex[:6]}")


# =============================================================================
# Strict Negative Tests
# =============================================================================

class E2EStrictNegatives(unittest.TestCase):
    """Every negative assertion expects a single contract-defined outcome."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="e2e-neg-")
        self._db = str(pathlib.Path(self._tmp) / "neg.db")
        self._sc = RuntimeStateSidecar(
            self._db, TEST_SIGNER_KEY, id_factory=_det_id, clock=_det_clock,
        )
        self._adapter = RuntimeAdapter(self._sc, ALL_FULL)

    def tearDown(self):
        self._sc.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_malformed_no_db_mutation(self):
        sha_before = _db_sha(self._db)
        r = self._adapter.process("not a dict")
        self.assertEqual(r["error"]["code"], "MALFORMED_ENVELOPE")
        self.assertEqual(_db_sha(self._db), sha_before)

    def test_unknown_operation(self):
        sha_before = _db_sha(self._db)
        r = self._adapter.process({
            "protocol_version": "1.0.0", "request_id": "r1", "operation": "bad",
            "payload": {"operation": "bad", "params": {}},
        })
        self.assertEqual(r["error"]["code"], "UNKNOWN_OPERATION")
        self.assertEqual(_db_sha(self._db), sha_before)

    def test_unavailable_capability(self):
        caps = list(ALL_FULL)
        caps[0] = {"capability": "create_run", "state": "unavailable",
                    "degradation_note": "na"}
        ad = RuntimeAdapter(self._sc, caps)
        r = ad.process(_mk("create_run", {"run_id": "r1"}))
        self.assertEqual(r["error"]["code"], "CAPABILITY_UNAVAILABLE")

    def test_degraded_scope_within(self):
        caps = list(ALL_FULL)
        caps[6] = {
            "capability": "evidence_snapshot", "state": "degraded_scope",
            "degradation_note": "t",
            "scope_constraints": {"param_allowlist": {"run_id": ["rx"]}},
        }
        payload = _run_created_payload("public")
        payload["run_provenance"]["origin_artifact"] = {"artifact_id": "T", "artifact_kind": "ticket"}
        payload["run_provenance"]["governing_contracts"] = [
            {"artifact_id": "c", "artifact_kind": "contract", "artifact_version": "1.0.0"},
        ]
        req = _make_request("run.created", payload, run_id="rx",
                            prev_digest=ZERO_DIGEST, head_order=0)
        self._sc.create_run(req)
        ad = RuntimeAdapter(self._sc, caps)
        r = ad.process(_mk("evidence_snapshot", {"run_id": "rx"}))
        self.assertEqual(r["status"], "success")

    def test_degraded_scope_out(self):
        caps = list(ALL_FULL)
        caps[6] = {
            "capability": "evidence_snapshot", "state": "degraded_scope",
            "degradation_note": "t",
            "scope_constraints": {"param_allowlist": {"run_id": ["rx"]}},
        }
        ad = RuntimeAdapter(self._sc, caps)
        r = ad.process(_mk("evidence_snapshot", {"run_id": "other"}))
        self.assertEqual(r["error"]["code"], "CAPABILITY_DEGRADED_SCOPE")

    def test_stale_head(self):
        """Stale head: adapter must succeed and sidecar reports stale_head code."""
        rid = f"stale-{uuid.uuid4().hex[:6]}"
        payload = _run_created_payload("public")
        payload["run_provenance"]["origin_artifact"] = {"artifact_id": "T", "artifact_kind": "ticket"}
        req = _make_request("run.created", payload, run_id=rid,
                            prev_digest=ZERO_DIGEST, head_order=0)
        self._sc.create_run(req)

        r = _make_request("run.started",
            {"started_at": _det_clock(), "executor_identity": "runner-1"},
            run_id=rid, prev_digest=ZERO_DIGEST, head_order=0)
        result = self._adapter.process(_mk("append_event", r))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["code"], "stale_head")

    def test_missing_run(self):
        """Missing run: adapter produces INTERNAL_ERROR with generic message."""
        r = self._adapter.process(_mk("get_run", {"run_id": "nx"}))
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["error"]["code"], "INTERNAL_ERROR")
        self.assertEqual(
            r["error"]["detail"]["internal"]["message"],
            "unexpected adapter error",
        )

    def test_missing_export_id(self):
        r = self._adapter.process(_mk("export_evidence", {
            "run_id": "r1", "exported_at": _det_clock(),
        }))
        self.assertEqual(r["error"]["code"], "INVALID_PAYLOAD")

    def test_missing_exported_at(self):
        r = self._adapter.process(_mk("export_evidence", {
            "run_id": "r1", "export_id": "export-0000",
        }))
        self.assertEqual(r["error"]["code"], "INVALID_PAYLOAD")

    def test_internal_error_generic_message(self):
        self._sc.close()
        r = self._adapter.process(_mk("get_run", {"run_id": "rx"}))
        self.assertEqual(r["status"], "error")
        # closed sidecar should produce INTERNAL_ERROR with generic message
        detail_str = json.dumps(r["error"]["detail"]).lower()
        for forbidden in ["c:", "\\\\", "/home", "token", "password", "secret",
                          "credential", "traceback"]:
            self.assertNotIn(forbidden, detail_str,
                             f"'{forbidden}' leaked in error detail")

    def test_no_hidden_retry(self):
        class SingleCallSC:
            count = 0
            def create_run(self, p):
                SingleCallSC.count += 1
                if SingleCallSC.count == 1:
                    raise RuntimeStateSidecarError("err", {"r": "first"})
                return {}
            def append_event(self, p): return {}
            def read_event(self, a, b): return {}
            def read_events(self, a): return []
            def get_run(self, a): return {}
            def get_stage(self, a, b): return {}
            def evidence_snapshot(self, a): return {}
            def export_evidence(self, a, **kw): return {}

        ad = RuntimeAdapter(SingleCallSC(), ALL_FULL)
        r = ad.process(_mk("create_run", {"run_id": "r1"}))
        self.assertEqual(r["error"]["code"], "DELEGATED_ERROR")
        self.assertEqual(SingleCallSC.count, 1)


# =============================================================================
# Read-Only DB Tests
# =============================================================================

class E2EReadOnlyDB(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="e2e-ro-")
        self._db = str(pathlib.Path(self._tmp) / "ro.db")
        self._sc = RuntimeStateSidecar(self._db, TEST_SIGNER_KEY,
                                        id_factory=_det_id, clock=_det_clock)
        self._adapter = RuntimeAdapter(self._sc, ALL_FULL)

    def tearDown(self):
        self._sc.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_read_ops_no_db_mutation(self):
        rid = f"ro-{uuid.uuid4().hex[:6]}"
        payload = _run_created_payload("public")
        payload["run_provenance"]["origin_artifact"] = {
            "artifact_id": "T", "artifact_kind": "ticket",
        }
        req = _make_request("run.created", payload, run_id=rid,
                            prev_digest=ZERO_DIGEST, head_order=0)
        self._sc.create_run(req)

        sha = _db_sha(self._db)
        for op, params in [
            ("read_event", {"run_id": rid, "event_order": 1}),
            ("read_events", {"run_id": rid}),
            ("get_run", {"run_id": rid}),
            ("evidence_snapshot", {"run_id": rid}),
        ]:
            self._adapter.process(_mk(op, params))
            self.assertEqual(_db_sha(self._db), sha, f"{op} mutated DB")

        r = self._adapter.process(_mk("export_evidence", {
            "run_id": rid,
            "export_id": f"export-{uuid.uuid4().hex}",
            "exported_at": _det_clock(),
        }))
        self.assertEqual(r["status"], "success",
                         "export_evidence failed")
        self.assertEqual(_db_sha(self._db), sha, "export_evidence mutated DB")


if __name__ == "__main__":
    unittest.main()
