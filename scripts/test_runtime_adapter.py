"""Implementation-remediation tests for runtime_adapter.py v1.0.0.

Covers:
- All 72 conformance fixtures with Draft202012Validator
- Seven runtime-decision branches against RuntimeAdapter
- Adversarial: dict/list subclass with hostile hooks, allows duplicates, property facade, path/secret/url/token in exceptions
- Constructor schema mirror, scope equality, response types
- Exact single delegation, input non-mutation
- Five runtime regression suites
"""

from __future__ import annotations

import copy, json, pathlib, re, subprocess, sys, tempfile, unittest, uuid
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.runtime_adapter import RuntimeAdapter, _FROZEN_OPERATIONS
from scripts.runtime_state_sidecar import RuntimeStateSidecar, RuntimeStateSidecarError
from scripts.runtime_state_core import ZERO_DIGEST

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFORMANCE_PATH = ROOT / "examples" / "runtime_adapter_contract" / "conformance.json"
TEST_SIGNER_KEY = b"runtime-adapter-conformance-key-32b!"

ALL = [{"capability": op, "state": "full"} for op in _FROZEN_OPERATIONS]


def _mk(op, params, rid="p1"):
    return {"protocol_version": "1.0.0", "request_id": rid, "operation": op,
            "payload": {"operation": op, "params": params}}


def _ns():
    tmp = tempfile.mkdtemp(prefix="rat-")
    sc = RuntimeStateSidecar(str(pathlib.Path(tmp) / "t.db"), TEST_SIGNER_KEY)
    sc._tmp = tmp
    return sc


def _ts(s):
    s.close()
    if hasattr(s, "_tmp"):
        import shutil; shutil.rmtree(s._tmp, ignore_errors=True)


def _cr(s, rid=None):
    from scripts.test_runtime_state_journal import _make_request as _j, _run_created_payload
    r = rid or f"run-{uuid.uuid4()}"
    p = _run_created_payload("public")
    p["run_provenance"]["origin_artifact"] = {"artifact_id": "T", "artifact_kind": "ticket"}
    p["run_provenance"]["governing_contracts"] = [{"artifact_id": "c", "artifact_kind": "contract", "artifact_version": "1.0.0"}]
    s.create_run(_j("run.created", p, run_id=r, prev_digest=ZERO_DIGEST, head_order=0))
    return r


# ===========================================================================
# 1. Adversarial Tests
# ===========================================================================

class AdversarialTests(unittest.TestCase):
    def setUp(self):
        self.sc = _ns()

    def tearDown(self):
        _ts(self.sc)

    # 1a. dict subclass whose items() raises
    def test_dict_subclass_items_raises(self):
        class BadDict(dict):
            def items(self):
                raise RuntimeError("items() executed!")
        r = RuntimeAdapter(self.sc, ALL).process(BadDict({"protocol_version": "1.0.0"}))
        self.assertEqual(r["error"]["code"], "MALFORMED_ENVELOPE")

    def test_dict_subclass_iter_raises(self):
        class BadDict(dict):
            def __iter__(self):
                raise RuntimeError("__iter__ executed!")
        r = RuntimeAdapter(self.sc, ALL).process(BadDict({"protocol_version": "1.0.0"}))
        self.assertEqual(r["error"]["code"], "MALFORMED_ENVELOPE")

    # 1b. list subclass whose __iter__() raises
    def test_list_subclass_iter_raises(self):
        class BadList(list):
            def __iter__(self):
                raise RuntimeError("list __iter__ executed!")
        # bad_list as top-level request (not dict)
        r = RuntimeAdapter(self.sc, ALL).process(BadList([1, 2]))
        self.assertEqual(r["error"]["code"], "MALFORMED_ENVELOPE")

    # 1c. tuple rejected
    def test_tuple_rejected(self):
        r = RuntimeAdapter(self.sc, ALL).process((1, 2))
        self.assertEqual(r["error"]["code"], "MALFORMED_ENVELOPE")

    # 1d. nested tuple in list rejected
    def test_nested_tuple_rejected(self):
        r = RuntimeAdapter(self.sc, ALL).process({"key": (1, 2)})
        self.assertEqual(r["error"]["code"], "MALFORMED_ENVELOPE")

    # 2a. capability list subclass with hostile hooks
    def test_capability_list_subclass_rejected(self):
        class BadList(list):
            def __iter__(self):
                raise RuntimeError("cap list iter!")
        with self.assertRaises((TypeError, ValueError)):
            RuntimeAdapter(self.sc, BadList(ALL))

    # 2b. capability dict subclass with hostile hooks
    def test_capability_dict_subclass_rejected(self):
        class BadDict(dict):
            def keys(self):
                raise RuntimeError("cap dict keys!")
        caps = [BadDict({"capability": op, "state": "full"}) for op in _FROZEN_OPERATIONS]
        with self.assertRaises((TypeError, ValueError)):
            RuntimeAdapter(self.sc, caps)

    # 3a. facade property whose getter raises
    def test_facade_property_raises_not_executed(self):
        class PropFacade:
            @property
            def create_run(self):
                raise RuntimeError("property accessed!")
        # Constructor must not execute the property
        try:
            RuntimeAdapter(PropFacade(), ALL)
        except TypeError:
            pass  # Expected: property not callable

    # 3b. facade with __getattr__ that raises not executed
    def test_facade_getattr_raises_not_executed(self):
        class GetattrFacade:
            def __getattr__(self, name):
                raise RuntimeError(f"__getattr__ called for {name}")
        try:
            RuntimeAdapter(GetattrFacade(), ALL)
        except TypeError:
            pass  # Expected: no method found via MRO

    # 4a. allowlist [1, 1.0] rejected as duplicates
    def test_numeric_duplicate_rejected(self):
        caps = list(ALL)
        caps[6] = {"capability": "evidence_snapshot", "state": "degraded_scope",
                    "degradation_note": "test",
                    "scope_constraints": {"param_allowlist": {"run_id": [1, 1.0]}}}
        with self.assertRaises(ValueError):
            RuntimeAdapter(self.sc, caps)

    # 4b. allowlist [True, 1] accepted (bool distinct from number)
    def test_bool_number_distinct_allowed(self):
        caps = list(ALL)
        caps[6] = {"capability": "evidence_snapshot", "state": "degraded_scope",
                    "degradation_note": "test",
                    "scope_constraints": {"param_allowlist": {"debug": [True, 1]}}}
        ad = RuntimeAdapter(self.sc, caps)
        self.assertIsNotNone(ad)

    # 5a. Windows path not in INTERNAL_ERROR
    def test_windows_path_absent(self):
        class WinErr:
            def create_run(self, p): raise RuntimeError("C:\\Users\\admin\\secret.txt")
            def append_event(self, p): return {}
            def read_event(self, a, b): return {}
            def read_events(self, a): return []
            def get_run(self, a): return {}
            def get_stage(self, a, b): return {}
            def evidence_snapshot(self, a): return {}
            def export_evidence(self, a, export_id=None, exported_at=None): return {}
        r = RuntimeAdapter(WinErr(), ALL).process(_mk("create_run", {"run_id": "x"}))
        detail = json.dumps(r["error"]["detail"])
        self.assertNotIn("C:", detail)
        self.assertNotIn("secret", detail)

    # 5b. POSIX path not in INTERNAL_ERROR
    def test_posix_path_absent(self):
        class PosixErr:
            def create_run(self, p): raise RuntimeError("/home/user/config.yaml")
            def append_event(self, p): return {}
            def read_event(self, a, b): return {}
            def read_events(self, a): return []
            def get_run(self, a): return {}
            def get_stage(self, a, b): return {}
            def evidence_snapshot(self, a): return {}
            def export_evidence(self, a, export_id=None, exported_at=None): return {}
        r = RuntimeAdapter(PosixErr(), ALL).process(_mk("create_run", {"run_id": "x"}))
        detail = json.dumps(r["error"]["detail"])
        self.assertNotIn("/home", detail)

    # 5c. URL not in INTERNAL_ERROR
    def test_url_absent(self):
        class UrlErr:
            def create_run(self, p): raise RuntimeError("failed: https://evil.com/steal?token=abc123")
            def append_event(self, p): return {}
            def read_event(self, a, b): return {}
            def read_events(self, a): return []
            def get_run(self, a): return {}
            def get_stage(self, a, b): return {}
            def evidence_snapshot(self, a): return {}
            def export_evidence(self, a, export_id=None, exported_at=None): return {}
        r = RuntimeAdapter(UrlErr(), ALL).process(_mk("create_run", {"run_id": "x"}))
        detail = json.dumps(r["error"]["detail"])
        self.assertNotIn("evil.com", detail)
        self.assertNotIn("token", detail)

    # 5d. password=/secret=/credential= not in INTERNAL_ERROR
    def test_credentials_absent(self):
        class CredErr:
            def create_run(self, p): raise RuntimeError("auth failed: password=admin123 secret=s3cret credential=xyz")
            def append_event(self, p): return {}
            def read_event(self, a, b): return {}
            def read_events(self, a): return []
            def get_run(self, a): return {}
            def get_stage(self, a, b): return {}
            def evidence_snapshot(self, a): return {}
            def export_evidence(self, a, export_id=None, exported_at=None): return {}
        r = RuntimeAdapter(CredErr(), ALL).process(_mk("create_run", {"run_id": "x"}))
        detail = json.dumps(r["error"]["detail"])
        self.assertNotIn("password", detail)
        self.assertNotIn("secret", detail)
        self.assertNotIn("credential", detail)

    # 5e. INTERNAL_ERROR uses generic message
    def test_internal_error_generic_message(self):
        class GenErr:
            def create_run(self, p): raise RuntimeError("specific diagnostic info")
            def append_event(self, p): return {}
            def read_event(self, a, b): return {}
            def read_events(self, a): return []
            def get_run(self, a): return {}
            def get_stage(self, a, b): return {}
            def evidence_snapshot(self, a): return {}
            def export_evidence(self, a, export_id=None, exported_at=None): return {}
        r = RuntimeAdapter(GenErr(), ALL).process(_mk("create_run", {"run_id": "x"}))
        internal = r["error"]["detail"]["internal"]
        self.assertEqual(internal["message"], "unexpected adapter error")
        self.assertNotIn("diagnostic", internal["message"])


# ===========================================================================
# 2. Conformance Fixtures
# ===========================================================================

class ConformanceFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = json.loads(CONFORMANCE_PATH.read_text(encoding="utf-8"))
        cls._data = [f for f in cls.fixtures["fixtures"] if "_section" not in f and f.get("data") is not None]

    def test_fixture_count_matches_declared(self):
        c = self.fixtures.get("counts", {})
        expected = c.get("positive", 0) + c.get("schema_invalid_negative", 0) + c.get("runtime_decision", 0)
        self.assertEqual(len(self._data), expected)

    def test_all_fixtures_schema_validated(self):
        from jsonschema import Draft202012Validator
        s = json.loads((ROOT / "assets" / "schemas" / "runtime-adapter-v1.schema.json").read_text(encoding="utf-8"))
        failed = 0
        for f in self._data:
            errs = list(Draft202012Validator(s).iter_errors(f["data"]))
            ok = (len(errs) == 0) == f.get("conforms", True)
            if not ok:
                failed += 1
        self.assertEqual(failed, 0, f"{failed} fixture schema failures")


# ===========================================================================
# 3. Runtime Decision Branches
# ===========================================================================

class RuntimeDecisionTests(unittest.TestCase):
    def setUp(self):
        self.sc = _ns()
        self.adapter = RuntimeAdapter(self.sc, ALL)

    def tearDown(self):
        _ts(self.sc)

    def test_capability_unavailable(self):
        caps = list(ALL)
        caps[0] = {"capability": "create_run", "state": "unavailable", "degradation_note": "disabled"}
        r = RuntimeAdapter(self.sc, caps).process(_mk("create_run", {"run_id": "r1"}))
        self.assertEqual(r["error"]["code"], "CAPABILITY_UNAVAILABLE")

    def test_degraded_scope_within(self):
        caps = list(ALL)
        caps[6] = {"capability": "evidence_snapshot", "state": "degraded_scope",
                    "degradation_note": "t", "scope_constraints": {"param_allowlist": {"run_id": ["rx"]}}}
        _cr(self.sc, "rx")
        r = RuntimeAdapter(self.sc, caps).process(_mk("evidence_snapshot", {"run_id": "rx"}))
        self.assertNotEqual(r.get("error", {}).get("code"), "CAPABILITY_DEGRADED_SCOPE")

    def test_degraded_scope_out(self):
        caps = list(ALL)
        caps[6] = {"capability": "evidence_snapshot", "state": "degraded_scope",
                    "degradation_note": "t", "scope_constraints": {"param_allowlist": {"run_id": ["rx"]}}}
        r = RuntimeAdapter(self.sc, caps).process(_mk("evidence_snapshot", {"run_id": "other"}))
        self.assertEqual(r["error"]["code"], "CAPABILITY_DEGRADED_SCOPE")

    def test_delegated_error(self):
        self.sc.close()
        r = self.adapter.process(_mk("create_run", {"run_id": "r1"}))
        self.assertEqual(r["error"]["code"], "DELEGATED_ERROR")

    def test_internal_error(self):
        class Crash:
            def create_run(self, p): raise RuntimeError("x")
            def append_event(self, p): return {}
            def read_event(self, a, b): return {}
            def read_events(self, a): return []
            def get_run(self, a): return {}
            def get_stage(self, a, b): return {}
            def evidence_snapshot(self, a): return {}
            def export_evidence(self, a, **kw): return {}
        r = RuntimeAdapter(Crash(), ALL).process(_mk("create_run", {"run_id": "r1"}))
        self.assertEqual(r["error"]["code"], "INTERNAL_ERROR")

    def test_read_event_null(self):
        rid = _cr(self.sc)
        r = self.adapter.process(_mk("read_event", {"run_id": rid, "event_order": 999}))
        self.assertEqual(r["status"], "success")
        self.assertIsNone(r["data"])

    def test_read_events_array(self):
        rid = _cr(self.sc)
        r = self.adapter.process(_mk("read_events", {"run_id": rid}))
        self.assertIsInstance(r["data"], list)


# ===========================================================================
# 4. Constructor & Single Delegation
# ===========================================================================

class ConstructorTests(unittest.TestCase):
    def setUp(self):
        self.sc = _ns()

    def tearDown(self):
        _ts(self.sc)

    def test_type_not_list(self):
        with self.assertRaises(TypeError):
            RuntimeAdapter(self.sc, None)

    def test_exactly_eight(self):
        with self.assertRaises(ValueError):
            RuntimeAdapter(self.sc, ALL[:7])

    def test_duck_typing(self):
        class Duck:
            def create_run(self, p): return {}
            def append_event(self, p): return {}
            def read_event(self, a, b): return {}
            def read_events(self, a): return []
            def get_run(self, a): return {}
            def get_stage(self, a, b): return {}
            def evidence_snapshot(self, a): return {}
            def export_evidence(self, a, export_id=None, exported_at=None): return {}
        ad = RuntimeAdapter(Duck(), ALL)
        r = ad.process(_mk("read_events", {"run_id": "x"}))
        self.assertEqual(r["status"], "success")

    def test_single_delegation(self):
        class Count:
            n = 0
            def create_run(self, p): Count.n += 1; return {}
            def append_event(self, p): return {}
            def read_event(self, a, b): return {}
            def read_events(self, a): return []
            def get_run(self, a): return {}
            def get_stage(self, a, b): return {}
            def evidence_snapshot(self, a): return {}
            def export_evidence(self, a, **kw): return {}
        RuntimeAdapter(Count(), ALL).process(_mk("create_run", {"run_id": "r1"}))
        self.assertEqual(Count.n, 1)

    def test_non_mutation(self):
        orig = {"protocol_version": "1.0.0", "request_id": "r1", "operation": "create_run",
                "payload": {"operation": "create_run", "params": {"run_id": "rx"}}}
        snap = json.dumps(orig)
        RuntimeAdapter(self.sc, ALL).process(orig)
        self.assertEqual(json.dumps(orig), snap)


# ===========================================================================
# 5. Regression Suites
# ===========================================================================

class RegressionTests(unittest.TestCase):
    def _run(self, mod):
        r = subprocess.run([sys.executable, "-m", "unittest", mod], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, f"{mod}: {r.stderr[:300]}")

    def test_core(self): self._run("scripts.test_runtime_state_core")
    def test_sidecar(self): self._run("scripts.test_runtime_state_sidecar")
    def test_journal(self): self._run("scripts.test_runtime_state_journal")
    def test_evidence(self): self._run("scripts.test_runtime_evidence_export_contract")
    def test_projection(self): self._run("scripts.test_runtime_state_projection")


if __name__ == "__main__":
    unittest.main()
