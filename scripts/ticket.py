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

RUNNER_READY_STATUS = "ready"
RUNNER_RUNNING_STATUS = "running"
ARCHITECT_READY_STATUS = "awaiting_review"
ARCHITECT_RUNNING_STATUS = "in_review"
FINAL_STATUS = "finalised"
SUPERSEDED_STATUS = "superseded"

VALID_RUNNER_RESULTS = {"done", "partial", "blocked", "invalid"}
VALID_REVIEW_RESULTS = {"accept", "accept_with_changes", "reject", "redesign"}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def lane_table(lane: str) -> str:
    return f"{lane}_ticket"


def inbox_dir(project_root: pathlib.Path, lane: str) -> pathlib.Path:
    return project_root / "docs" / lane / "inbox"


def outbox_dir(project_root: pathlib.Path, lane: str) -> pathlib.Path:
    return project_root / "docs" / lane / "outbox"


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def read_json(path: pathlib.Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def extract_frontmatter(text: str) -> str:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return match.group(1) if match else ""


def frontmatter_value(frontmatter: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:[ \t]*([^\r\n]*)$", frontmatter, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value or None


def infer_summary(ticket_text: str) -> str:
    lines = ticket_text.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("## Task"):
            for next_line in lines[idx + 1 :]:
                stripped = next_line.strip()
                if stripped:
                    return stripped
    return "workflow ticket"


def infer_runner_result(outbox_payload: dict[str, Any]) -> str | None:
    runner_status = outbox_payload.get("runner_status")
    if isinstance(runner_status, str) and runner_status in VALID_RUNNER_RESULTS:
        return runner_status
    return None


def load_ticket_row(project_root: pathlib.Path, lane: str, ticket_path: pathlib.Path) -> dict[str, Any]:
    ticket_text = read_text(ticket_path)
    frontmatter = extract_frontmatter(ticket_text)
    ticket_id = frontmatter_value(frontmatter, "ticket_id") or ticket_path.stem
    task_type = frontmatter_value(frontmatter, "task_type") or "change"
    task_mode = frontmatter_value(frontmatter, "task_mode") or "general"
    priority = frontmatter_value(frontmatter, "priority") or "medium"
    epic_id = frontmatter_value(frontmatter, "epic_id") or ticket_id
    outbox_hint = frontmatter_value(frontmatter, "outbox_result_path")
    outbox_path = pathlib.Path(outbox_hint) if outbox_hint else (outbox_dir(project_root, lane) / f"{ticket_id}.result.json")
    if not outbox_path.is_absolute():
        outbox_path = (project_root / outbox_path).resolve()

    metadata: dict[str, Any] = {
        "ticket_id": ticket_id,
        "epic_id": epic_id,
        "task_mode": task_mode,
        "task_type": task_type,
        "priority": priority,
        "inbox_path": str(ticket_path.relative_to(project_root)).replace("\\", "/"),
        "outbox_path": str(outbox_path.relative_to(project_root)).replace("\\", "/") if outbox_path.is_relative_to(project_root) else str(outbox_path),
        "status": RUNNER_READY_STATUS,
        "next_actor": "runner",
        "runner_result": None,
        "review_result": None,
        "supersedes_ticket_id": frontmatter_value(frontmatter, "supersedes_ticket_id"),
        "parent_ticket_id": frontmatter_value(frontmatter, "parent_ticket_id"),
        "summary": infer_summary(ticket_text),
        "claimed_by": None,
        "claimed_at": None,
        "created_at": datetime.fromtimestamp(ticket_path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat(),
        "updated_at": iso_now(),
    }
    if outbox_path.exists():
        outbox_payload = read_json(outbox_path)
        metadata["status"] = ARCHITECT_READY_STATUS
        metadata["next_actor"] = "architect"
        metadata["runner_result"] = infer_runner_result(outbox_payload)
    return metadata


def upsert_ticket(conn: sqlite3.Connection, lane: str, row: dict[str, Any], preserve_terminal: bool = True) -> None:
    table = lane_table(lane)
    existing = conn.execute(
        f"SELECT status, next_actor, runner_result, review_result, claimed_by, claimed_at FROM {table} WHERE ticket_id = ?",
        (row["ticket_id"],),
    ).fetchone()
    if existing and preserve_terminal and existing[0] in {FINAL_STATUS, SUPERSEDED_STATUS}:
        row["status"] = existing[0]
        row["next_actor"] = existing[1]
        row["runner_result"] = existing[2]
        row["review_result"] = existing[3]
        row["claimed_by"] = existing[4]
        row["claimed_at"] = existing[5]

    columns = [
        "ticket_id",
        "epic_id",
        "task_mode",
        "task_type",
        "priority",
        "inbox_path",
        "outbox_path",
        "status",
        "next_actor",
        "runner_result",
        "review_result",
        "supersedes_ticket_id",
        "parent_ticket_id",
        "summary",
        "claimed_by",
        "claimed_at",
        "created_at",
        "updated_at",
    ]
    placeholders = ", ".join(":" + column for column in columns)
    assignments = ", ".join(f"{column}=excluded.{column}" for column in columns[1:])
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(ticket_id) DO UPDATE SET {assignments}",
        {column: row.get(column) for column in columns},
    )


def fetch_row(conn: sqlite3.Connection, lane: str, ticket_id: str) -> dict[str, Any] | None:
    table = lane_table(lane)
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"SELECT * FROM {table} WHERE ticket_id = ?", (ticket_id,)).fetchone()
    return dict(row) if row is not None else None


def actor_ready_status(actor: str) -> str:
    return RUNNER_READY_STATUS if actor == "runner" else ARCHITECT_READY_STATUS


def actor_running_status(actor: str) -> str:
    return RUNNER_RUNNING_STATUS if actor == "runner" else ARCHITECT_RUNNING_STATUS


def command_sync_mailbox(conn: sqlite3.Connection, project_root: pathlib.Path, lane: str, ticket_id: str | None) -> dict[str, Any]:
    paths = [inbox_dir(project_root, lane) / f"{ticket_id}.md"] if ticket_id else sorted(inbox_dir(project_root, lane).glob(f"{lane.upper()}-*.md"))
    synced: list[str] = []
    skipped: list[str] = []
    for ticket_path in paths:
        if not ticket_path.exists():
            skipped.append(ticket_path.name)
            continue
        row = load_ticket_row(project_root, lane, ticket_path)
        upsert_ticket(conn, lane, row)
        synced.append(row["ticket_id"])
    conn.commit()
    return {"status": "ok", "lane": lane, "synced_ticket_ids": synced, "skipped": skipped}


def command_list(conn: sqlite3.Connection, lane: str, status: str | None, next_actor: str | None) -> list[dict[str, Any]]:
    table = lane_table(lane)
    conn.row_factory = sqlite3.Row
    clauses = []
    params: list[str] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if next_actor:
        clauses.append("next_actor = ?")
        params.append(next_actor)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT ticket_id, epic_id, task_mode, task_type, priority, status, next_actor, runner_result, review_result, inbox_path, outbox_path, updated_at FROM {table} {where} ORDER BY id DESC",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def command_next(conn: sqlite3.Connection, lane: str, actor: str) -> dict[str, Any] | None:
    table = lane_table(lane)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        f"SELECT * FROM {table} WHERE status = ? AND next_actor = ? ORDER BY id DESC LIMIT 1",
        (actor_ready_status(actor), actor),
    ).fetchone()
    return dict(row) if row is not None else None


def command_claim(conn: sqlite3.Connection, lane: str, ticket_id: str, actor: str, claimed_by: str | None) -> dict[str, Any]:
    table = lane_table(lane)
    expected_status = actor_ready_status(actor)
    new_status = actor_running_status(actor)
    now = iso_now()
    cursor = conn.execute(
        f"UPDATE {table} SET status = ?, claimed_by = ?, claimed_at = ?, updated_at = ? WHERE ticket_id = ? AND status = ? AND next_actor = ?",
        (new_status, claimed_by or actor, now, now, ticket_id, expected_status, actor),
    )
    conn.commit()
    if cursor.rowcount != 1:
        raise RuntimeError(f"claim failed for {ticket_id}; expected status={expected_status} next_actor={actor}")
    row = fetch_row(conn, lane, ticket_id)
    if row is None:
        raise RuntimeError(f"{table} row missing after claim for {ticket_id}")
    return row


def command_mark_runner_result(conn: sqlite3.Connection, lane: str, ticket_id: str, runner_result: str, outbox_path: str | None) -> dict[str, Any]:
    if runner_result not in VALID_RUNNER_RESULTS:
        raise ValueError(f"invalid runner_result: {runner_result}")
    table = lane_table(lane)
    now = iso_now()
    cursor = conn.execute(
        f"UPDATE {table} SET status = ?, next_actor = ?, runner_result = ?, outbox_path = COALESCE(?, outbox_path), updated_at = ? WHERE ticket_id = ?",
        (ARCHITECT_READY_STATUS, "architect", runner_result, outbox_path, now, ticket_id),
    )
    conn.commit()
    if cursor.rowcount != 1:
        raise RuntimeError(f"{table} row not found for {ticket_id}")
    row = fetch_row(conn, lane, ticket_id)
    if row is None:
        raise RuntimeError(f"{table} row missing after runner result update for {ticket_id}")
    return row


def command_mark_review_result(conn: sqlite3.Connection, lane: str, ticket_id: str, review_result: str, supersedes_ticket_id: str | None) -> dict[str, Any]:
    if review_result not in VALID_REVIEW_RESULTS:
        raise ValueError(f"invalid review_result: {review_result}")
    table = lane_table(lane)
    now = iso_now()
    cursor = conn.execute(
        f"UPDATE {table} SET status = ?, next_actor = ?, review_result = ?, supersedes_ticket_id = COALESCE(?, supersedes_ticket_id), updated_at = ? WHERE ticket_id = ?",
        (FINAL_STATUS, "none", review_result, supersedes_ticket_id, now, ticket_id),
    )
    conn.commit()
    if cursor.rowcount != 1:
        raise RuntimeError(f"{table} row not found for {ticket_id}")
    row = fetch_row(conn, lane, ticket_id)
    if row is None:
        raise RuntimeError(f"{table} row missing after review update for {ticket_id}")
    return row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dual-lane workflow ticket helper.",
        epilog=(
            "Helper commands:\n"
            "  python railyard/scripts/ticket.py --lane domain --project-root . sync-mailbox\n"
            "  python railyard/scripts/ticket.py --lane domain next --actor runner\n"
            "  python railyard/scripts/ticket.py --lane system claim --ticket-id SYSTEM-001 --actor runner --claimed-by codex"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--lane", choices=("domain", "system"), required=True)
    parser.add_argument("--db", default=".workflow/workflow.db")
    parser.add_argument("--project-root", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync-mailbox", help="Sync ticket markdown files into the lane ticket table.")
    sync_parser.add_argument("--ticket-id", default="")

    show_parser = subparsers.add_parser("show", help="Show one ticket row.")
    show_parser.add_argument("--ticket-id", required=True)

    list_parser = subparsers.add_parser("list", help="List ticket rows.")
    list_parser.add_argument("--status", default="")
    list_parser.add_argument("--next-actor", default="")

    next_parser = subparsers.add_parser("next", help="Return the next ready ticket for an actor.")
    next_parser.add_argument("--actor", choices=("runner", "architect"), required=True)

    claim_parser = subparsers.add_parser("claim", help="Claim a ticket for an actor.")
    claim_parser.add_argument("--ticket-id", required=True)
    claim_parser.add_argument("--actor", choices=("runner", "architect"), required=True)
    claim_parser.add_argument("--claimed-by", default="")

    runner_result_parser = subparsers.add_parser("mark-runner-result", help="Record runner completion and hand off to architect review.")
    runner_result_parser.add_argument("--ticket-id", required=True)
    runner_result_parser.add_argument("--runner-result", choices=tuple(sorted(VALID_RUNNER_RESULTS)), required=True)
    runner_result_parser.add_argument("--outbox-path", default="")

    review_parser = subparsers.add_parser("mark-review-result", help="Record architect review result.")
    review_parser.add_argument("--ticket-id", required=True)
    review_parser.add_argument("--review-result", choices=tuple(sorted(VALID_REVIEW_RESULTS)), required=True)
    review_parser.add_argument("--supersedes-ticket-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = pathlib.Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    project_root = pathlib.Path(args.project_root).resolve()
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        if args.command == "sync-mailbox":
            payload = command_sync_mailbox(conn, project_root, args.lane, args.ticket_id or None)
        elif args.command == "show":
            payload = fetch_row(conn, args.lane, args.ticket_id)
            if payload is None:
                raise RuntimeError(f"{lane_table(args.lane)} row not found for {args.ticket_id}")
        elif args.command == "list":
            payload = command_list(conn, args.lane, args.status or None, args.next_actor or None)
        elif args.command == "next":
            payload = command_next(conn, args.lane, args.actor)
        elif args.command == "claim":
            payload = command_claim(conn, args.lane, args.ticket_id, args.actor, args.claimed_by or None)
        elif args.command == "mark-runner-result":
            payload = command_mark_runner_result(conn, args.lane, args.ticket_id, args.runner_result, args.outbox_path or None)
        elif args.command == "mark-review-result":
            payload = command_mark_review_result(conn, args.lane, args.ticket_id, args.review_result, args.supersedes_ticket_id or None)
        else:
            raise RuntimeError(f"Unsupported command: {args.command}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
