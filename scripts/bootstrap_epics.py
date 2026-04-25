#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from epic import upsert_epic
from workflow_schema import ensure_schema


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def normalize_epic_id(lane: str, raw_value: str, index: int) -> str:
    value = raw_value.strip()
    if value:
        return value
    return f"{lane.upper()}-E{index:03d}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap lane epics from a simple JSON queue file.",
        epilog=(
            "Helper commands:\n"
            "  python railyard/scripts/bootstrap_epics.py --lane domain --input queue.json\n"
            "  python railyard/scripts/bootstrap_epics.py --lane system --input system-queue.json --db .workflow/workflow.db"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--lane", choices=("domain", "system"), required=True)
    parser.add_argument("--db", default=".workflow/workflow.db")
    parser.add_argument("--input", required=True, help="JSON file with an items[] array.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = pathlib.Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    input_path = pathlib.Path(args.input).resolve()
    payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Input JSON must contain an items[] array.")

    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        synced: list[str] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            epic_id = normalize_epic_id(args.lane, str(item.get("epic_id") or item.get("focus_id") or ""), index)
            now = iso_now()
            row = {
                "epic_id": epic_id,
                "title": str(item.get("title") or epic_id),
                "status": str(item.get("status") or "queued"),
                "priority": str(item.get("priority") or "medium"),
                "source": str(item.get("source") or input_path.name),
                "summary": str(item.get("summary") or item.get("title") or epic_id),
                "blocked_by_epic_ids_json": json_dumps(item.get("blocked_by_epics") or []),
                "blocked_by_external_json": json_dumps(item.get("blocked_by_external") or []),
                "preferred_entrypoints_json": json_dumps(item.get("preferred_entrypoints") or []),
                "done_definition_json": json_dumps(item.get("done_definition") or []),
                "notes_json": json_dumps(item.get("notes") or []),
                "linked_ticket_id": item.get("linked_ticket_id"),
                "completed_at": item.get("completed_at"),
                "created_at": now,
                "updated_at": now,
            }
            upsert_epic(conn, args.lane, row, preserve_terminal=False)
            synced.append(epic_id)
        conn.commit()
        print(json.dumps({"status": "ok", "lane": args.lane, "synced_epic_ids": synced}, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
