#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import epic as epic_helper
import ticket as ticket_helper
from architect import build_runner_spawn_payload
from workflow_schema import ensure_schema

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / ".workflow" / "workflow.db"
VALID_LANES = {"domain", "system"}
VALID_ACTORS = {"architect", "runner"}
REQUIRED_READ_TOOLS = [
    "get_ticket",
    "list_tickets",
    "next_ticket",
    "get_epic",
    "list_open_epics",
    "next_open_epic",
    "list_ticket_events",
    "get_workflow_schema_version",
]
REQUIRED_RESULT_FIELDS = ticket_helper.REQUIRED_RESULT_FIELDS


@dataclass(frozen=True)
class ServerConfig:
    db_path: pathlib.Path
    project_root: pathlib.Path


def resolve_path(path_value: str, base: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(path_value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def resolve_lane(lane: str) -> str:
    normalized = (lane or "").strip().lower()
    if normalized not in VALID_LANES:
        expected = ", ".join(sorted(VALID_LANES))
        raise ValueError(f"explicit lane is required and must be one of: {expected}")
    return normalized


def resolve_actor(actor: str) -> str:
    normalized = (actor or "").strip().lower()
    if normalized not in VALID_ACTORS:
        expected = ", ".join(sorted(VALID_ACTORS))
        raise ValueError(f"actor must be one of: {expected}")
    return normalized


@contextmanager
def open_connection(db_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def open_write_connection(db_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def require_value(value: str, name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    return []


def parse_json_object(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{name} must be a JSON object")


def normalize_ticket(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket_id": row.get("ticket_id"),
        "epic_id": row.get("epic_id"),
        "task_mode": row.get("task_mode"),
        "task_type": row.get("task_type"),
        "priority": row.get("priority"),
        "status": row.get("status"),
        "next_actor": row.get("next_actor"),
        "runner_result": row.get("runner_result"),
        "review_result": row.get("review_result"),
        "supersedes_ticket_id": row.get("supersedes_ticket_id"),
        "parent_ticket_id": row.get("parent_ticket_id"),
        "summary": row.get("summary"),
        "inbox_path": row.get("inbox_path"),
        "outbox_path": row.get("outbox_path"),
        "claimed_by": row.get("claimed_by"),
        "claimed_at": row.get("claimed_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def normalize_lifecycle_row(lane: str, row: dict[str, Any]) -> dict[str, Any]:
    return {"lane": lane, "ticket": normalize_ticket(row)}


def apply_mark_review_result(
    config: ServerConfig,
    lane: str,
    ticket_id: str,
    review_result: str,
    supersedes_ticket_id: str = "",
    validator_report_record: str = "",
) -> dict[str, Any]:
    resolved_lane = resolve_lane(lane)
    resolved_ticket_id = require_value(ticket_id, "ticket_id")
    normalized_review_result = require_value(review_result, "review_result")
    with open_write_connection(config.db_path) as conn:
        row = ticket_helper.command_mark_review_result(
            conn,
            resolved_lane,
            resolved_ticket_id,
            normalized_review_result,
            supersedes_ticket_id.strip() or None,
            project_root=config.project_root,
            validator_report_record_path=validator_report_record.strip() or None,
        )
    return normalize_lifecycle_row(resolved_lane, row)


def resolve_result_path(project_root: pathlib.Path, row: dict[str, Any], outbox_path: str) -> pathlib.Path:
    result_hint = outbox_path.strip() or str(row.get("outbox_path") or "")
    if not result_hint:
        raise ValueError(f"outbox_path is required for {row.get('ticket_id')}")
    return ticket_helper.resolve_project_path(project_root, result_hint)


def validate_result_payload_details(
    payload: dict[str, Any],
    ticket_id: str,
    expected_runner_result: str = "",
) -> dict[str, Any]:
    errors: list[str] = []
    missing = sorted(REQUIRED_RESULT_FIELDS - set(payload))
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    if payload.get("ticket_id") != ticket_id:
        errors.append(f"ticket_id mismatch: expected {ticket_id}, got {payload.get('ticket_id')}")
    runner_status = payload.get("runner_status")
    if runner_status not in ticket_helper.VALID_RUNNER_RESULTS:
        errors.append(f"runner_status must be one of {sorted(ticket_helper.VALID_RUNNER_RESULTS)}")
    if expected_runner_result and runner_status != expected_runner_result:
        errors.append(f"runner_status mismatch: expected {expected_runner_result}, got {runner_status}")
    for key in ("files_changed", "validation", "notes", "protocol_reads", "evidence"):
        if key in payload and not isinstance(payload.get(key), list):
            errors.append(f"{key} must be an array")
    protocol_reads = payload.get("protocol_reads")
    if "protocol_reads" in payload:
        if not isinstance(protocol_reads, list) or not protocol_reads:
            errors.append("protocol_reads must be a non-empty array")
        elif not all(isinstance(item, str) and item.strip() for item in protocol_reads):
            errors.append("protocol_reads must be an array of non-empty strings")
    evidence = payload.get("evidence")
    if "evidence" in payload:
        if not isinstance(evidence, list):
            errors.append("evidence must be an array")
        elif evidence and not all(isinstance(item, str) and item.strip() for item in evidence):
            errors.append("evidence must be an array of non-empty strings")
    confidence = payload.get("confidence")
    if "confidence" in payload and confidence not in {"high", "medium", "low"}:
        errors.append(f"confidence must be one of {{'high', 'medium', 'low'}}, got {confidence!r}")
    if "summary" in payload and (not isinstance(payload.get("summary"), str) or not payload["summary"].strip()):
        errors.append("summary must be a non-empty string")
    if "created_at" in payload and (not isinstance(payload.get("created_at"), str) or not payload["created_at"].strip()):
        errors.append("created_at must be a non-empty string")
    return {
        "valid": not errors,
        "ticket_id": ticket_id,
        "runner_status": runner_status if isinstance(runner_status, str) else None,
        "expected_runner_result": expected_runner_result or None,
        "missing_fields": missing,
        "errors": errors,
    }


def normalize_epic(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "epic_id": row.get("epic_id"),
        "title": row.get("title"),
        "status": row.get("status"),
        "priority": row.get("priority"),
        "source": row.get("source"),
        "summary": row.get("summary"),
        "blocked_by_epic_ids": parse_json_list(row.get("blocked_by_epic_ids_json")),
        "blocked_by_external": parse_json_list(row.get("blocked_by_external_json")),
        "preferred_entrypoints": parse_json_list(row.get("preferred_entrypoints_json")),
        "done_definition": parse_json_list(row.get("done_definition_json")),
        "notes": parse_json_list(row.get("notes_json")),
        "blocking_epic_ids": row.get("blocking_epic_ids", []),
        "blocking_external_dependencies": row.get("blocking_external_dependencies", []),
        "dependency_state": row.get("dependency_state"),
        "actionable": row.get("actionable"),
        "linked_ticket_id": row.get("linked_ticket_id"),
        "completed_at": row.get("completed_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def fetch_schema_versions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT component, version, updated_at FROM schema_version ORDER BY component").fetchall()
    return [dict(row) for row in rows]


def create_server(config: ServerConfig) -> Any:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("Railyard MCP-lite")

    @server.tool(name="resolve_lane")
    def resolve_lane_tool(lane: str) -> dict[str, str]:
        resolved_lane = resolve_lane(lane)
        return {
            "lane": resolved_lane,
            "epic_table": f"{resolved_lane}_epic",
            "ticket_table": f"{resolved_lane}_ticket",
        }

    @server.tool(name="get_ticket")
    def get_ticket(lane: str, ticket_id: str) -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_ticket_id = require_value(ticket_id, "ticket_id")
        with open_connection(config.db_path) as conn:
            row = ticket_helper.fetch_row(conn, resolved_lane, resolved_ticket_id)
        if row is None:
            raise RuntimeError(f"{resolved_lane}_ticket row not found for {resolved_ticket_id}")
        return {"lane": resolved_lane, "ticket": normalize_ticket(row)}

    @server.tool(name="list_tickets")
    def list_tickets(lane: str, status: str = "", next_actor: str = "") -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        with open_connection(config.db_path) as conn:
            rows = ticket_helper.command_list(conn, resolved_lane, status.strip() or None, next_actor.strip() or None)
        return {"lane": resolved_lane, "tickets": [normalize_ticket(row) for row in rows]}

    @server.tool(name="next_ticket")
    def next_ticket(lane: str, actor: str) -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_actor = resolve_actor(actor)
        with open_connection(config.db_path) as conn:
            row = ticket_helper.command_next(conn, resolved_lane, resolved_actor)
        return {"lane": resolved_lane, "actor": resolved_actor, "ticket": normalize_ticket(row) if row else None}

    @server.tool(name="get_epic")
    def get_epic(lane: str, epic_id: str) -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_epic_id = require_value(epic_id, "epic_id")
        with open_connection(config.db_path) as conn:
            row = epic_helper.command_show(conn, resolved_lane, resolved_epic_id)
        return {"lane": resolved_lane, "epic": normalize_epic(row)}

    @server.tool(name="list_open_epics")
    def list_open_epics(lane: str) -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        with open_connection(config.db_path) as conn:
            rows = epic_helper.sorted_open_rows(conn, resolved_lane)
        return {"lane": resolved_lane, "epics": [normalize_epic(row) for row in rows]}

    @server.tool(name="next_open_epic")
    def next_open_epic(lane: str) -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        with open_connection(config.db_path) as conn:
            rows = epic_helper.sorted_open_rows(conn, resolved_lane)
        return {"lane": resolved_lane, "epic": normalize_epic(rows[0]) if rows else None}

    @server.tool(name="list_ticket_events")
    def list_ticket_events(lane: str, ticket_id: str, limit: int = 20) -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_ticket_id = require_value(ticket_id, "ticket_id")
        normalized_limit = max(1, min(int(limit), 100))
        with open_connection(config.db_path) as conn:
            events = ticket_helper.command_events(conn, resolved_lane, resolved_ticket_id, normalized_limit)
        return {"lane": resolved_lane, "ticket_id": resolved_ticket_id, "events": events}

    @server.tool(name="get_workflow_schema_version")
    def get_workflow_schema_version() -> dict[str, Any]:
        with open_connection(config.db_path) as conn:
            versions = fetch_schema_versions(conn)
        return {"schema_versions": versions}

    @server.tool(name="dispatch_next_runner")
    def dispatch_next_runner(lane: str, runner_name: str = "runner-1") -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_runner_name = require_value(runner_name, "runner_name")
        with open_write_connection(config.db_path) as conn:
            sync_payload = ticket_helper.command_sync_mailbox(
                conn,
                config.project_root,
                resolved_lane,
                ticket_id=None,
                reset_lifecycle=False,
            )
            ticket = ticket_helper.command_next(conn, resolved_lane, "runner")
        if ticket is None:
            return {"status": "idle", "lane": resolved_lane, "synced": sync_payload, "ticket": None}
        return {
            "status": "ready",
            "lane": resolved_lane,
            "synced": sync_payload,
            "ticket": ticket,
            "spawn": build_runner_spawn_payload(resolved_lane, ticket, resolved_runner_name),
        }

    @server.tool(name="claim_ticket")
    def claim_ticket(lane: str, ticket_id: str, actor: str, claimed_by: str = "") -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_ticket_id = require_value(ticket_id, "ticket_id")
        resolved_actor = resolve_actor(actor)
        with open_write_connection(config.db_path) as conn:
            row = ticket_helper.command_claim(
                conn,
                resolved_lane,
                resolved_ticket_id,
                resolved_actor,
                claimed_by.strip() or None,
            )
        return normalize_lifecycle_row(resolved_lane, row)

    @server.tool(name="start_review")
    def start_review(lane: str, ticket_id: str, claimed_by: str = "") -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_ticket_id = require_value(ticket_id, "ticket_id")
        with open_write_connection(config.db_path) as conn:
            row = ticket_helper.command_claim(
                conn,
                resolved_lane,
                resolved_ticket_id,
                "architect",
                claimed_by.strip() or None,
                action="start-review",
            )
        return normalize_lifecycle_row(resolved_lane, row)

    @server.tool(name="mark_runner_result")
    def mark_runner_result(lane: str, ticket_id: str, runner_result: str, outbox_path: str = "") -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_ticket_id = require_value(ticket_id, "ticket_id")
        normalized_runner_result = require_value(runner_result, "runner_result")
        with open_write_connection(config.db_path) as conn:
            row = ticket_helper.command_mark_runner_result(
                conn,
                config.project_root,
                resolved_lane,
                resolved_ticket_id,
                normalized_runner_result,
                outbox_path.strip() or None,
            )
        return normalize_lifecycle_row(resolved_lane, row)

    @server.tool(name="recover_stale_ticket")
    def recover_stale_ticket(lane: str, ticket_id: str, actor: str, reason: str, dry_run: bool = False) -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_ticket_id = require_value(ticket_id, "ticket_id")
        resolved_actor = resolve_actor(actor)
        normalized_reason = require_value(reason, "reason")
        with open_write_connection(config.db_path) as conn:
            row = ticket_helper.command_recover_stale(
                conn,
                config.project_root,
                resolved_lane,
                resolved_ticket_id,
                resolved_actor,
                normalized_reason,
                dry_run=dry_run,
            )
        if dry_run:
            row["lane"] = resolved_lane
            return row
        return normalize_lifecycle_row(resolved_lane, row)

    @server.tool(name="mark_review_result")
    def mark_review_result(
        lane: str,
        ticket_id: str,
        review_result: str,
        supersedes_ticket_id: str = "",
        validator_report_record: str = "",
    ) -> dict[str, Any]:
        return apply_mark_review_result(
            config,
            lane,
            ticket_id,
            review_result,
            supersedes_ticket_id,
            validator_report_record,
        )

    @server.tool(name="validate_result_payload")
    def validate_result_payload(
        lane: str,
        ticket_id: str,
        outbox_path: str = "",
        payload_json: str = "",
        expected_runner_result: str = "",
    ) -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_ticket_id = require_value(ticket_id, "ticket_id")
        if payload_json.strip():
            payload = parse_json_object(payload_json, "payload_json")
            result_path = None
        else:
            with open_connection(config.db_path) as conn:
                row = ticket_helper.fetch_row(conn, resolved_lane, resolved_ticket_id)
            if row is None:
                raise RuntimeError(f"{resolved_lane}_ticket row not found for {resolved_ticket_id}")
            result_path = resolve_result_path(config.project_root, row, outbox_path)
            payload = ticket_helper.read_json(result_path)
        details = validate_result_payload_details(payload, resolved_ticket_id, expected_runner_result.strip())
        details["lane"] = resolved_lane
        details["outbox_path"] = str(result_path) if result_path else None
        return details

    @server.tool(name="validate_ticket_state")
    def validate_ticket_state(
        lane: str,
        ticket_id: str,
        expected_status: str,
        expected_actor: str,
    ) -> dict[str, Any]:
        resolved_lane = resolve_lane(lane)
        resolved_ticket_id = require_value(ticket_id, "ticket_id")
        normalized_expected_status = require_value(expected_status, "expected_status")
        normalized_expected_actor = require_value(expected_actor, "expected_actor")
        with open_connection(config.db_path) as conn:
            row = ticket_helper.fetch_row(conn, resolved_lane, resolved_ticket_id)
        if row is None:
            return {
                "valid": False,
                "lane": resolved_lane,
                "ticket_id": resolved_ticket_id,
                "expected_status": normalized_expected_status,
                "expected_actor": normalized_expected_actor,
                "actual_status": None,
                "actual_actor": None,
                "errors": [f"{resolved_lane}_ticket row not found for {resolved_ticket_id}"],
                "ticket": None,
            }
        errors = []
        if row.get("status") != normalized_expected_status:
            errors.append(f"status mismatch: expected {normalized_expected_status}, got {row.get('status')}")
        if row.get("next_actor") != normalized_expected_actor:
            errors.append(f"next_actor mismatch: expected {normalized_expected_actor}, got {row.get('next_actor')}")
        return {
            "valid": not errors,
            "lane": resolved_lane,
            "ticket_id": resolved_ticket_id,
            "expected_status": normalized_expected_status,
            "expected_actor": normalized_expected_actor,
            "actual_status": row.get("status"),
            "actual_actor": row.get("next_actor"),
            "errors": errors,
            "ticket": normalize_ticket(row),
        }

    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Railyard MCP-lite stdio server scaffold.",
        epilog=(
            "Examples:\n"
            "  python scripts/railyard_mcp_server.py --db .workflow/workflow.db --project-root .\n"
            "  python scripts/railyard_mcp_server.py --db ../project/.workflow/workflow.db --project-root ../project"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to the Railyard workflow SQLite database.")
    parser.add_argument("--project-root", default=str(ROOT), help="Path to the Railyard project root.")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> ServerConfig:
    project_root = resolve_path(str(args.project_root), ROOT)
    db_path = resolve_path(str(args.db), project_root)
    return ServerConfig(db_path=db_path, project_root=project_root)


def main() -> int:
    args = parse_args()
    config = build_config(args)
    server = create_server(config)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
