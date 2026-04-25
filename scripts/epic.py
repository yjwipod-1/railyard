#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workflow_schema import ensure_schema

VALID_STATUSES = {"queued", "in_progress", "partial", "blocked", "done", "superseded"}
TERMINAL_STATUSES = {"done", "superseded"}
VALID_PRIORITIES = {"high", "medium", "low"}
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
STATUS_RANK = {"in_progress": 0, "partial": 1, "queued": 2, "blocked": 3, "done": 4, "superseded": 5}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def lane_table(lane: str) -> str:
    return f"{lane}_epic"


def epic_dir(project_root: pathlib.Path, lane: str) -> pathlib.Path:
    return project_root / "docs" / lane / "epics"


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def extract_frontmatter(text: str) -> str:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return match.group(1) if match else ""


def frontmatter_value(frontmatter: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:[ \t]*([^\r\n]*)$", frontmatter, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value or None


def section_body(text: str, heading: str) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def markdown_bullets(section: str) -> list[str]:
    values: list[str] = []
    for line in section.splitlines():
        match = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*)$", line)
        if match:
            values.append(match.group(1).strip())
    return values


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    return []


def parse_epic_doc(path: pathlib.Path, lane: str) -> dict[str, Any]:
    text = read_text(path)
    frontmatter = extract_frontmatter(text)
    epic_id = frontmatter_value(frontmatter, "epic_id") or path.stem
    title_match = re.search(rf"^#\s+{re.escape(epic_id)}\s+-\s+(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else epic_id
    status = (frontmatter_value(frontmatter, "status") or "queued").strip().lower()
    priority = (frontmatter_value(frontmatter, "priority") or "medium").strip().lower()
    if status not in VALID_STATUSES:
        status = "queued"
    if priority not in VALID_PRIORITIES:
        priority = "medium"
    source = frontmatter_value(frontmatter, "source") or path.name
    summary = " ".join(section_body(text, "Summary").split()) or title
    done_definition = markdown_bullets(section_body(text, "Done Definition"))
    preferred_entrypoints = markdown_bullets(section_body(text, "Preferred Entrypoints"))
    notes = markdown_bullets(section_body(text, "Notes"))
    blocked_by_epics = markdown_bullets(section_body(text, "Blocked By Epics"))
    blocked_by_external = markdown_bullets(section_body(text, "Blocked By External"))
    linked_ticket_id = frontmatter_value(frontmatter, "linked_ticket_id")
    created_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()
    return {
        "epic_id": epic_id,
        "title": title,
        "status": status,
        "priority": priority,
        "source": source,
        "summary": summary,
        "blocked_by_epic_ids_json": json_dumps(blocked_by_epics),
        "blocked_by_external_json": json_dumps(blocked_by_external),
        "preferred_entrypoints_json": json_dumps(preferred_entrypoints),
        "done_definition_json": json_dumps(done_definition),
        "notes_json": json_dumps(notes),
        "linked_ticket_id": linked_ticket_id or None,
        "completed_at": None,
        "created_at": created_at,
        "updated_at": iso_now(),
        "lane": lane,
    }


def upsert_epic(conn: sqlite3.Connection, lane: str, row: dict[str, Any], preserve_terminal: bool = True) -> None:
    table = lane_table(lane)
    existing = conn.execute(
        f"SELECT status, completed_at FROM {table} WHERE epic_id = ?",
        (row["epic_id"],),
    ).fetchone()
    if existing and preserve_terminal and existing[0] in TERMINAL_STATUSES and row["status"] not in TERMINAL_STATUSES:
        row["status"] = existing[0]
        row["completed_at"] = existing[1]

    columns = [
        "epic_id",
        "title",
        "status",
        "priority",
        "source",
        "summary",
        "blocked_by_epic_ids_json",
        "blocked_by_external_json",
        "preferred_entrypoints_json",
        "done_definition_json",
        "notes_json",
        "linked_ticket_id",
        "completed_at",
        "created_at",
        "updated_at",
    ]
    placeholders = ", ".join(":" + column for column in columns)
    assignments = ", ".join(f"{column}=excluded.{column}" for column in columns[1:])
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(epic_id) DO UPDATE SET {assignments}",
        {column: row.get(column) for column in columns},
    )


def parse_row(row: sqlite3.Row | dict[str, Any], snapshot: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    payload = dict(row)
    for key in (
        "blocked_by_epic_ids_json",
        "blocked_by_external_json",
        "preferred_entrypoints_json",
        "done_definition_json",
        "notes_json",
    ):
        payload[key] = json_list(payload.get(key))
    blocked_internal = []
    if snapshot is not None:
        blocked_internal = [
            dep for dep in payload["blocked_by_epic_ids_json"] if snapshot.get(dep, {}).get("status") not in TERMINAL_STATUSES
        ]
    else:
        blocked_internal = list(payload["blocked_by_epic_ids_json"])
    blocked_external = list(payload["blocked_by_external_json"])
    if blocked_internal and blocked_external:
        dependency_state = "blocked_mixed"
    elif blocked_internal:
        dependency_state = "blocked_internal"
    elif blocked_external:
        dependency_state = "blocked_external"
    else:
        dependency_state = "actionable"
    payload["blocking_epic_ids"] = blocked_internal
    payload["blocking_external_dependencies"] = blocked_external
    payload["dependency_state"] = dependency_state
    payload["actionable"] = dependency_state == "actionable"
    payload["priority_rank"] = PRIORITY_RANK.get(payload["priority"], 9)
    payload["status_rank"] = STATUS_RANK.get(payload["status"], 9)
    return payload


def dependency_snapshot(conn: sqlite3.Connection, lane: str) -> dict[str, dict[str, Any]]:
    table = lane_table(lane)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT epic_id, title, status FROM {table}").fetchall()
    return {str(row["epic_id"]): {"title": row["title"], "status": row["status"]} for row in rows}


def sorted_open_rows(conn: sqlite3.Connection, lane: str) -> list[dict[str, Any]]:
    table = lane_table(lane)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE status NOT IN ('done', 'superseded') ORDER BY updated_at DESC"
    ).fetchall()
    snapshot = dependency_snapshot(conn, lane)
    decorated = [parse_row(row, snapshot) for row in rows]
    decorated.sort(
        key=lambda row: (
            0 if row["actionable"] else 1,
            row["priority_rank"],
            row["status_rank"],
            row["title"].lower(),
        )
    )
    return decorated


def command_sync_docs(conn: sqlite3.Connection, project_root: pathlib.Path, lane: str) -> dict[str, Any]:
    paths = sorted(epic_dir(project_root, lane).glob(f"{lane.upper()}-E*.md"))
    synced: list[str] = []
    for path in paths:
        row = parse_epic_doc(path, lane)
        upsert_epic(conn, lane, row)
        synced.append(row["epic_id"])
    conn.commit()
    return {"status": "ok", "lane": lane, "synced_epic_ids": synced}


def command_show(conn: sqlite3.Connection, lane: str, epic_id: str) -> dict[str, Any]:
    table = lane_table(lane)
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"SELECT * FROM {table} WHERE epic_id = ?", (epic_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"{table} row not found for {epic_id}")
    return parse_row(row, dependency_snapshot(conn, lane))


def command_upsert(conn: sqlite3.Connection, lane: str, epic_id: str, title: str, status: str, priority: str, source: str, summary: str) -> dict[str, Any]:
    now = iso_now()
    row = {
        "epic_id": epic_id,
        "title": title,
        "status": status,
        "priority": priority,
        "source": source,
        "summary": summary or title,
        "blocked_by_epic_ids_json": json_dumps([]),
        "blocked_by_external_json": json_dumps([]),
        "preferred_entrypoints_json": json_dumps([]),
        "done_definition_json": json_dumps([]),
        "notes_json": json_dumps([]),
        "linked_ticket_id": None,
        "completed_at": now if status in TERMINAL_STATUSES else None,
        "created_at": now,
        "updated_at": now,
    }
    upsert_epic(conn, lane, row, preserve_terminal=False)
    conn.commit()
    return command_show(conn, lane, epic_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dual-lane workflow epic helper.",
        epilog=(
            "Helper commands:\n"
            "  python railyard/scripts/epic.py --lane domain --project-root . sync-docs\n"
            "  python railyard/scripts/epic.py --lane system list-open --db .workflow/workflow.db\n"
            "  python railyard/scripts/epic.py --lane domain show --epic-id DOMAIN-E001"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--lane", choices=("domain", "system"), required=True)
    parser.add_argument("--db", default=".workflow/workflow.db")
    parser.add_argument("--project-root", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sync-docs", help="Sync epic markdown docs into the lane epic table.")
    show_parser = subparsers.add_parser("show", help="Show one epic row.")
    show_parser.add_argument("--epic-id", required=True)
    subparsers.add_parser("list-open", help="List unresolved epics.")
    subparsers.add_parser("next-open", help="Return the next unresolved epic.")
    upsert_parser = subparsers.add_parser("upsert", help="Create or update an epic row directly.")
    upsert_parser.add_argument("--epic-id", required=True)
    upsert_parser.add_argument("--title", required=True)
    upsert_parser.add_argument("--status", default="queued")
    upsert_parser.add_argument("--priority", default="medium")
    upsert_parser.add_argument("--source", default="helper")
    upsert_parser.add_argument("--summary", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = pathlib.Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    project_root = pathlib.Path(args.project_root).resolve()
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        if args.command == "sync-docs":
            payload = command_sync_docs(conn, project_root, args.lane)
        elif args.command == "show":
            payload = command_show(conn, args.lane, args.epic_id)
        elif args.command == "list-open":
            payload = sorted_open_rows(conn, args.lane)
        elif args.command == "next-open":
            rows = sorted_open_rows(conn, args.lane)
            payload = rows[0] if rows else None
        elif args.command == "upsert":
            payload = command_upsert(conn, args.lane, args.epic_id, args.title, args.status, args.priority, args.source, args.summary)
        else:
            raise RuntimeError(f"Unsupported command: {args.command}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
