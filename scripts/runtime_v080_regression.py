"""Fail-closed full regression runner for the public v0.8 release test set.

The runner deliberately keeps its test census and invocation rules in this
module.  It runs every release test in a fresh Python subprocess and emits a
single JSON ledger suitable for CI and independent review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from typing import Iterable, Mapping


ROOT = pathlib.Path(__file__).resolve().parent.parent
RELEASE_TEST_FILES = (
    "scripts/test_governance_read_router.py",
    "scripts/test_knowledge_contract_validation.py",
    "scripts/test_runtime_action_policy.py",
    "scripts/test_runtime_action_policy_schema.py",
    "scripts/test_runtime_adapter.py",
    "scripts/test_runtime_adapter_e2e.py",
    "scripts/test_runtime_evidence_export.py",
    "scripts/test_runtime_evidence_export_contract.py",
    "scripts/test_runtime_gate_action_sidecar_e2e.py",
    "scripts/test_runtime_gate_decision.py",
    "scripts/test_runtime_publish_gate.py",
    "scripts/test_runtime_state_contract.py",
    "scripts/test_runtime_state_core.py",
    "scripts/test_runtime_state_journal.py",
    "scripts/test_runtime_state_projection.py",
    "scripts/test_runtime_state_sidecar.py",
    "scripts/test_runtime_v080_ci.py",
    "scripts/test_runtime_v080_smoke.py",
    "scripts/test_runtime_validator_dispatch.py",
    "scripts/test_runtime_validator_mesh.py",
    "scripts/test_runtime_validator_mesh_schema.py",
    "scripts/test_runtime_validator_mesh_sidecar_e2e.py",
    "scripts/test_validator_gate_regressions.py",
)

# This suite has no script entry point, so unittest module invocation is part
# of the release contract rather than an implementation fallback.
MODULE_INVOCATIONS = {
    "scripts/test_runtime_state_sidecar.py": "scripts.test_runtime_state_sidecar",
}
_RAN_TESTS = re.compile(r"Ran\s+(\d+)\s+tests?\s+in\s+", re.MULTILINE)
_SOURCE_EXCLUDED_SEGMENTS = frozenset({".git"})


def _relative_posix(path: pathlib.Path, root: pathlib.Path) -> str:
    return pathlib.PurePosixPath(path.relative_to(root).as_posix()).as_posix()


def discover_release_tests(root: pathlib.Path) -> tuple[str, ...]:
    """Return the complete release-test census in deterministic order."""
    return tuple(
        sorted(
            _relative_posix(path, root)
            for path in (root / "scripts").glob("test_*.py")
            if path.is_file()
        )
    )


def _source_snapshot(root: pathlib.Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = pathlib.PurePosixPath(path.relative_to(root).as_posix())
        if any(part in _SOURCE_EXCLUDED_SEGMENTS for part in relative.parts):
            continue
        snapshot[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _command_for(relative_path: str, module_invocations: Mapping[str, str]) -> tuple[str, list[str]]:
    module = module_invocations.get(relative_path)
    if module:
        return "unittest_module", [sys.executable, "-m", "unittest", module]
    return "script", [sys.executable, relative_path]


def _environment(root: pathlib.Path, workspace: pathlib.Path) -> dict[str, str]:
    environment = os.environ.copy()
    python_paths = [str(root), str(root / "scripts")]
    if environment.get("PYTHONPATH"):
        python_paths.append(environment["PYTHONPATH"])
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(workspace / "pycache"),
        "PYTHONPATH": os.pathsep.join(python_paths),
        "TMP": str(workspace / "tmp"),
        "TEMP": str(workspace / "tmp"),
        "TMPDIR": str(workspace / "tmp"),
    })
    return environment


def _test_count(process: subprocess.CompletedProcess[str]) -> int:
    matches = _RAN_TESTS.findall(process.stdout + "\n" + process.stderr)
    return int(matches[-1]) if matches else 0


def _output_tail(process: subprocess.CompletedProcess[str]) -> str:
    text = (process.stdout + "\n" + process.stderr).strip()
    return text[-2000:]


def _outside_source(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return False


def run_regression(
    root: pathlib.Path = ROOT,
    manifest: Iterable[str] = RELEASE_TEST_FILES,
    module_invocations: Mapping[str, str] = MODULE_INVOCATIONS,
    tmp_dir: pathlib.Path | None = None,
) -> dict:
    """Run a manifest only when it exactly equals the discovered test census."""
    root = root.resolve()
    expected = tuple(sorted(manifest))
    discovered = discover_release_tests(root)
    source_before = _source_snapshot(root)
    missing = sorted(set(expected) - set(discovered))
    extra = sorted(set(discovered) - set(expected))
    if expected != discovered:
        return {
            "files": len(discovered),
            "total_tests": 0,
            "failed_files": max(1, len(missing) + len(extra)),
            "census_match": False,
            "missing_files": missing,
            "extra_files": extra,
            "source_unchanged": _source_snapshot(root) == source_before,
            "ledger": [],
        }

    if tmp_dir is not None and not _outside_source(tmp_dir, root):
        raise ValueError("--tmp-dir must be outside the Source repository")

    def execute(workspace: pathlib.Path) -> dict:
        (workspace / "tmp").mkdir(parents=True, exist_ok=True)
        environment = _environment(root, workspace)
        ledger = []
        for relative_path in expected:
            invocation_mode, command = _command_for(relative_path, module_invocations)
            started = time.monotonic()
            process = subprocess.run(
                command,
                cwd=str(root),
                env=environment,
                text=True,
                capture_output=True,
                timeout=600,
            )
            test_count = _test_count(process)
            passed = process.returncode == 0 and test_count > 0
            entry = {
                "path": relative_path,
                "invocation_mode": invocation_mode,
                "command": command,
                "test_count": test_count,
                "exit_code": process.returncode,
                "duration_seconds": round(time.monotonic() - started, 6),
                "status": "pass" if passed else "fail",
            }
            if not passed:
                entry["output_tail"] = _output_tail(process)
            ledger.append(entry)

        source_unchanged = _source_snapshot(root) == source_before
        failed_files = sum(1 for entry in ledger if entry["status"] != "pass")
        if not source_unchanged:
            failed_files += 1
        return {
            "files": len(expected),
            "total_tests": sum(entry["test_count"] for entry in ledger),
            "failed_files": failed_files,
            "census_match": True,
            "missing_files": [],
            "extra_files": [],
            "source_unchanged": source_unchanged,
            "ledger": ledger,
        }

    if tmp_dir is None:
        with tempfile.TemporaryDirectory(prefix="railyard-v080-regression-") as temporary:
            return execute(pathlib.Path(temporary))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return execute(tmp_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the complete v0.8 release regression census")
    parser.add_argument("--tmp-dir", help="Optional caller workspace outside the Source repository")
    args = parser.parse_args()
    try:
        result = run_regression(tmp_dir=pathlib.Path(args.tmp_dir) if args.tmp_dir else None)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        result = {
            "files": 0,
            "total_tests": 0,
            "failed_files": 1,
            "census_match": False,
            "missing_files": [],
            "extra_files": [],
            "source_unchanged": True,
            "ledger": [],
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if (
        result["census_match"]
        and result["files"] == len(RELEASE_TEST_FILES)
        and result["failed_files"] == 0
        and result["total_tests"] >= 1206
        and result["source_unchanged"]
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
