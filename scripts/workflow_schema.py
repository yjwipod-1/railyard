#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS domain_epic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epic_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    source TEXT,
    summary TEXT,
    blocked_by_epic_ids_json TEXT NOT NULL DEFAULT '[]',
    blocked_by_external_json TEXT NOT NULL DEFAULT '[]',
    preferred_entrypoints_json TEXT NOT NULL DEFAULT '[]',
    done_definition_json TEXT NOT NULL DEFAULT '[]',
    notes_json TEXT NOT NULL DEFAULT '[]',
    linked_ticket_id TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_domain_epic_status_priority ON domain_epic(status, priority, updated_at DESC);

CREATE TABLE IF NOT EXISTS domain_ticket (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL UNIQUE,
    epic_id TEXT NOT NULL,
    task_mode TEXT NOT NULL,
    task_type TEXT,
    priority TEXT NOT NULL,
    inbox_path TEXT NOT NULL,
    outbox_path TEXT,
    status TEXT NOT NULL,
    next_actor TEXT NOT NULL,
    runner_result TEXT,
    review_result TEXT,
    supersedes_ticket_id TEXT,
    parent_ticket_id TEXT,
    summary TEXT,
    claimed_by TEXT,
    claimed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_domain_ticket_status_actor ON domain_ticket(status, next_actor, id DESC);
CREATE INDEX IF NOT EXISTS idx_domain_ticket_epic ON domain_ticket(epic_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS system_epic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epic_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    source TEXT,
    summary TEXT,
    blocked_by_epic_ids_json TEXT NOT NULL DEFAULT '[]',
    blocked_by_external_json TEXT NOT NULL DEFAULT '[]',
    preferred_entrypoints_json TEXT NOT NULL DEFAULT '[]',
    done_definition_json TEXT NOT NULL DEFAULT '[]',
    notes_json TEXT NOT NULL DEFAULT '[]',
    linked_ticket_id TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_system_epic_status_priority ON system_epic(status, priority, updated_at DESC);

CREATE TABLE IF NOT EXISTS system_ticket (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL UNIQUE,
    epic_id TEXT NOT NULL,
    task_mode TEXT NOT NULL,
    task_type TEXT,
    priority TEXT NOT NULL,
    inbox_path TEXT NOT NULL,
    outbox_path TEXT,
    status TEXT NOT NULL,
    next_actor TEXT NOT NULL,
    runner_result TEXT,
    review_result TEXT,
    supersedes_ticket_id TEXT,
    parent_ticket_id TEXT,
    summary TEXT,
    claimed_by TEXT,
    claimed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_system_ticket_status_actor ON system_ticket(status, next_actor, id DESC);
CREATE INDEX IF NOT EXISTS idx_system_ticket_epic ON system_ticket(epic_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS workflow_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lane TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    actor TEXT,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_event_object ON workflow_event(lane, object_type, object_id, id DESC);

CREATE TABLE IF NOT EXISTS schema_version (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.execute(
        "INSERT INTO schema_version(component, version, updated_at) VALUES ('railyard', 2, datetime('now')) "
        "ON CONFLICT(component) DO UPDATE SET version = excluded.version, updated_at = excluded.updated_at"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize or inspect the Railyard workflow SQLite schema.",
        epilog=(
            "Helper commands:\n"
            "  python railyard/scripts/workflow_schema.py ensure --db .workflow/workflow.db\n"
            "  python railyard/scripts/workflow_schema.py tables --db .workflow/workflow.db"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure_parser = subparsers.add_parser("ensure", help="Create the Railyard workflow schema if missing.")
    ensure_parser.add_argument("--db", required=True, help="Path to the SQLite database file.")

    tables_parser = subparsers.add_parser("tables", help="List workflow tables.")
    tables_parser.add_argument("--db", required=True, help="Path to the SQLite database file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = pathlib.Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        conn.commit()
        if args.command == "ensure":
            payload = {
                "status": "ok",
                "db_path": str(db_path),
                "tables": ["domain_epic", "domain_ticket", "system_epic", "system_ticket", "workflow_event", "schema_version"],
            }
        else:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('domain_epic','domain_ticket','system_epic','system_ticket','workflow_event','schema_version') ORDER BY name"
            ).fetchall()
            payload = {"status": "ok", "db_path": str(db_path), "tables": [row[0] for row in rows]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
