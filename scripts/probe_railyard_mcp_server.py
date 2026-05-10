#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

import railyard_mcp_server
from workflow_schema import ensure_schema

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / ".workflow" / "workflow.db"

EXPECTED_TOOLS = {
    "resolve_lane": {"lane"},
    "get_ticket": {"lane", "ticket_id"},
    "list_tickets": {"lane", "status", "next_actor"},
    "next_ticket": {"lane", "actor"},
    "get_epic": {"lane", "epic_id"},
    "list_open_epics": {"lane"},
    "next_open_epic": {"lane"},
    "list_ticket_events": {"lane", "ticket_id", "limit"},
    "get_workflow_schema_version": set(),
    "dispatch_next_runner": {"lane", "runner_name"},
    "claim_ticket": {"lane", "ticket_id", "actor", "claimed_by"},
    "start_review": {"lane", "ticket_id", "claimed_by"},
    "mark_runner_result": {"lane", "ticket_id", "runner_result", "outbox_path"},
    "recover_stale_ticket": {"lane", "ticket_id", "actor", "reason", "dry_run"},
    "mark_review_result": {"lane", "ticket_id", "review_result", "supersedes_ticket_id"},
    "validate_result_payload": {"lane", "ticket_id", "outbox_path", "payload_json", "expected_runner_result"},
    "validate_ticket_state": {"lane", "ticket_id", "expected_status", "expected_actor"},
}

LANE_TOOLS = {name for name, params in EXPECTED_TOOLS.items() if "lane" in params}
FORBIDDEN_BROAD_MUTATION_TOOLS = {
    "sync_mailbox",
    "draft",
    "draft_ticket",
    "create_ticket",
    "update_ticket",
    "delete_ticket",
    "upsert_ticket",
    "write_ticket",
    "reset_ticket",
    "reset_lifecycle",
    "run_sql",
    "execute_sql",
}

PROBE_EPIC_ID = "SYSTEM-PROBE"
PROBE_TICKET_ID = "SYSTEM-PROBE-001"
PROBE_ARCHITECT_TICKET_ID = "SYSTEM-PROBE-002"
PROBE_STALE_TICKET_ID = "SYSTEM-PROBE-003"
PROBE_RESULT_PATH = pathlib.Path("docs/system/outbox/SYSTEM-PROBE-001.result.json")


class ProbeFailure(AssertionError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def db_counts(path: pathlib.Path) -> dict[str, int]:
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
        counts: dict[str, int] = {}
        for (table_name,) in rows:
            if table_name.startswith("sqlite_"):
                continue
            counts[table_name] = int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
        return counts
    finally:
        conn.close()


def normalize_content(value: Any) -> Any:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict):
        return normalize_content(value[1])
    if isinstance(value, dict):
        if "structuredContent" in value:
            return normalize_content(value["structuredContent"])
        return {str(key): normalize_content(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        normalized = [normalize_content(item) for item in value]
        if len(normalized) == 1:
            first = normalized[0]
            if isinstance(first, dict) and set(first) == {"text"}:
                text_value = first["text"]
                if isinstance(text_value, str):
                    try:
                        return json.loads(text_value)
                    except json.JSONDecodeError:
                        return text_value
        return normalized
    text = getattr(value, "text", None)
    if isinstance(text, str):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    structured = getattr(value, "structuredContent", None)
    if structured is not None:
        return normalize_content(structured)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return normalize_content(model_dump())
    return value


def input_schema(tool: Any) -> dict[str, Any]:
    for attr in ("inputSchema", "input_schema"):
        schema = getattr(tool, attr, None)
        if isinstance(schema, dict):
            return schema
    model_dump = getattr(tool, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        for key in ("inputSchema", "input_schema"):
            schema = dumped.get(key)
            if isinstance(schema, dict):
                return schema
    return {}


async def call_tool(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    return normalize_content(await server.call_tool(name, arguments))


async def expect_tool_error(server: Any, name: str, arguments: dict[str, Any], expected_text: str) -> str:
    try:
        await server.call_tool(name, arguments)
    except Exception as exc:  # MCP wraps tool exceptions by version; assert on the message only.
        message = str(exc)
        if expected_text not in message:
            raise ProbeFailure(f"{name} failed with unexpected error: {message}") from exc
        return message
    raise ProbeFailure(f"{name} accepted invalid arguments: {arguments}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def is_relative_to(path: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def workflow_state_roots(db_path: pathlib.Path, project_root: pathlib.Path) -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    project_workflow = project_root / ".workflow"
    roots.append(project_workflow.resolve())
    for candidate in [db_path.parent, *db_path.parents]:
        if candidate.name == ".workflow":
            roots.append(candidate.resolve())
            break

    unique: list[pathlib.Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return unique


def require_safe_probe_temp_root(temp_parent: pathlib.Path, db_path: pathlib.Path, project_root: pathlib.Path) -> None:
    resolved_temp_parent = temp_parent.resolve()
    for workflow_root in workflow_state_roots(db_path, project_root):
        if resolved_temp_parent == workflow_root or is_relative_to(resolved_temp_parent, workflow_root):
            raise ProbeFailure(
                "unsafe probe temp root: "
                f"{resolved_temp_parent} is inside workflow state directory {workflow_root}; "
                "use --temp-root outside any .workflow directory"
            )


def prepare_temp_project(temp_root: pathlib.Path) -> pathlib.Path:
    result_path = temp_root / PROBE_RESULT_PATH
    result_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticket_id": PROBE_TICKET_ID,
        "runner_status": "done",
        "summary": "MCP probe fixture result.",
        "files_changed": [],
        "validation": ["probe fixture"],
        "notes": [],
        "protocol_reads": [
            "railyard/SKILL.md",
            "railyard/references/roles.md",
            "railyard/references/startup-sequence.md",
        ],
        "created_at": utc_now(),
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    inbox_path = temp_root / "docs/system/inbox/SYSTEM-PROBE-001.md"
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    inbox_path.write_text(
        "\n".join(
            [
                "---",
                f"ticket_id: {PROBE_TICKET_ID}",
                f"epic_id: {PROBE_EPIC_ID}",
                "task_mode: general",
                "task_type: validation",
                "priority: high",
                f"outbox_result_path: {PROBE_RESULT_PATH.as_posix()}",
                "---",
                "",
                f"# {PROBE_TICKET_ID} - MCP probe fixture",
                "",
                "## Task",
                "",
                "Probe fixture.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return temp_root


def prepare_temp_db(temp_db: pathlib.Path) -> None:
    conn = sqlite3.connect(str(temp_db))
    try:
        ensure_schema(conn)
        now = utc_now()
        conn.execute(
            """
            UPDATE system_ticket
            SET status = 'superseded',
                next_actor = 'none',
                updated_at = ?
            WHERE status = 'ready'
              AND next_actor = 'runner'
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO system_epic (
                epic_id, title, status, priority, source, summary, blocked_by_epic_ids_json,
                blocked_by_external_json, preferred_entrypoints_json, done_definition_json,
                notes_json, linked_ticket_id, completed_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '[]', '[]', '[]', '[]', '[]', NULL, NULL, ?, ?)
            ON CONFLICT(epic_id) DO UPDATE SET
                title = excluded.title,
                status = excluded.status,
                priority = excluded.priority,
                summary = excluded.summary,
                updated_at = excluded.updated_at
            """,
            (PROBE_EPIC_ID, "MCP probe fixture", "queued", "high", "probe", "Fixture epic for MCP smoke probe.", now, now),
        )
        ticket_rows = [
            (PROBE_TICKET_ID, "ready", "runner", None, None),
            (PROBE_ARCHITECT_TICKET_ID, "awaiting_review", "architect", "done", None),
            (PROBE_STALE_TICKET_ID, "ready", "runner", None, None),
        ]
        for ticket_id, status, next_actor, runner_result, review_result in ticket_rows:
            conn.execute(
                """
                INSERT INTO system_ticket (
                    ticket_id, epic_id, task_mode, task_type, priority, inbox_path, outbox_path,
                    status, next_actor, runner_result, review_result, supersedes_ticket_id,
                    parent_ticket_id, summary, claimed_by, claimed_at, created_at, updated_at
                )
                VALUES (?, ?, 'general', 'validation', 'high', ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, NULL, ?, ?)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    epic_id = excluded.epic_id,
                    status = excluded.status,
                    next_actor = excluded.next_actor,
                    runner_result = excluded.runner_result,
                    review_result = excluded.review_result,
                    outbox_path = excluded.outbox_path,
                    claimed_by = NULL,
                    claimed_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    ticket_id,
                    PROBE_EPIC_ID,
                    f"docs/system/inbox/{ticket_id}.md",
                    f"docs/system/outbox/{ticket_id}.result.json",
                    status,
                    next_actor,
                    runner_result,
                    review_result,
                    "Fixture ticket for MCP smoke probe.",
                    now,
                    now,
                ),
            )
        conn.execute(
            """
            INSERT INTO workflow_event (
                lane, object_type, object_id, actor, action, from_status, to_status, payload_json, created_at
            )
            VALUES ('system', 'ticket', ?, 'probe', 'seed', NULL, 'ready', ?, ?)
            """,
            (PROBE_TICKET_ID, json_dumps({"fixture": True}), now),
        )
        conn.commit()
    finally:
        conn.close()


async def run_probe(
    db_path: pathlib.Path,
    project_root: pathlib.Path,
    keep_temp: bool,
    temp_root: pathlib.Path | None,
) -> dict[str, Any]:
    require(db_path.exists(), f"database not found: {db_path}")
    live_before_hash = sha256_file(db_path)
    live_before_counts = db_counts(db_path)
    checks: list[dict[str, Any]] = []
    temp_parent = temp_root.resolve() if temp_root else ROOT / ".tmp" / "railyard-mcp-probe"
    require_safe_probe_temp_root(temp_parent, db_path, project_root)
    temp_parent.mkdir(parents=True, exist_ok=True)

    temp_dir = temp_parent / f"railyard-mcp-probe-{uuid.uuid4().hex[:12]}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    try:
        temp_db = temp_dir / "workflow.db"
        shutil.copy2(db_path, temp_db)
        temp_project_root = prepare_temp_project(temp_dir / "project")
        prepare_temp_db(temp_db)

        config = railyard_mcp_server.ServerConfig(db_path=temp_db, project_root=temp_project_root)
        server = railyard_mcp_server.create_server(config)
        tools = await server.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        tool_names = set(tools_by_name)

        missing = sorted(set(EXPECTED_TOOLS) - tool_names)
        forbidden = sorted(FORBIDDEN_BROAD_MUTATION_TOOLS & tool_names)
        require(not missing, f"missing expected MCP tools: {', '.join(missing)}")
        require(not forbidden, f"forbidden broad mutation MCP tools are exposed: {', '.join(forbidden)}")
        checks.append({"name": "surface", "status": "ok", "tools": sorted(tool_names)})

        for tool_name, expected_params in sorted(EXPECTED_TOOLS.items()):
            schema = input_schema(tools_by_name[tool_name])
            properties = set((schema.get("properties") or {}).keys())
            absent = sorted(expected_params - properties)
            require(not absent, f"{tool_name} schema missing parameters: {', '.join(absent)}")
            if tool_name in LANE_TOOLS:
                require("lane" in properties, f"{tool_name} must expose explicit lane parameter")
        checks.append({"name": "schemas", "status": "ok", "lane_tools": sorted(LANE_TOOLS)})

        for tool_name in sorted(LANE_TOOLS):
            args: dict[str, Any] = {"lane": "bad-lane"}
            args.update(
                {
                    "ticket_id": PROBE_TICKET_ID,
                    "epic_id": PROBE_EPIC_ID,
                    "actor": "runner",
                    "runner_name": "probe-runner",
                    "claimed_by": "probe",
                    "runner_result": "done",
                    "review_result": "accept",
                    "reason": "probe validation",
                    "dry_run": True,
                    "expected_status": "ready",
                    "expected_actor": "runner",
                }
            )
            await expect_tool_error(server, tool_name, args, "explicit lane is required")
        checks.append({"name": "lane_parameter_validation", "status": "ok", "tools": sorted(LANE_TOOLS)})

        resolved = await call_tool(server, "resolve_lane", {"lane": "system"})
        require(resolved.get("lane") == "system", "resolve_lane did not normalize system lane")
        ticket = await call_tool(server, "get_ticket", {"lane": "system", "ticket_id": PROBE_TICKET_ID})
        require(ticket["ticket"]["ticket_id"] == PROBE_TICKET_ID, "get_ticket returned wrong ticket")
        listed = await call_tool(server, "list_tickets", {"lane": "system", "status": "ready", "next_actor": "runner"})
        require(any(item["ticket_id"] == PROBE_TICKET_ID for item in listed["tickets"]), "list_tickets omitted probe ticket")
        next_ticket = await call_tool(server, "next_ticket", {"lane": "system", "actor": "runner"})
        require(next_ticket["ticket"]["ticket_id"] == PROBE_TICKET_ID, "next_ticket did not return probe ticket")
        epic = await call_tool(server, "get_epic", {"lane": "system", "epic_id": PROBE_EPIC_ID})
        require(epic["epic"]["epic_id"] == PROBE_EPIC_ID, "get_epic returned wrong epic")
        open_epics = await call_tool(server, "list_open_epics", {"lane": "system"})
        require(any(item["epic_id"] == PROBE_EPIC_ID for item in open_epics["epics"]), "list_open_epics omitted probe epic")
        next_epic = await call_tool(server, "next_open_epic", {"lane": "system"})
        require(next_epic["epic"] is not None, "next_open_epic returned no epic")
        events = await call_tool(server, "list_ticket_events", {"lane": "system", "ticket_id": PROBE_TICKET_ID, "limit": 5})
        require(events["events"], "list_ticket_events returned no probe events")
        schema_version = await call_tool(server, "get_workflow_schema_version", {})
        require(schema_version["schema_versions"], "get_workflow_schema_version returned no versions")
        checks.append({"name": "representative_reads", "status": "ok"})

        dispatch = await call_tool(server, "dispatch_next_runner", {"lane": "system", "runner_name": "probe-runner"})
        require(dispatch["status"] == "ready", "dispatch_next_runner did not return ready")
        require(dispatch["ticket"]["ticket_id"] == PROBE_TICKET_ID, "dispatch_next_runner returned wrong ticket")
        require(dispatch["spawn"]["contract"] == "railyard.runner_dispatch.v3", "dispatch_next_runner returned wrong dispatch contract")
        require(dispatch["spawn"]["agent_type"] is None, "dispatch_next_runner must not hardcode platform agent_type")
        require(dispatch["spawn"]["fallback_profile"] == "railyard-runner", "dispatch_next_runner omitted fallback profile")
        require(
            dispatch["spawn"]["profile_priority"] == "fallback_after_platform_native",
            "dispatch_next_runner returned wrong profile priority",
        )
        require(
            "write" in dispatch["spawn"]["required_capabilities"],
            "dispatch_next_runner omitted runner write capability",
        )
        require(
            "read_only" in dispatch["spawn"]["reject_if_only"],
            "dispatch_next_runner omitted read-only rejection category",
        )
        require(
            dispatch["spawn"]["capability_match_policy"] == "conservative_fuzzy",
            "dispatch_next_runner returned wrong capability match policy",
        )
        require(dispatch["spawn"]["runner_name"] == "probe-runner", "dispatch_next_runner omitted runner_name")
        startup_reads = dispatch["spawn"].get("required_startup_reads")
        require(isinstance(startup_reads, list) and startup_reads, "dispatch_next_runner omitted required_startup_reads")
        require("railyard/references/roles.md" in startup_reads, "dispatch_next_runner omitted roles.md startup read")
        require(
            "railyard/references/startup-sequence.md" in startup_reads,
            "dispatch_next_runner omitted startup-sequence.md startup read",
        )
        prompt = dispatch["spawn"].get("prompt")
        require(isinstance(prompt, str) and "Before claiming or editing anything" in prompt, "runner prompt omitted startup read gate")
        require("protocol_reads" in prompt, "runner prompt omitted protocol_reads result evidence")
        checks.append({"name": "dispatch", "status": "ok"})

        invalid_payload = await call_tool(
            server,
            "validate_result_payload",
            {"lane": "system", "ticket_id": PROBE_TICKET_ID, "payload_json": json_dumps({"ticket_id": PROBE_TICKET_ID})},
        )
        require(invalid_payload["valid"] is False, "validate_result_payload accepted incomplete payload")
        valid_payload = await call_tool(
            server,
            "validate_result_payload",
            {
                "lane": "system",
                "ticket_id": PROBE_TICKET_ID,
                "payload_json": json_dumps(
                    {
                        "ticket_id": PROBE_TICKET_ID,
                        "runner_status": "done",
                        "summary": "Valid probe payload.",
                        "files_changed": [],
                        "validation": [],
                        "notes": [],
                        "protocol_reads": [
                            "railyard/SKILL.md",
                            "railyard/references/roles.md",
                            "railyard/references/startup-sequence.md",
                        ],
                        "created_at": utc_now(),
                    }
                ),
                "expected_runner_result": "done",
            },
        )
        require(valid_payload["valid"] is True, "validate_result_payload rejected valid payload")
        state = await call_tool(
            server,
            "validate_ticket_state",
            {"lane": "system", "ticket_id": PROBE_TICKET_ID, "expected_status": "ready", "expected_actor": "runner"},
        )
        require(state["valid"] is True, "validate_ticket_state rejected expected fixture state")
        checks.append({"name": "validation_tools", "status": "ok"})

        claimed = await call_tool(
            server,
            "claim_ticket",
            {"lane": "system", "ticket_id": PROBE_TICKET_ID, "actor": "runner", "claimed_by": "probe-runner"},
        )
        require(claimed["ticket"]["status"] == "running", "claim_ticket did not move ticket to running")
        runner_done = await call_tool(
            server,
            "mark_runner_result",
            {
                "lane": "system",
                "ticket_id": PROBE_TICKET_ID,
                "runner_result": "done",
                "outbox_path": str(PROBE_RESULT_PATH),
            },
        )
        require(runner_done["ticket"]["status"] == "awaiting_review", "mark_runner_result did not queue review")
        review_started = await call_tool(
            server,
            "start_review",
            {"lane": "system", "ticket_id": PROBE_ARCHITECT_TICKET_ID, "claimed_by": "probe-architect"},
        )
        require(review_started["ticket"]["status"] == "in_review", "start_review did not move ticket to in_review")
        reviewed = await call_tool(
            server,
            "mark_review_result",
            {"lane": "system", "ticket_id": PROBE_ARCHITECT_TICKET_ID, "review_result": "accept"},
        )
        require(reviewed["ticket"]["status"] == "finalised", "mark_review_result did not finalise accepted ticket")
        checks.append({"name": "narrow_write_tools", "status": "ok"})

        stale_claimed = await call_tool(
            server,
            "claim_ticket",
            {"lane": "system", "ticket_id": PROBE_STALE_TICKET_ID, "actor": "runner", "claimed_by": "interrupted-runner"},
        )
        require(stale_claimed["ticket"]["status"] == "running", "stale fixture claim did not move ticket to running")
        stale_dry_run = await call_tool(
            server,
            "recover_stale_ticket",
            {
                "lane": "system",
                "ticket_id": PROBE_STALE_TICKET_ID,
                "actor": "runner",
                "reason": "probe runner interrupted before outbox",
                "dry_run": True,
            },
        )
        require(stale_dry_run["status"] == "dry_run", "recover_stale_ticket dry-run returned wrong status")
        require(stale_dry_run["to"]["status"] == "ready", "recover_stale_ticket dry-run did not preview ready")
        stale_recovered = await call_tool(
            server,
            "recover_stale_ticket",
            {
                "lane": "system",
                "ticket_id": PROBE_STALE_TICKET_ID,
                "actor": "runner",
                "reason": "probe runner interrupted before outbox",
            },
        )
        require(stale_recovered["ticket"]["status"] == "ready", "recover_stale_ticket did not reset to ready")
        require(stale_recovered["ticket"]["claimed_by"] is None, "recover_stale_ticket did not clear claimed_by")
        stale_events = await call_tool(
            server,
            "list_ticket_events",
            {"lane": "system", "ticket_id": PROBE_STALE_TICKET_ID, "limit": 5},
        )
        require(
            any(item["action"] == "recover-stale-running" for item in stale_events["events"]),
            "recover_stale_ticket did not record recover-stale-running event",
        )
        checks.append({"name": "stale_running_recovery", "status": "ok"})

        temp_counts = db_counts(temp_db)
        temp_info = {"temp_db": str(temp_db), "temp_project_root": str(temp_project_root), "counts": temp_counts}
        if keep_temp:
            keep_dir = temp_parent / "probe-last"
            if keep_dir.exists():
                shutil.rmtree(keep_dir)
            shutil.copytree(temp_dir, keep_dir)
            temp_info["kept_at"] = str(keep_dir)
    finally:
        if not keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)

    live_after_hash = sha256_file(db_path)
    live_after_counts = db_counts(db_path)
    require(live_after_hash == live_before_hash, "live DB hash changed during probe")
    require(live_after_counts == live_before_counts, "live DB table counts changed during probe")
    checks.append(
        {
            "name": "live_db_unchanged",
            "status": "ok",
            "db_path": str(db_path),
            "sha256": live_after_hash,
            "counts": live_after_counts,
        }
    )

    return {
        "status": "ok",
        "db_path": str(db_path),
        "project_root": str(project_root),
        "checks": checks,
        "temp": temp_info,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the Railyard MCP-lite server against a temporary DB copy.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Live or test workflow DB to copy before probing.")
    parser.add_argument("--project-root", default=str(ROOT), help="Source project root used only for path resolution.")
    parser.add_argument("--temp-root", default="", help="Optional temp directory root for probe working files.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep the final temp probe directory under the probe temp root.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = pathlib.Path(args.db).resolve()
    project_root = pathlib.Path(args.project_root).resolve()
    temp_root = pathlib.Path(args.temp_root).resolve() if args.temp_root else None
    try:
        payload = asyncio.run(run_probe(db_path, project_root, args.keep_temp, temp_root))
    except Exception as exc:
        error_payload = {
            "status": "failed",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "db_path": str(db_path),
        }
        print(json.dumps(error_payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
