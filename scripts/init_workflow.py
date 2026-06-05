#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sqlite3
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workflow_schema import ensure_schema

SKILL_ROOT = SCRIPT_DIR.parent
SKELETON_ROOT = SKILL_ROOT / "assets" / "skeleton"
WORKFLOW_AUTHORITY_PATH = SKILL_ROOT / ".railyard-workflow.json"
REQUIRED_WORKFLOW_TABLES = {
    "domain_epic",
    "domain_ticket",
    "system_epic",
    "system_ticket",
    "workflow_event",
}
SCAN_PRUNE_DIRS = {".git", ".venv", "__pycache__", "node_modules", "venv"}


class WorkflowAuthorityError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize a reusable dual-lane workflow in a target project.",
        epilog=(
            "Helper commands:\n"
            "  python railyard/scripts/init_workflow.py --project-root .\n"
            "  python railyard/scripts/init_workflow.py --project-root . --force"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--project-root", required=True, help="Target project root.")
    parser.add_argument(
        "--db-path",
        help=(
            "Explicit workflow SQLite path. Relative paths resolve from the target project root. "
            "When omitted, the database defaults to <Railyard root>/.workflow/workflow.db."
        ),
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing skeleton files when safe.")
    return parser.parse_args()


def copy_skeleton(project_root: pathlib.Path, force: bool) -> list[str]:
    created: list[str] = []
    for path in sorted(SKELETON_ROOT.rglob("*")):
        relative = path.relative_to(SKELETON_ROOT)
        target = project_root / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not force:
            continue
        shutil.copy2(path, target)
        created.append(str(relative).replace("\\", "/"))
    return created


def resolve_requested_db_path(project_root: pathlib.Path, requested_path: str) -> pathlib.Path:
    db_path = pathlib.Path(requested_path)
    if not db_path.is_absolute():
        db_path = project_root / db_path
    return db_path.resolve()


def resolve_recorded_path(descriptor: object) -> pathlib.Path:
    if not isinstance(descriptor, dict):
        raise WorkflowAuthorityError("workflow_db must be a path descriptor object.")
    base = descriptor.get("base")
    path = descriptor.get("path")
    if not isinstance(path, str) or not path:
        raise WorkflowAuthorityError("workflow_db.path must be a non-empty string.")
    if base == "railyard_root":
        return (SKILL_ROOT / path).resolve()
    if base == "absolute":
        return pathlib.Path(path).resolve()
    raise WorkflowAuthorityError(f"Unsupported workflow_db.base value: {base!r}.")


def load_recorded_db_path() -> pathlib.Path | None:
    if not WORKFLOW_AUTHORITY_PATH.exists():
        return None
    try:
        payload = json.loads(WORKFLOW_AUTHORITY_PATH.read_text(encoding="utf-8"))
        db_path = resolve_recorded_path(payload.get("workflow_db"))
    except (OSError, json.JSONDecodeError, WorkflowAuthorityError) as exc:
        raise WorkflowAuthorityError(
            f"Invalid workflow authority record at {WORKFLOW_AUTHORITY_PATH}: {exc}"
        ) from exc
    if not db_path.is_file():
        raise WorkflowAuthorityError(
            f"Recorded workflow database does not exist: {db_path}. "
            "Run init_workflow.py with an explicit --db-path to repair the authority record."
        )
    return db_path


def is_railyard_workflow_db(path: pathlib.Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return REQUIRED_WORKFLOW_TABLES.issubset({row[0] for row in rows})


def scan_workflow_candidates(project_root: pathlib.Path) -> list[pathlib.Path]:
    roots = [project_root]
    try:
        SKILL_ROOT.relative_to(project_root)
    except ValueError:
        roots.append(SKILL_ROOT)

    candidates: set[pathlib.Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for current, dirnames, _ in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in SCAN_PRUNE_DIRS]
            if ".workflow" not in dirnames:
                continue
            candidate = (pathlib.Path(current) / ".workflow" / "workflow.db").resolve()
            dirnames.remove(".workflow")
            if candidate.is_file() and is_railyard_workflow_db(candidate):
                candidates.add(candidate)
    return sorted(candidates, key=lambda path: str(path).casefold())


def choose_workflow_candidate(candidates: list[pathlib.Path]) -> pathlib.Path:
    print(
        f"No workflow authority record exists at {WORKFLOW_AUTHORITY_PATH}. "
        "Existing Railyard workflow databases were found:",
        file=sys.stderr,
    )
    for index, candidate in enumerate(candidates, start=1):
        print(f"  [{index}] {candidate}", file=sys.stderr)
    print(
        "Select the workflow database number to record as this Railyard installation's authority. "
        "For non-interactive use, rerun with an explicit --db-path.",
        file=sys.stderr,
    )
    selection = sys.stdin.readline().strip()
    try:
        selected_index = int(selection)
    except ValueError:
        raise WorkflowAuthorityError("No valid workflow database selection was provided.")
    if not 1 <= selected_index <= len(candidates):
        raise WorkflowAuthorityError("No valid workflow database selection was provided.")
    return candidates[selected_index - 1]


def resolve_db_path(project_root: pathlib.Path, requested_path: str | None) -> tuple[pathlib.Path, str]:
    if requested_path is not None:
        return resolve_requested_db_path(project_root, requested_path), "explicit"

    recorded_path = load_recorded_db_path()
    if recorded_path is not None:
        return recorded_path, "recorded"

    candidates = scan_workflow_candidates(project_root)
    if candidates:
        return choose_workflow_candidate(candidates), "discovered"

    return (SKILL_ROOT / ".workflow" / "workflow.db").resolve(), "default"


def describe_path(path: pathlib.Path) -> dict[str, str]:
    try:
        relative = path.relative_to(SKILL_ROOT)
    except ValueError:
        return {"base": "absolute", "path": str(path)}
    return {"base": "railyard_root", "path": relative.as_posix() or "."}


def record_workflow_authority(project_root: pathlib.Path, db_path: pathlib.Path, selection_source: str) -> None:
    payload = {
        "schema_version": 1,
        "railyard_root": str(SKILL_ROOT),
        "project_root": describe_path(project_root),
        "workflow_root": describe_path(db_path.parent),
        "workflow_db": describe_path(db_path),
        "selection_source": selection_source,
    }
    WORKFLOW_AUTHORITY_PATH.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    project_root = pathlib.Path(args.project_root).resolve()
    project_root.mkdir(parents=True, exist_ok=True)
    try:
        db_path, selection_source = resolve_db_path(project_root, args.db_path)
    except WorkflowAuthorityError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()

    created = copy_skeleton(project_root, force=args.force)
    record_workflow_authority(project_root, db_path, selection_source)
    payload = {
        "status": "ok",
        "project_root": str(project_root),
        "db_path": str(db_path),
        "workflow_authority_path": str(WORKFLOW_AUTHORITY_PATH),
        "workflow_selection_source": selection_source,
        "copied_paths": created,
        "notes": [
            "The skeleton directory is a reusable project seed copied into the target project.",
            "Inbox and outbox directories are included as body surfaces for tickets and results.",
            "Default .github/agents profiles provide optional platform dispatch adapters for tools that support them.",
            "The workflow authority record stores the resolved project and workflow paths used by this Railyard installation."
        ]
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
