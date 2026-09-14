"""Clean-workspace contract tests for the public v0.8 CI surface.

This module intentionally uses only the Python standard library. It copies the
complete product file set using the accepted exact-segment exclusion rule, then
runs the public artifact validator and all-scenario smoke command from that
copy. All writable state stays under a caller-owned temporary root.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
EXCLUDED_SEGMENTS = frozenset({
    ".agents", ".claude", ".codex", ".git", ".obsidian", ".pytest_cache",
    ".qwen", ".tmp", ".tmp_doc_review", ".workbuddy", ".workflow",
    "__pycache__", "node_modules",
})
FROZEN_HASHES = {
    "scripts/runtime_v080_regression.py": "0b7a2f98ea1a96a6e94d03ce199833e835fb61ec247be4f10e668b7f29db1e22",
    "scripts/runtime_v080_smoke.py": "121e718d66aeb026e8d0c2d13b5fb5b012eac80559dcb470c86f5514dab9b7b0",
    "scripts/validate_artifacts.py": "8a5d9b6d43b89dd3d91e08561ca393bb2bef4657c2143f8af7dd591c732efdc6",
    "requirements-mcp.txt": "cf7c83d709c498f04eeb3006aed80ee75ecbe0dd5502d88835e2ff6edea491c2",
    "assets/schemas/runtime-v080-staging-manifest-v2.schema.json": "9b158df1344d2f83df64f839c86842f3e3c14f9ffe839cc55505fbda4b0633dd",
    "examples/runtime_v080_staging_manifest/conformance-v2.json": "7a2988f56935765b5c4a98a986a289cb55166b6f065dda8f60e8352e94b7343f",
}
FROZEN_LF_HASHES = {
    # .gitattributes declares this text artifact with eol=lf.  Freeze the
    # Git-authoritative LF bytes so Windows working-tree checkout conversion
    # cannot make the public CI contract platform-dependent.
    "examples/runtime_v080_smoke/conformance.json": "97916177a5424cb0c06eb157a610c87a9040ab381873b7fed72a2795b4748839",
}
CORE_TEST_REQUIREMENTS = "jsonschema>=4.18,<5\nreferencing>=0.30,<1\n"


def _is_product_path(relative_path: pathlib.PurePosixPath) -> bool:
    return not any(part in EXCLUDED_SEGMENTS for part in relative_path.parts)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lf_normalized_sha256(path: pathlib.Path) -> str:
    """Hash a text=auto eol=lf authority independently of checkout EOLs."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _manifest(root: pathlib.Path) -> dict[str, tuple[int, str]]:
    entries: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = pathlib.PurePosixPath(path.relative_to(root).as_posix())
        if _is_product_path(relative):
            entries[relative.as_posix()] = (path.stat().st_size, _sha256(path))
    return entries


def _copy_product_tree(source: pathlib.Path, destination: pathlib.Path) -> None:
    for path in sorted(source.rglob("*")):
        relative = pathlib.PurePosixPath(path.relative_to(source).as_posix())
        if not _is_product_path(relative):
            continue
        target = destination.joinpath(*relative.parts)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def _source_transient_state(root: pathlib.Path) -> set[str]:
    findings: set[str] = set()
    for current, directories, files in os.walk(root):
        current_path = pathlib.Path(current)
        relative_parts = current_path.relative_to(root).parts
        retained_directories = []
        for name in directories:
            relative = pathlib.PurePosixPath(*relative_parts, name).as_posix()
            if name in EXCLUDED_SEGMENTS:
                findings.add(relative + "/")
            else:
                retained_directories.append(name)
        directories[:] = retained_directories
        for name in files:
            if name.endswith((".pyc", ".pyo", ".tmp", ".report")):
                findings.add(pathlib.PurePosixPath(*relative_parts, name).as_posix())
    return findings


def _source_workflow_state(root: pathlib.Path) -> dict[str, tuple[int, str] | None]:
    """Capture state which the MCP probe must never create or modify in Source."""
    findings: dict[str, tuple[int, str] | None] = {}
    sensitive_names = {".workflow", ".railyard-workflow.json", "__pycache__"}
    for path in sorted(root.rglob("*")):
        relative = pathlib.PurePosixPath(path.relative_to(root).as_posix())
        if not (set(relative.parts) & sensitive_names or path.suffix in {".pyc", ".pyo"}):
            continue
        findings[relative.as_posix()] = (_sha256(path) and path.stat().st_size, _sha256(path)) if path.is_file() else None
    return findings


def _run(command: list[str], cwd: pathlib.Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(cwd), env=env, text=True, capture_output=True, timeout=180)


def _load_regression_module():
    spec = importlib.util.spec_from_file_location(
        "runtime_v080_regression_ci_probe", ROOT / "scripts" / "runtime_v080_regression.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_mcp_probe_module():
    spec = importlib.util.spec_from_file_location(
        "probe_railyard_mcp_server_ci_probe", ROOT / "scripts" / "probe_railyard_mcp_server.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    scripts_directory = str(ROOT / "scripts")
    sys.path.insert(0, scripts_directory)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class RuntimeV080CIContractTests(unittest.TestCase):
    def test_workflow_declares_the_portable_offline_public_gate(self) -> None:
        raw = (ROOT / ".github" / "workflows" / "railyard-validate.yml").read_bytes()
        text = raw.decode("utf-8")
        for value in ("pull_request:", "push:", "contents: read", "fail-fast: false",
                      "ubuntu-latest", "windows-latest", "PYTHONDONTWRITEBYTECODE",
                      "PYTHONPYCACHEPREFIX", "python -m compileall -q scripts",
                      "python scripts/validate_artifacts.py --project-root .",
                      "python scripts/test_runtime_v080_ci.py",
                      "python scripts/runtime_v080_regression.py",
                      "python scripts/runtime_v080_smoke.py --tmp-dir", "--all run"):
            self.assertIn(value, text)

        lines = text.splitlines()
        strategy_start = lines.index("    strategy:")
        env_start = lines.index("    env:", strategy_start)
        strategy_lines = lines[strategy_start:env_start]
        matrix_start = strategy_lines.index("      matrix:")
        matrix_lines = strategy_lines[matrix_start + 1:]
        matrix_values: dict[str, list[str]] = {}
        active_key: str | None = None
        for line in matrix_lines:
            if line.startswith("        ") and line.endswith(":") and not line.startswith("          "):
                active_key = line.strip()[:-1]
                matrix_values[active_key] = []
            elif line.startswith("          - ") and active_key is not None:
                matrix_values[active_key].append(line.split("- ", 1)[1].strip().strip('"'))
            elif line.strip():
                self.fail(f"unexpected core matrix declaration: {line!r}")

        expected_operating_systems = ["ubuntu-latest", "windows-latest"]
        expected_python_versions = ["3.10", "3.11", "3.12", "3.13", "3.14"]
        self.assertEqual(set(matrix_values), {"os", "python-version"})
        self.assertEqual(matrix_values["os"], expected_operating_systems)
        self.assertEqual(matrix_values["python-version"], expected_python_versions)
        hosted_cells = {
            (operating_system, python_version)
            for operating_system in matrix_values["os"]
            for python_version in matrix_values["python-version"]
        }
        self.assertEqual(len(hosted_cells), 10)
        self.assertEqual(
            hosted_cells,
            {
                (operating_system, python_version)
                for operating_system in expected_operating_systems
                for python_version in expected_python_versions
            },
        )
        strategy_text = "\n".join(strategy_lines)
        for forbidden in ("include:", "exclude:", "continue-on-error", "allow-failure", "allowed-failure"):
            self.assertNotIn(forbidden, strategy_text)
        self.assertIn("name: Public v0.8 validation (${{ matrix.os }}, Python ${{ matrix.python-version }})", text)
        self.assertIn("python-version: ${{ matrix.python-version }}", text)
        self.assertNotIn('python-version: "3.12"', text)
        self.assertNotIn("mkdir -p", text)
        self.assertNotIn(".tmp/", text)
        self.assertNotIn("--db .workflow", text)
        self.assertNotIn("Railyard-Control", text)
        self.assertLess(
            text.index("python scripts/runtime_v080_regression.py"),
            text.index("python scripts/runtime_v080_smoke.py --tmp-dir"),
        )
        core_install = "run: python -m pip install -r requirements-test.txt"
        self.assertEqual(text.count(core_install), 1)
        setup_start = text.index("- name: Set up Python")
        setup_end = text.index("- name:", setup_start + 1)
        core_start = text.index("- name: Install core test dependencies")
        core_end = text.index("- name:", core_start + 1)
        compile_start = text.index("- name: Compile public scripts")
        self.assertEqual(setup_end, core_start)
        self.assertEqual(core_end, compile_start)
        self.assertIn(core_install, text[core_start:core_end])
        self.assertNotIn("if:", text[core_start:core_end])
        self.assertNotIn("continue-on-error", text[core_start:core_end])
        self.assertNotIn("requirements-mcp", text[core_start:core_end])

        optional_start = text.index("# Optional MCP-lite coverage")
        optional_gate = text[optional_start:]
        self.assertGreater(optional_start, text.index("python scripts/runtime_v080_smoke.py --tmp-dir"))
        optional_install_start = text.index("- name: Install optional MCP-lite dependencies")
        optional_install_end = text.index("- name:", optional_install_start + 1)
        optional_probe_start = optional_install_end
        optional_probe_end = len(text)
        optional_initialize_start = text.index("- name: Initialize optional MCP-lite probe database")
        optional_initialize_end = text.index("- name:", optional_initialize_start + 1)
        self.assertIn("--initialize-test-db", text[optional_initialize_start:optional_initialize_end])
        self.assertIn("--temp-root \"${{ runner.temp }}/railyard-ci-mcp\"", text[optional_initialize_start:optional_initialize_end])
        self.assertIn("--db \"${{ runner.temp }}/railyard-ci-mcp/workflow.db\"", text[optional_initialize_start:optional_initialize_end])
        self.assertIn("if: hashFiles('requirements-mcp.txt') != ''", text[optional_install_start:optional_install_end])
        self.assertNotIn("continue-on-error", text[optional_install_start:optional_install_end])
        self.assertIn("run: python -m pip install -r requirements-mcp.txt", text[optional_install_start:optional_install_end])
        self.assertIn("if: hashFiles('requirements-mcp.txt') != ''", text[optional_probe_start:optional_probe_end])
        self.assertNotIn("continue-on-error", text[optional_probe_start:optional_probe_end])
        self.assertIn("run: python scripts/probe_railyard_mcp_server.py", text[optional_probe_start:optional_probe_end])
        self.assertIn("--temp-root \"${{ runner.temp }}/railyard-ci-mcp\"", text[optional_probe_start:optional_probe_end])
        self.assertIn("--db \"${{ runner.temp }}/railyard-ci-mcp/workflow.db\"", text[optional_probe_start:optional_probe_end])
        self.assertNotIn("continue-on-error", optional_gate)
        self.assertNotIn("requirements-test.txt", optional_gate)

    def test_mcp_probe_initialization_is_isolated_and_fail_closed(self) -> None:
        probe = _load_mcp_probe_module()
        source_state_before = _source_workflow_state(ROOT)
        source_transient_before = _source_transient_state(ROOT)
        with tempfile.TemporaryDirectory(prefix="railyard mcp probe isolation ") as temporary:
            temporary_root = pathlib.Path(temporary)
            isolated_root = temporary_root / "isolated root"
            database = isolated_root / "fixture" / "workflow.db"
            initialized = probe.initialize_test_db(isolated_root, database, ROOT)
            self.assertEqual(initialized["mode"], "initialize-test-db")
            self.assertTrue(database.is_file())

            with self.assertRaises(probe.ProbeFailure):
                probe.initialize_test_db(isolated_root, database, ROOT)
            with self.assertRaises(probe.ProbeFailure):
                probe.initialize_test_db(isolated_root, temporary_root / "outside.db", ROOT)
            with self.assertRaises(probe.ProbeFailure):
                probe.initialize_test_db(ROOT / "probe-state", ROOT / "probe-state" / "workflow.db", ROOT)

            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONPYCACHEPREFIX"] = str(temporary_root / "python cache")
            initialization_failure = _run(
                [sys.executable, "scripts/probe_railyard_mcp_server.py", "--initialize-test-db",
                 "--temp-root", str(isolated_root), "--db", str(database), "--project-root", str(ROOT)],
                ROOT, environment,
            )
            self.assertNotEqual(initialization_failure.returncode, 0)
            probe_failure = _run(
                [sys.executable, "scripts/probe_railyard_mcp_server.py", "--temp-root", str(isolated_root),
                 "--db", str(isolated_root / "missing.db"), "--project-root", str(ROOT)],
                ROOT, environment,
            )
            self.assertNotEqual(probe_failure.returncode, 0)
        self.assertEqual(_source_workflow_state(ROOT), source_state_before)
        self.assertEqual(_source_transient_state(ROOT), source_transient_before)

    def test_core_test_requirements_manifest_is_exact(self) -> None:
        requirements = ROOT / "requirements-test.txt"
        self.assertTrue(requirements.is_file())
        self.assertEqual(requirements.read_text(encoding="utf-8"), CORE_TEST_REQUIREMENTS)

    def test_regression_manifest_and_sidecar_invocation_are_exact(self) -> None:
        regression = _load_regression_module()
        self.assertEqual(regression.discover_release_tests(ROOT), regression.RELEASE_TEST_FILES)
        mode, command = regression._command_for(
            "scripts/test_runtime_state_sidecar.py", regression.MODULE_INVOCATIONS)
        self.assertEqual(mode, "unittest_module")
        self.assertEqual(
            command,
            [sys.executable, "-m", "unittest", "scripts.test_runtime_state_sidecar"],
        )
        for relative in regression.RELEASE_TEST_FILES:
            if relative != "scripts/test_runtime_state_sidecar.py":
                self.assertEqual(
                    regression._command_for(relative, regression.MODULE_INVOCATIONS)[0], "script")

    def test_regression_fails_closed_and_keeps_its_source_clean(self) -> None:
        regression = _load_regression_module()
        with tempfile.TemporaryDirectory(prefix="railyard regression contract ") as temporary:
            temporary_root = pathlib.Path(temporary)

            def make_product(name: str, filename: str, source: str) -> pathlib.Path:
                product = temporary_root / name
                scripts = product / "scripts"
                scripts.mkdir(parents=True)
                (scripts / filename).write_text(source, encoding="utf-8")
                return product

            passing = make_product(
                "passing", "test_passing.py",
                "import unittest\n"
                "class Passing(unittest.TestCase):\n"
                "    def test_pass(self): self.assertTrue(True)\n"
                "if __name__ == '__main__': unittest.main()\n",
            )
            passed = regression.run_regression(
                root=passing,
                manifest=("scripts/test_passing.py",),
                module_invocations={},
                tmp_dir=temporary_root / "passing workspace",
            )
            self.assertEqual(passed["total_tests"], 1)
            self.assertEqual(passed["failed_files"], 0)
            self.assertTrue(passed["source_unchanged"])
            self.assertFalse((passing / "__pycache__").exists())

            mismatch = make_product("mismatch", "test_actual.py", "raise SystemExit(0)\n")
            mismatched = regression.run_regression(
                root=mismatch,
                manifest=("scripts/test_expected.py",),
                module_invocations={},
                tmp_dir=temporary_root / "mismatch workspace",
            )
            self.assertFalse(mismatched["census_match"])
            self.assertEqual(mismatched["missing_files"], ["scripts/test_expected.py"])
            self.assertEqual(mismatched["extra_files"], ["scripts/test_actual.py"])

            zero = make_product(
                "zero", "test_zero.py", "import unittest\nunittest.main()\n")
            zero_result = regression.run_regression(
                root=zero,
                manifest=("scripts/test_zero.py",),
                module_invocations={},
                tmp_dir=temporary_root / "zero workspace",
            )
            self.assertEqual(zero_result["ledger"][0]["test_count"], 0)
            self.assertEqual(zero_result["failed_files"], 1)

            failing = make_product("failing", "test_failing.py", "raise SystemExit(7)\n")
            failed = regression.run_regression(
                root=failing,
                manifest=("scripts/test_failing.py",),
                module_invocations={},
                tmp_dir=temporary_root / "failing workspace",
            )
            self.assertEqual(failed["ledger"][0]["exit_code"], 7)
            self.assertEqual(failed["failed_files"], 1)

    def test_frozen_public_authorities_are_unchanged(self) -> None:
        for relative, expected in FROZEN_HASHES.items():
            self.assertEqual(_sha256(ROOT / relative), expected, relative)
        for relative, expected in FROZEN_LF_HASHES.items():
            self.assertEqual(_lf_normalized_sha256(ROOT / relative), expected, relative)

    def test_lf_frozen_conformance_hash_rejects_content_tampering(self) -> None:
        """EOL conversion is neutral, while a semantic catalog mutation is not."""
        source = ROOT / "examples" / "runtime_v080_smoke" / "conformance.json"
        expected = FROZEN_LF_HASHES["examples/runtime_v080_smoke/conformance.json"]
        with tempfile.TemporaryDirectory(prefix="railyard conformance authority ") as temporary:
            candidate = pathlib.Path(temporary) / "conformance.json"
            tampered = source.read_bytes().replace(
                b'"scenario_count": 20', b'"scenario_count": 21', 1)
            self.assertNotEqual(tampered, source.read_bytes())
            candidate.write_bytes(tampered)
            self.assertNotEqual(_lf_normalized_sha256(candidate), expected)

    def test_failing_smoke_aggregate_cannot_report_success(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "runtime_v080_smoke_ci_probe", ROOT / "scripts" / "runtime_v080_smoke.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        catalog = json.loads(
            (ROOT / "examples" / "runtime_v080_smoke" / "conformance.json").read_text(encoding="utf-8"))
        failed_results = [
            {"scenario_id": f"v080-scenario-{index:03d}",
             "scenario_status": "fail" if index == 1 else "pass"}
            for index in range(1, 21)
        ]
        self.assertNotEqual(module._all_mode_exit_code(failed_results, catalog), 0)

    def test_clean_copy_executes_public_validation_without_source_pollution(self) -> None:
        before = _manifest(ROOT)
        transient_before = _source_transient_state(ROOT)
        workflow_state_before = _source_workflow_state(ROOT)
        with tempfile.TemporaryDirectory(prefix="railyard v080 ci ") as temporary:
            temporary_root = pathlib.Path(temporary)
            clean_root = temporary_root / "clean product tree"
            smoke_root = temporary_root / "caller smoke workspace"
            cache_root = temporary_root / "python cache"
            _copy_product_tree(ROOT, clean_root)
            self.assertEqual(_manifest(clean_root), before)
            for excluded in EXCLUDED_SEGMENTS:
                self.assertFalse((clean_root / excluded).exists(), excluded)
            self.assertFalse((clean_root / "Railyard-Control").exists())
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONPYCACHEPREFIX"] = str(cache_root)
            validation = _run(
                [sys.executable, "scripts/validate_artifacts.py", "--project-root", "."],
                clean_root, environment,
            )
            self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
            smoke = _run(
                [sys.executable, "scripts/runtime_v080_smoke.py", "--tmp-dir", str(smoke_root), "--all", "run"],
                clean_root, environment,
            )
            self.assertEqual(smoke.returncode, 0, smoke.stdout + smoke.stderr)
            payload = json.loads(smoke.stdout)
            catalog = json.loads((clean_root / "examples" / "runtime_v080_smoke" / "conformance.json").read_text(encoding="utf-8"))
            expected_count = len(catalog["scenarios"])
            self.assertGreater(expected_count, 0)
            self.assertEqual(catalog.get("scenario_count"), expected_count)
            self.assertEqual(expected_count, 20)
            self.assertEqual(payload["total"], expected_count)
            self.assertEqual(payload["passed"], 20)
            self.assertEqual(payload["failed"], 0)
            self.assertEqual(len(payload["results"]), expected_count)
            self.assertTrue(all(item["scenario_status"] == "pass" for item in payload["results"]))
            self.assertTrue(smoke_root.is_dir())
            self.assertTrue(all(path.is_relative_to(smoke_root) for path in smoke_root.rglob("*")))
        self.assertEqual(_manifest(ROOT), before)
        self.assertEqual(_source_transient_state(ROOT), transient_before)
        self.assertEqual(_source_workflow_state(ROOT), workflow_state_before)


if __name__ == "__main__":
    unittest.main()
