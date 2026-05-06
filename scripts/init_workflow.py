#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize a reusable dual-lane workflow in a target project.",
        epilog=(
            "Helper commands:\n"
            "  python railyard/scripts/init_workflow.py --project-root .\n"
            "  python railyard/scripts/init_workflow.py --project-root . --db-path .workflow/workflow.db --force"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--project-root", required=True, help="Target project root.")
    parser.add_argument("--db-path", default=".workflow/workflow.db", help="Workflow SQLite path relative to project root.")
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


def main() -> int:
    args = parse_args()
    project_root = pathlib.Path(args.project_root).resolve()
    project_root.mkdir(parents=True, exist_ok=True)
    db_path = (project_root / args.db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()

    created = copy_skeleton(project_root, force=args.force)
    payload = {
        "status": "ok",
        "project_root": str(project_root),
        "db_path": str(db_path),
        "copied_paths": created,
        "notes": [
            "The skeleton directory is a reusable project seed copied into the target project.",
            "Inbox and outbox directories are included as body surfaces for tickets and results.",
            "Default .github/agents profiles provide optional platform dispatch adapters for tools that support them."
        ]
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
