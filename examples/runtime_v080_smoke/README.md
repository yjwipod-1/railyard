# Runtime v0.8 Smoke Quickstart

This is the public quickstart for the frozen runtime v0.8 smoke catalog. Use the smoke CLI only: `scripts/runtime_v080_smoke.py`. Runtime component modules are import APIs, not separate smoke CLIs.

## Run The Canonical Local Route

Run these commands from the repository root. The runtime remains stdlib-only; the test and validation route uses the two direct test dependencies in `requirements-test.txt`. Provide a temporary workspace outside the source checkout for the smoke runner. Do not create a source `.tmp`, `.workflow`, cache, or evidence directory.

PowerShell:

```powershell
$smokeRoot = Join-Path $env:TEMP "Railyard v0.8 smoke"
python -m pip install -r requirements-test.txt
python -m compileall -q scripts
python scripts/validate_artifacts.py --project-root .
python scripts/test_runtime_v080_ci.py
python scripts/runtime_v080_regression.py
python scripts/runtime_v080_smoke.py --tmp-dir $smokeRoot --all run
```

POSIX shell:

```bash
smoke_root="${TMPDIR:-/tmp}/railyard v0.8 smoke"
python -m pip install -r requirements-test.txt
python -m compileall -q scripts
python scripts/validate_artifacts.py --project-root .
python scripts/test_runtime_v080_ci.py
python scripts/runtime_v080_regression.py
python scripts/runtime_v080_smoke.py --tmp-dir "$smoke_root" --all run
```

## Conformance Semantics

The frozen [catalog](conformance.json) contains 20 scenarios. Scenarios 003-011 are nine expected typed non-pass Validator Mesh outcomes. The other 11 scenarios cover normal, tamper, recovery, and visibility paths.

The smoke runner measures execution conformance, not whether every scenario's business `final_verdict` is `pass`. A correct all-scenario run reports `total=20`, `passed=20`, `failed=0`, and exits 0. Therefore, a typed non-pass `final_verdict` in a passing scenario is expected scenario data, not a smoke failure.

The call ledger and scenario boundary are defined by the accepted [runtime v0.8 smoke contract](../../references/runtime-v080-smoke-contract.md). The catalog is the frozen scenario authority; the contract explains the three-call and one-event boundary for scenarios 003-011 and the expected raw Mesh outputs.

## Release Boundary

The repository implements local deterministic runtime validation and configures Windows and Linux GitHub Actions for this route. Hosted CI has been locally validated but has not been remotely executed without Human-authorized staging and push. Railyard provides no hosted runtime or service, scheduler, proprietary provider or model, Knowledge extraction or store, vector database, RAG implementation, or automatic release, tag, commit, or push.
