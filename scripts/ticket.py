#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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
DRAFTED_STATUS = "drafted"
FINAL_STATUS = "finalised"
SUPERSEDED_STATUS = "superseded"

VALID_RUNNER_RESULTS = {"done", "partial", "blocked", "invalid"}
VALID_REVIEW_RESULTS = {"accept", "accept_with_changes", "reject", "redesign"}
VALID_PRIORITIES = {"high", "medium", "low"}
VALID_VALIDATOR_RISK_LEVELS = {"low", "medium", "high"}
VALID_VALIDATOR_VERDICTS = {"pass", "fail", "blocked", "inconclusive", "human_review_required"}
VALID_VALIDATOR_CONFIDENCE = {"high", "medium", "low"}
VALID_VALIDATOR_FINDING_SEVERITIES = {"error", "warn", "info"}
VALID_VALIDATOR_FINDING_STATUSES = {"pass", "fail", "not_applicable", "blocked", "inconclusive"}
VALIDATOR_REPORT_RECORD_TYPE = "railyard.validator_report_reference.v1"
VALIDATOR_REQUIRED_METADATA_FIELDS = {
    "validator_risk_level",
    "validator_contract_source",
    "validator_expected_artifacts",
    "validator_evidence_pack",
    "validator_failure_behavior",
}
VALID_BLOCKER_CATEGORIES = {
    "permission_denied",
    "command_failed",
    "sandbox_boundary",
    "authorization_required",
    "environment_issue",
    "unresolved_dependency",
}
PRIORITY_RANK_SQL = "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 9 END"
STATUS_RANK_SQL = "CASE status WHEN 'awaiting_review' THEN 0 WHEN 'drafted' THEN 1 WHEN 'ready' THEN 2 ELSE 9 END"
REQUIRED_RESULT_FIELDS = {
    "ticket_id",
    "runner_status",
    "summary",
    "files_changed",
    "validation",
    "notes",
    "created_at",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


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


def resolve_project_path(project_root: pathlib.Path, raw_path: str) -> pathlib.Path:
    path = pathlib.Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def markdown_list(items: list[str], fallback: str = "None") -> str:
    values = [item.strip() for item in items if item.strip()]
    if not values:
        values = [fallback]
    return "\n".join(f"- {item}" for item in values)


def record_event(
    conn: sqlite3.Connection,
    lane: str,
    object_type: str,
    object_id: str,
    actor: str | None,
    action: str,
    from_status: str | None,
    to_status: str | None,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO workflow_event(lane, object_type, object_id, actor, action, from_status, to_status, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            lane,
            object_type,
            object_id,
            actor,
            action,
            from_status,
            to_status,
            json_dumps(payload or {}),
            iso_now(),
        ),
    )


def extract_frontmatter(text: str) -> str:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return match.group(1) if match else ""


def frontmatter_value(frontmatter: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:[ \t]*([^\r\n]*)$", frontmatter, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value or None


def frontmatter_has_key(frontmatter: str, key: str) -> bool:
    return re.search(rf"^{re.escape(key)}:[ \t]*[^\r\n]*$", frontmatter, re.MULTILINE) is not None


def validate_validator_gate(frontmatter: str, ticket_id: str) -> bool | None:
    validator_required = frontmatter_value(frontmatter, "validator_required")
    validator_gate_reason = frontmatter_value(frontmatter, "validator_gate_reason")
    has_validator_required = frontmatter_has_key(frontmatter, "validator_required")
    has_validator_gate_reason = frontmatter_has_key(frontmatter, "validator_gate_reason")
    if not has_validator_required and not has_validator_gate_reason:
        return None
    if validator_required is None:
        raise ValueError(f"ticket {ticket_id} must explicitly set validator_required to true or false")
    normalized = validator_required.lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"ticket {ticket_id} validator_required must be true or false")
    if validator_gate_reason is None:
        raise ValueError(f"ticket {ticket_id} must include validator_gate_reason")
    if normalized == "true":
        missing = sorted(
            field for field in VALIDATOR_REQUIRED_METADATA_FIELDS
            if frontmatter_value(frontmatter, field) is None
        )
        if missing:
            raise ValueError(
                f"ticket {ticket_id} requires Validator evidence but is missing gate metadata: {', '.join(missing)}"
            )
        risk_level = str(frontmatter_value(frontmatter, "validator_risk_level")).lower()
        if risk_level not in VALID_VALIDATOR_RISK_LEVELS:
            raise ValueError(
                f"ticket {ticket_id} validator_risk_level must be one of {sorted(VALID_VALIDATOR_RISK_LEVELS)}"
            )
    return normalized == "true"


def load_ticket_validator_gate(
    project_root: pathlib.Path,
    row: dict[str, Any],
    ticket_id: str,
) -> dict[str, Any]:
    inbox_hint = row.get("inbox_path")
    if not isinstance(inbox_hint, str) or not inbox_hint.strip():
        raise RuntimeError(f"ticket {ticket_id} has no readable inbox_path for Validator gate enforcement")
    ticket_path = resolve_project_path(project_root, inbox_hint)
    if not ticket_path.is_file():
        raise RuntimeError(f"ticket {ticket_id} inbox file is not readable for Validator gate enforcement: {ticket_path}")
    frontmatter = extract_frontmatter(read_text(ticket_path))
    frontmatter_ticket_id = frontmatter_value(frontmatter, "ticket_id")
    if frontmatter_ticket_id is not None and frontmatter_ticket_id != ticket_id:
        raise RuntimeError(
            f"ticket {ticket_id} inbox file declares a different ticket_id: {frontmatter_ticket_id}"
        )
    required = validate_validator_gate(frontmatter, ticket_id)
    if required is None:
        state = "legacy_missing"
    elif required:
        state = "required"
    else:
        state = "not_required"
    return {
        "state": state,
        "validator_required": required,
        "inbox_path": str(ticket_path),
    }


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_non_empty_string(payload: dict[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{key} must be a non-empty string")
    return value.strip()


def validate_independent_validator_report(report: dict[str, Any], ticket_id: str) -> str:
    required_fields = {
        "validator_role",
        "contract_id",
        "contract_version",
        "overall_verdict",
        "confidence",
        "artifact_summary",
        "findings",
        "missing_evidence",
        "recommended_next_action",
        "validated_artifacts",
        "commands_run",
        "notes",
    }
    missing = sorted(required_fields - set(report))
    if missing:
        raise ValueError(f"Validator report for {ticket_id} missing required fields: {', '.join(missing)}")
    if report.get("validator_role") != "validator":
        raise ValueError(f"Validator report for {ticket_id} validator_role must be 'validator'")
    require_non_empty_string(report, "contract_id", "validator_report")
    require_non_empty_string(report, "contract_version", "validator_report")
    require_non_empty_string(report, "recommended_next_action", "validator_report")
    verdict = report.get("overall_verdict")
    if verdict not in VALID_VALIDATOR_VERDICTS:
        raise ValueError(
            f"Validator report for {ticket_id} overall_verdict must be one of {sorted(VALID_VALIDATOR_VERDICTS)}"
        )
    if report.get("confidence") not in VALID_VALIDATOR_CONFIDENCE:
        raise ValueError(
            f"Validator report for {ticket_id} confidence must be one of {sorted(VALID_VALIDATOR_CONFIDENCE)}"
        )
    if not isinstance(report.get("artifact_summary"), dict):
        raise ValueError(f"Validator report for {ticket_id} artifact_summary must be an object")
    for key in ("findings", "missing_evidence", "validated_artifacts", "commands_run"):
        if not isinstance(report.get(key), list):
            raise ValueError(f"Validator report for {ticket_id} {key} must be an array")
    if not all(isinstance(item, str) and item.strip() for item in report["validated_artifacts"]):
        raise ValueError(f"Validator report for {ticket_id} validated_artifacts must contain non-empty strings")
    if not all(isinstance(item, str) and item.strip() for item in report["commands_run"]):
        raise ValueError(f"Validator report for {ticket_id} commands_run must contain non-empty strings")
    notes = report.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ValueError(f"Validator report for {ticket_id} notes must be a string or null")

    findings = report["findings"]
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"Validator report for {ticket_id} findings[{index}] must be an object")
        for key in ("rule_id", "message"):
            require_non_empty_string(finding, key, f"validator_report.findings[{index}]")
        if finding.get("severity") not in VALID_VALIDATOR_FINDING_SEVERITIES:
            raise ValueError(
                f"Validator report for {ticket_id} findings[{index}].severity must be one of "
                f"{sorted(VALID_VALIDATOR_FINDING_SEVERITIES)}"
            )
        if finding.get("status") not in VALID_VALIDATOR_FINDING_STATUSES:
            raise ValueError(
                f"Validator report for {ticket_id} findings[{index}].status must be one of "
                f"{sorted(VALID_VALIDATOR_FINDING_STATUSES)}"
            )
        if "evidence" not in finding or (
            finding["evidence"] is not None and not isinstance(finding["evidence"], str)
        ):
            raise ValueError(
                f"Validator report for {ticket_id} findings[{index}].evidence must be a string or null"
            )

    has_error_fail = any(
        finding.get("severity") == "error" and finding.get("status") == "fail"
        for finding in findings
    )
    has_blocked = any(finding.get("status") == "blocked" for finding in findings)
    has_inconclusive = any(finding.get("status") == "inconclusive" for finding in findings)
    if verdict == "pass":
        if has_error_fail or has_blocked or has_inconclusive:
            raise ValueError(f"Validator report for {ticket_id} has an inconsistent pass verdict")
        if not report["validated_artifacts"]:
            raise ValueError(f"Validator report for {ticket_id} pass verdict requires validated_artifacts")
    elif verdict == "fail" and not has_error_fail:
        raise ValueError(f"Validator report for {ticket_id} fail verdict requires an error/fail finding")
    elif verdict == "blocked" and not has_blocked:
        raise ValueError(f"Validator report for {ticket_id} blocked verdict requires a blocked finding")
    elif verdict == "inconclusive" and not has_inconclusive:
        raise ValueError(f"Validator report for {ticket_id} inconclusive verdict requires an inconclusive finding")
    return str(verdict)


def validate_validator_report_record(
    project_root: pathlib.Path,
    ticket_id: str,
    record_path_value: str,
) -> dict[str, Any]:
    record_path = resolve_project_path(project_root, record_path_value)
    if not record_path.is_file():
        raise ValueError(f"Validator report reference record does not exist for {ticket_id}: {record_path}")
    record = read_json(record_path)
    required_fields = {
        "record_type",
        "ticket_id",
        "validator_role",
        "independence",
        "producer_identity",
        "report_path",
        "report_sha256",
        "created_at",
    }
    missing = sorted(required_fields - set(record))
    if missing:
        raise ValueError(f"Validator report reference record for {ticket_id} missing fields: {', '.join(missing)}")
    if record.get("record_type") != VALIDATOR_REPORT_RECORD_TYPE:
        raise ValueError(
            f"Validator report reference record for {ticket_id} record_type must be {VALIDATOR_REPORT_RECORD_TYPE!r}"
        )
    if record.get("ticket_id") != ticket_id:
        raise ValueError(f"Validator report reference record ticket_id must match {ticket_id}")
    if record.get("validator_role") != "validator":
        raise ValueError(f"Validator report reference record for {ticket_id} validator_role must be 'validator'")
    if record.get("independence") != "independent":
        raise ValueError(f"Validator report reference record for {ticket_id} independence must be 'independent'")
    producer_identity = require_non_empty_string(record, "producer_identity", "validator_report_record")
    report_path_value = require_non_empty_string(record, "report_path", "validator_report_record")
    expected_hash = require_non_empty_string(record, "report_sha256", "validator_report_record").lower()
    require_non_empty_string(record, "created_at", "validator_report_record")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError(f"Validator report reference record for {ticket_id} report_sha256 must be a SHA-256 hex digest")
    report_path = resolve_project_path(project_root, report_path_value)
    if not report_path.is_file():
        raise ValueError(f"Referenced Validator report does not exist for {ticket_id}: {report_path}")
    actual_hash = sha256_file(report_path)
    if actual_hash != expected_hash:
        raise ValueError(f"Referenced Validator report SHA-256 mismatch for {ticket_id}")
    report = read_json(report_path)
    verdict = validate_independent_validator_report(report, ticket_id)
    return {
        "record_path": str(record_path),
        "record_type": VALIDATOR_REPORT_RECORD_TYPE,
        "producer_identity": producer_identity,
        "report_path": str(report_path),
        "report_sha256": actual_hash,
        "overall_verdict": verdict,
    }


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


def validate_result_payload(outbox_payload: dict[str, Any], ticket_id: str) -> str:
    missing = sorted(REQUIRED_RESULT_FIELDS - set(outbox_payload))
    if missing:
        raise ValueError(f"result for {ticket_id} missing required fields: {', '.join(missing)}")
    if outbox_payload.get("ticket_id") != ticket_id:
        raise ValueError(f"result ticket_id mismatch: expected {ticket_id}, got {outbox_payload.get('ticket_id')}")
    runner_status = outbox_payload.get("runner_status")
    if runner_status not in VALID_RUNNER_RESULTS:
        raise ValueError(f"result runner_status must be one of {sorted(VALID_RUNNER_RESULTS)}")
    for key in ("files_changed", "validation", "notes"):
        if not isinstance(outbox_payload.get(key), list):
            raise ValueError(f"result field {key} must be an array")
    if "protocol_reads" in outbox_payload:
        protocol_reads = outbox_payload["protocol_reads"]
        if not isinstance(protocol_reads, list) or not protocol_reads or not all(isinstance(item, str) and item.strip() for item in protocol_reads):
            raise ValueError("result field protocol_reads must be an array of non-empty strings")
    if "evidence" in outbox_payload:
        evidence = outbox_payload["evidence"]
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item.strip() for item in evidence):
            raise ValueError("result field evidence must be an array of non-empty strings")
    if "confidence" in outbox_payload:
        confidence = outbox_payload["confidence"]
        if confidence not in {"high", "medium", "low"}:
            raise ValueError(f"result confidence must be one of {{'high', 'medium', 'low'}}, got {confidence!r}")
    if "runner_trace" in outbox_payload:
        validate_runner_trace(outbox_payload["runner_trace"], str(runner_status))
    if not isinstance(outbox_payload.get("summary"), str) or not outbox_payload["summary"].strip():
        raise ValueError("result summary must be a non-empty string")
    if not isinstance(outbox_payload.get("created_at"), str) or not outbox_payload["created_at"].strip():
        raise ValueError("result created_at must be a non-empty string")
    return str(runner_status)


def validate_optional_string_or_null(payload: dict[str, Any], key: str) -> None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"runner_trace.{key} must be a non-empty string or null")


def validate_runner_trace(trace: Any, runner_status: str) -> None:
    if not isinstance(trace, dict):
        raise ValueError("result field runner_trace must be an object")
    required = {"platform_name", "agent_profile", "attempts", "commands", "blocker_category", "next_action"}
    missing = sorted(required - set(trace))
    if missing:
        raise ValueError(f"runner_trace missing required fields: {', '.join(missing)}")
    validate_optional_string_or_null(trace, "platform_name")
    validate_optional_string_or_null(trace, "agent_profile")
    attempts = trace.get("attempts")
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError("runner_trace.attempts must be an integer >= 1")
    commands = trace.get("commands")
    if not isinstance(commands, list) or not all(isinstance(item, str) and item.strip() for item in commands):
        raise ValueError("runner_trace.commands must be an array of non-empty command strings")
    blocker_category = trace.get("blocker_category")
    if runner_status == "blocked":
        if blocker_category not in VALID_BLOCKER_CATEGORIES:
            raise ValueError(f"runner_trace.blocker_category must be one of {sorted(VALID_BLOCKER_CATEGORIES)} when runner_status is blocked")
    elif blocker_category is not None:
        raise ValueError("runner_trace.blocker_category must be null unless runner_status is blocked")
    next_action = trace.get("next_action")
    if runner_status in {"blocked", "partial"}:
        if not isinstance(next_action, str) or not next_action.strip():
            raise ValueError("runner_trace.next_action must be a non-empty string when runner_status is blocked or partial")
    elif next_action is not None and (not isinstance(next_action, str) or not next_action.strip()):
        raise ValueError("runner_trace.next_action must be a non-empty string or null")


def load_ticket_row(project_root: pathlib.Path, lane: str, ticket_path: pathlib.Path) -> dict[str, Any]:
    ticket_text = read_text(ticket_path)
    frontmatter = extract_frontmatter(ticket_text)
    ticket_id = frontmatter_value(frontmatter, "ticket_id") or ticket_path.stem
    validate_validator_gate(frontmatter, ticket_id)
    task_type = frontmatter_value(frontmatter, "task_type") or "change"
    task_mode = frontmatter_value(frontmatter, "task_mode") or "general"
    priority = (frontmatter_value(frontmatter, "priority") or "medium").lower()
    if priority not in VALID_PRIORITIES:
        priority = "medium"
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


def upsert_ticket(conn: sqlite3.Connection, lane: str, row: dict[str, Any], preserve_lifecycle: bool = True, action: str = "sync-mailbox") -> None:
    table = lane_table(lane)
    existing = conn.execute(
        f"SELECT status, next_actor, runner_result, review_result, claimed_by, claimed_at, created_at FROM {table} WHERE ticket_id = ?",
        (row["ticket_id"],),
    ).fetchone()
    from_status = existing[0] if existing else None
    if existing and preserve_lifecycle:
        row["status"] = existing[0]
        row["next_actor"] = existing[1]
        row["runner_result"] = existing[2]
        row["review_result"] = existing[3]
        row["claimed_by"] = existing[4]
        row["claimed_at"] = existing[5]
        row["created_at"] = existing[6]

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
    if not existing or from_status != row["status"] or not preserve_lifecycle:
        record_event(
            conn,
            lane=lane,
            object_type="ticket",
            object_id=row["ticket_id"],
            actor="helper",
            action=action,
            from_status=from_status,
            to_status=row["status"],
            payload={"preserve_lifecycle": preserve_lifecycle, "inbox_path": row.get("inbox_path")},
        )


def fetch_row(conn: sqlite3.Connection, lane: str, ticket_id: str) -> dict[str, Any] | None:
    table = lane_table(lane)
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"SELECT * FROM {table} WHERE ticket_id = ?", (ticket_id,)).fetchone()
    return dict(row) if row is not None else None


def actor_ready_status(actor: str) -> str:
    return RUNNER_READY_STATUS if actor == "runner" else ARCHITECT_READY_STATUS


def actor_ready_statuses(actor: str) -> list[str]:
    if actor == "runner":
        return [RUNNER_READY_STATUS]
    return [ARCHITECT_READY_STATUS, DRAFTED_STATUS]


def actor_running_status(actor: str) -> str:
    return RUNNER_RUNNING_STATUS if actor == "runner" else ARCHITECT_RUNNING_STATUS


def command_sync_mailbox(
    conn: sqlite3.Connection,
    project_root: pathlib.Path,
    lane: str,
    ticket_id: str | None,
    reset_lifecycle: bool = False,
) -> dict[str, Any]:
    paths = [inbox_dir(project_root, lane) / f"{ticket_id}.md"] if ticket_id else sorted(inbox_dir(project_root, lane).glob(f"{lane.upper()}-*.md"))
    synced: list[str] = []
    skipped: list[str] = []
    for ticket_path in paths:
        if not ticket_path.exists():
            skipped.append(ticket_path.name)
            continue
        row = load_ticket_row(project_root, lane, ticket_path)
        upsert_ticket(conn, lane, row, preserve_lifecycle=not reset_lifecycle, action="sync-mailbox")
        synced.append(row["ticket_id"])
    conn.commit()
    return {"status": "ok", "lane": lane, "synced_ticket_ids": synced, "skipped": skipped, "reset_lifecycle": reset_lifecycle}


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
        f"SELECT ticket_id, epic_id, task_mode, task_type, priority, status, next_actor, runner_result, review_result, inbox_path, outbox_path, claimed_by, claimed_at, updated_at "
        f"FROM {table} {where} ORDER BY {PRIORITY_RANK_SQL}, {STATUS_RANK_SQL}, id ASC",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def command_events(conn: sqlite3.Connection, lane: str, ticket_id: str, limit: int) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT lane, object_type, object_id, actor, action, from_status, to_status, payload_json, created_at "
        "FROM workflow_event WHERE lane = ? AND object_type = 'ticket' AND object_id = ? ORDER BY id DESC LIMIT ?",
        (lane, ticket_id, limit),
    ).fetchall()
    payloads: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(str(item.pop("payload_json") or "{}"))
        except json.JSONDecodeError:
            item["payload"] = {}
        payloads.append(item)
    return payloads


def command_next(conn: sqlite3.Connection, lane: str, actor: str) -> dict[str, Any] | None:
    table = lane_table(lane)
    conn.row_factory = sqlite3.Row
    statuses = actor_ready_statuses(actor)
    placeholders = ", ".join("?" for _ in statuses)
    row = conn.execute(
        f"SELECT * FROM {table} WHERE status IN ({placeholders}) AND next_actor = ? "
        f"ORDER BY {PRIORITY_RANK_SQL}, {STATUS_RANK_SQL}, id ASC LIMIT 1",
        (*statuses, actor),
    ).fetchone()
    return dict(row) if row is not None else None


def command_claim(conn: sqlite3.Connection, lane: str, ticket_id: str, actor: str, claimed_by: str | None, action: str = "claim") -> dict[str, Any]:
    table = lane_table(lane)
    expected_statuses = actor_ready_statuses(actor)
    new_status = actor_running_status(actor)
    now = iso_now()
    before = fetch_row(conn, lane, ticket_id)
    from_status = before.get("status") if before else None
    placeholders = ", ".join("?" for _ in expected_statuses)
    cursor = conn.execute(
        f"UPDATE {table} SET status = ?, claimed_by = ?, claimed_at = ?, updated_at = ? "
        f"WHERE ticket_id = ? AND status IN ({placeholders}) AND next_actor = ?",
        (new_status, claimed_by or actor, now, now, ticket_id, *expected_statuses, actor),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        recovery_hint = ""
        if before and actor == "runner" and before.get("status") == RUNNER_RUNNING_STATUS and before.get("next_actor") == "runner":
            recovery_hint = (
                "; ticket is already running. If the Runner was interrupted before writing its outbox result JSON, "
                "use recover-stale --ticket-id "
                f"{ticket_id} --actor runner --reason \"runner interrupted before outbox\""
            )
        raise RuntimeError(f"claim failed for {ticket_id}; expected status in {expected_statuses} next_actor={actor}{recovery_hint}")
    row = fetch_row(conn, lane, ticket_id)
    if row is None:
        raise RuntimeError(f"{table} row missing after claim for {ticket_id}")
    record_event(
        conn,
        lane=lane,
        object_type="ticket",
        object_id=ticket_id,
        actor=claimed_by or actor,
        action=action,
        from_status=from_status,
        to_status=new_status,
        payload={"expected_statuses": expected_statuses},
    )
    conn.commit()
    return row


def command_mark_runner_result(
    conn: sqlite3.Connection,
    project_root: pathlib.Path,
    lane: str,
    ticket_id: str,
    runner_result: str,
    outbox_path: str | None,
) -> dict[str, Any]:
    if runner_result not in VALID_RUNNER_RESULTS:
        raise ValueError(f"invalid runner_result: {runner_result}")
    table = lane_table(lane)
    before = fetch_row(conn, lane, ticket_id)
    if before is None:
        raise RuntimeError(f"{table} row not found for {ticket_id}")
    if before.get("status") != RUNNER_RUNNING_STATUS or before.get("next_actor") != "runner":
        raise RuntimeError(f"runner result requires status={RUNNER_RUNNING_STATUS} next_actor=runner for {ticket_id}")
    result_hint = outbox_path or before.get("outbox_path")
    if not result_hint:
        raise RuntimeError(f"runner result requires an outbox result path for {ticket_id}")
    result_path = resolve_project_path(project_root, str(result_hint))
    if not result_path.exists():
        raise RuntimeError(
            f"runner result file not found: {result_path}. "
            "If the Runner was interrupted before writing this outbox file, use recover-stale instead of mark-runner-result."
        )
    outbox_payload = read_json(result_path)
    result_from_file = validate_result_payload(outbox_payload, ticket_id)
    if result_from_file != runner_result:
        raise ValueError(f"runner_result mismatch: CLI={runner_result} outbox={result_from_file}")
    stored_outbox_path = str(result_path.relative_to(project_root)).replace("\\", "/") if result_path.is_relative_to(project_root) else str(result_path)
    now = iso_now()
    cursor = conn.execute(
        f"UPDATE {table} SET status = ?, next_actor = ?, runner_result = ?, review_result = NULL, outbox_path = ?, updated_at = ? "
        f"WHERE ticket_id = ? AND status = ? AND next_actor = ?",
        (ARCHITECT_READY_STATUS, "architect", runner_result, stored_outbox_path, now, ticket_id, RUNNER_RUNNING_STATUS, "runner"),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise RuntimeError(f"{table} row not found for {ticket_id}")
    row = fetch_row(conn, lane, ticket_id)
    if row is None:
        raise RuntimeError(f"{table} row missing after runner result update for {ticket_id}")
    record_event(
        conn,
        lane=lane,
        object_type="ticket",
        object_id=ticket_id,
        actor=str(before.get("claimed_by") or "runner"),
        action="mark-runner-result",
        from_status=str(before.get("status")),
        to_status=ARCHITECT_READY_STATUS,
        payload={"runner_result": runner_result, "outbox_path": stored_outbox_path},
    )
    conn.commit()
    return row


def command_recover_stale(
    conn: sqlite3.Connection,
    project_root: pathlib.Path,
    lane: str,
    ticket_id: str,
    actor: str,
    reason: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    if actor != "runner":
        raise ValueError("recover-stale currently supports actor=runner")
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("recover-stale requires a non-empty --reason")
    table = lane_table(lane)
    before = fetch_row(conn, lane, ticket_id)
    if before is None:
        raise RuntimeError(f"{table} row not found for {ticket_id}")
    if before.get("status") != RUNNER_RUNNING_STATUS or before.get("next_actor") != "runner":
        raise RuntimeError(f"recover-stale requires status={RUNNER_RUNNING_STATUS} next_actor=runner for {ticket_id}")
    result_hint = before.get("outbox_path")
    stored_outbox_path = str(result_hint) if result_hint else None
    if result_hint:
        result_path = resolve_project_path(project_root, str(result_hint))
        if result_path.exists():
            raise RuntimeError(
                f"runner result file exists for {ticket_id}: {result_path}. "
                "Use mark-runner-result instead of recover-stale."
            )
    else:
        result_path = None
    recovered = dict(before)
    recovered.update(
        {
            "status": RUNNER_READY_STATUS,
            "next_actor": "runner",
            "runner_result": None,
            "review_result": None,
            "claimed_by": None,
            "claimed_at": None,
        }
    )
    if dry_run:
        return {
            "status": "dry_run",
            "lane": lane,
            "ticket_id": ticket_id,
            "reason": normalized_reason,
            "from": before,
            "to": recovered,
            "outbox_path": stored_outbox_path,
            "would_record_event": {
                "action": "recover-stale-running",
                "from_status": RUNNER_RUNNING_STATUS,
                "to_status": RUNNER_READY_STATUS,
                "payload": {
                    "reason": normalized_reason,
                    "previous_claimed_by": before.get("claimed_by"),
                    "previous_claimed_at": before.get("claimed_at"),
                    "outbox_path": stored_outbox_path,
                },
            },
        }

    now = iso_now()
    cursor = conn.execute(
        f"UPDATE {table} SET status = ?, next_actor = ?, claimed_by = NULL, claimed_at = NULL, "
        "runner_result = NULL, review_result = NULL, updated_at = ? "
        "WHERE ticket_id = ? AND status = ? AND next_actor = ?",
        (RUNNER_READY_STATUS, "runner", now, ticket_id, RUNNER_RUNNING_STATUS, "runner"),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise RuntimeError(f"recover-stale failed for {ticket_id}; expected status=running next_actor=runner")
    row = fetch_row(conn, lane, ticket_id)
    if row is None:
        raise RuntimeError(f"{table} row missing after recover-stale for {ticket_id}")
    record_event(
        conn,
        lane=lane,
        object_type="ticket",
        object_id=ticket_id,
        actor=actor,
        action="recover-stale-running",
        from_status=str(before.get("status")),
        to_status=RUNNER_READY_STATUS,
        payload={
            "reason": normalized_reason,
            "previous_claimed_by": before.get("claimed_by"),
            "previous_claimed_at": before.get("claimed_at"),
            "outbox_path": stored_outbox_path,
        },
    )
    conn.commit()
    return row


def command_mark_review_result(
    conn: sqlite3.Connection,
    lane: str,
    ticket_id: str,
    review_result: str,
    supersedes_ticket_id: str | None,
    project_root: pathlib.Path | None = None,
    validator_report_record_path: str | None = None,
) -> dict[str, Any]:
    if review_result not in VALID_REVIEW_RESULTS:
        raise ValueError(f"invalid review_result: {review_result}")
    table = lane_table(lane)
    before = fetch_row(conn, lane, ticket_id)
    if before is None:
        raise RuntimeError(f"{table} row not found for {ticket_id}")
    if before.get("status") not in {ARCHITECT_READY_STATUS, ARCHITECT_RUNNING_STATUS} or before.get("next_actor") != "architect":
        raise RuntimeError(f"review result requires status in {[ARCHITECT_READY_STATUS, ARCHITECT_RUNNING_STATUS]} next_actor=architect for {ticket_id}")
    validator_gate: dict[str, Any] | None = None
    validator_report: dict[str, Any] | None = None
    if review_result in {"accept", "accept_with_changes"}:
        resolved_project_root = (project_root or pathlib.Path.cwd()).resolve()
        validator_gate = load_ticket_validator_gate(resolved_project_root, before, ticket_id)
        if validator_gate["validator_required"] is True:
            if not validator_report_record_path:
                raise RuntimeError(
                    f"ticket {ticket_id} requires independent Validator evidence before {review_result}; "
                    "provide --validator-report-record"
                )
            validator_report = validate_validator_report_record(
                resolved_project_root,
                ticket_id,
                validator_report_record_path,
            )
            if validator_report["overall_verdict"] != "pass":
                raise RuntimeError(
                    f"ticket {ticket_id} Validator verdict {validator_report['overall_verdict']!r} "
                    f"does not permit {review_result}"
                )
        new_status = FINAL_STATUS
        next_actor = "none"
    elif review_result == "reject":
        new_status = RUNNER_READY_STATUS
        next_actor = "runner"
    else:
        new_status = DRAFTED_STATUS
        next_actor = "architect"
    now = iso_now()
    cursor = conn.execute(
        f"UPDATE {table} SET status = ?, next_actor = ?, review_result = ?, supersedes_ticket_id = COALESCE(?, supersedes_ticket_id), "
        f"claimed_by = NULL, claimed_at = NULL, updated_at = ? WHERE ticket_id = ? AND status IN (?, ?) AND next_actor = ?",
        (new_status, next_actor, review_result, supersedes_ticket_id, now, ticket_id, ARCHITECT_READY_STATUS, ARCHITECT_RUNNING_STATUS, "architect"),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise RuntimeError(f"{table} row not found for {ticket_id}")
    row = fetch_row(conn, lane, ticket_id)
    if row is None:
        raise RuntimeError(f"{table} row missing after review update for {ticket_id}")
    review_actor = str(before.get("claimed_by") or "architect") if before.get("status") == ARCHITECT_RUNNING_STATUS else "architect"
    record_event(
        conn,
        lane=lane,
        object_type="ticket",
        object_id=ticket_id,
        actor=review_actor,
        action="mark-review-result",
        from_status=str(before.get("status")),
        to_status=new_status,
        payload={
            "review_result": review_result,
            "supersedes_ticket_id": supersedes_ticket_id,
            "validator_gate": validator_gate,
            "validator_report": validator_report,
        },
    )
    conn.commit()
    return row


def next_ticket_id(conn: sqlite3.Connection, project_root: pathlib.Path, lane: str) -> str:
    prefix = lane.upper()
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    max_value = 0
    table = lane_table(lane)
    rows = conn.execute(f"SELECT ticket_id FROM {table} WHERE ticket_id LIKE ?", (f"{prefix}-%",)).fetchall()
    for row in rows:
        match = pattern.match(str(row[0]))
        if match:
            max_value = max(max_value, int(match.group(1)))
    for path in inbox_dir(project_root, lane).glob(f"{prefix}-*.md"):
        match = pattern.match(path.stem)
        if match:
            max_value = max(max_value, int(match.group(1)))
    return f"{prefix}-{max_value + 1:03d}"


def render_ticket_doc(
    lane: str,
    ticket_id: str,
    epic_id: str,
    title: str,
    task: str,
    task_mode: str,
    task_type: str,
    priority: str,
    scope: list[str],
    acceptance_checks: list[str],
    constraints: list[str],
    notes: list[str],
    review_focus: list[str],
    validator_required: bool,
    validator_gate_reason: str,
    validator_risk_level: str | None,
    validator_contract_source: str | None,
    validator_expected_artifacts: list[str],
    validator_evidence_pack: list[str],
    validator_failure_behavior: str | None,
) -> str:
    outbox_result_path = f"docs/{lane}/outbox/{ticket_id}.result.json"
    validator_metadata = [
        f"validator_required: {str(validator_required).lower()}",
        f"validator_gate_reason: {validator_gate_reason.strip()}",
        f"validator_risk_level: {(validator_risk_level or '').strip()}",
        f"validator_contract_source: {(validator_contract_source or '').strip()}",
        f"validator_expected_artifacts: {'; '.join(item.strip() for item in validator_expected_artifacts if item.strip())}",
        f"validator_evidence_pack: {'; '.join(item.strip() for item in validator_evidence_pack if item.strip())}",
        f"validator_failure_behavior: {(validator_failure_behavior or '').strip()}",
    ]
    return "\n".join(
        [
            "---",
            f"ticket_id: {ticket_id}",
            f"epic_id: {epic_id}",
            f"task_mode: {task_mode}",
            f"task_type: {task_type}",
            f"priority: {priority}",
            f"outbox_result_path: {outbox_result_path}",
            "parent_ticket_id:",
            "supersedes_ticket_id:",
            *validator_metadata,
            "---",
            "",
            f"# {ticket_id} - {title}",
            "",
            "## Task",
            "",
            task.strip(),
            "",
            "## Scope",
            "",
            markdown_list(scope, "Bounded to the task above."),
            "",
            "## Constraints",
            "",
            markdown_list(constraints, "No additional constraints."),
            "",
            "## Acceptance Checks",
            "",
            markdown_list(acceptance_checks, "Architect review confirms the task is complete."),
            "",
            "## Notes",
            "",
            markdown_list(notes, "No notes."),
            "",
            "## Review Focus",
            "",
            markdown_list(review_focus, "Verify scope, validation, and changed files."),
            "",
        ]
    )


def command_draft(
    conn: sqlite3.Connection,
    project_root: pathlib.Path,
    lane: str,
    ticket_id: str | None,
    epic_id: str,
    title: str,
    task: str,
    task_mode: str,
    task_type: str,
    priority: str,
    scope: list[str],
    acceptance_checks: list[str],
    constraints: list[str],
    notes: list[str],
    review_focus: list[str],
    validator_required: bool,
    validator_gate_reason: str,
    validator_risk_level: str | None,
    validator_contract_source: str | None,
    validator_expected_artifacts: list[str],
    validator_evidence_pack: list[str],
    validator_failure_behavior: str | None,
    force: bool,
) -> dict[str, Any]:
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"priority must be one of {sorted(VALID_PRIORITIES)}")
    if not validator_gate_reason.strip():
        raise ValueError("validator_gate_reason must be non-empty")
    if validator_required:
        missing: list[str] = []
        if validator_risk_level is None:
            missing.append("validator_risk_level")
        if validator_contract_source is None or not validator_contract_source.strip():
            missing.append("validator_contract_source")
        if not any(item.strip() for item in validator_expected_artifacts):
            missing.append("validator_expected_artifacts")
        if not any(item.strip() for item in validator_evidence_pack):
            missing.append("validator_evidence_pack")
        if validator_failure_behavior is None or not validator_failure_behavior.strip():
            missing.append("validator_failure_behavior")
        if missing:
            raise ValueError(
                "validator_required=true requires gate metadata: " + ", ".join(missing)
            )
    resolved_ticket_id = ticket_id or next_ticket_id(conn, project_root, lane)
    ticket_path = inbox_dir(project_root, lane) / f"{resolved_ticket_id}.md"
    ticket_path.parent.mkdir(parents=True, exist_ok=True)
    outbox_dir(project_root, lane).mkdir(parents=True, exist_ok=True)
    if ticket_path.exists() and not force:
        raise RuntimeError(
            f"ticket file already exists: {ticket_path}. "
            "draft creates new or explicitly forced ticket files; it is not a lifecycle recovery command. "
            "Use show, sync-mailbox, or recover-stale for existing interrupted tickets."
        )
    ticket_text = render_ticket_doc(
        lane=lane,
        ticket_id=resolved_ticket_id,
        epic_id=epic_id,
        title=title,
        task=task,
        task_mode=task_mode,
        task_type=task_type,
        priority=priority,
        scope=scope,
        acceptance_checks=acceptance_checks,
        constraints=constraints,
        notes=notes,
        review_focus=review_focus,
        validator_required=validator_required,
        validator_gate_reason=validator_gate_reason,
        validator_risk_level=validator_risk_level,
        validator_contract_source=validator_contract_source,
        validator_expected_artifacts=validator_expected_artifacts,
        validator_evidence_pack=validator_evidence_pack,
        validator_failure_behavior=validator_failure_behavior,
    )
    ticket_path.write_text(ticket_text, encoding="utf-8")
    row = load_ticket_row(project_root, lane, ticket_path)
    upsert_ticket(conn, lane, row, preserve_lifecycle=False, action="draft-ticket")
    conn.commit()
    payload = fetch_row(conn, lane, resolved_ticket_id)
    if payload is None:
        raise RuntimeError(f"{lane_table(lane)} row missing after drafting {resolved_ticket_id}")
    payload["created_file"] = str(ticket_path.relative_to(project_root)).replace("\\", "/")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Railyard workflow ticket helper.",
        epilog=(
            "Helper commands:\n"
            "  python railyard/scripts/ticket.py --lane domain --project-root . sync-mailbox\n"
            "  python railyard/scripts/ticket.py --lane domain --project-root . sync-mailbox --ticket-id DOMAIN-001 --reset-lifecycle\n"
            "  python railyard/scripts/ticket.py --lane domain draft --epic-id DOMAIN-E001 --title \"Define scope\" --task \"Write docs/scope.md.\" --validator-not-required --validator-gate-reason \"Low-risk documentation-only ticket.\"\n"
            "  python railyard/scripts/ticket.py --lane domain next --actor runner\n"
            "  python railyard/scripts/ticket.py --lane system claim --ticket-id SYSTEM-DEMO-001 --actor runner --claimed-by runner-1\n"
            "  python railyard/scripts/ticket.py --lane system recover-stale --ticket-id SYSTEM-DEMO-001 --actor runner --reason \"runner interrupted before outbox\"\n\n"
            "Recovery notes:\n"
            "  sync-mailbox --reset-lifecycle resets lifecycle fields from the inbox and outbox files.\n"
            "  Prefer recover-stale for interrupted running tickets that have no runner result JSON."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--lane", choices=("domain", "system"), required=True)
    parser.add_argument("--db", default=".workflow/workflow.db")
    parser.add_argument("--project-root", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser(
        "sync-mailbox",
        help="Sync ticket markdown files into the lane ticket table. Use --reset-lifecycle to reset lifecycle fields from mailbox files.",
    )
    sync_parser.add_argument("--ticket-id", default="")
    sync_parser.add_argument("--reset-lifecycle", action="store_true", help="Allow sync to reset lifecycle fields from mailbox files.")

    draft_parser = subparsers.add_parser("draft", help="Create a ticket inbox file and ready DB row.")
    draft_parser.add_argument("--ticket-id", default="")
    draft_parser.add_argument("--epic-id", required=True)
    draft_parser.add_argument("--title", required=True)
    draft_parser.add_argument("--task", required=True)
    draft_parser.add_argument("--task-mode", default="general")
    draft_parser.add_argument("--task-type", default="change")
    draft_parser.add_argument("--priority", choices=tuple(sorted(VALID_PRIORITIES)), default="medium")
    draft_parser.add_argument("--scope", action="append", default=[])
    draft_parser.add_argument("--acceptance-check", action="append", default=[])
    draft_parser.add_argument("--constraint", action="append", default=[])
    draft_parser.add_argument("--note", action="append", default=[])
    draft_parser.add_argument("--review-focus", action="append", default=[])
    validator_group = draft_parser.add_mutually_exclusive_group(required=True)
    validator_group.add_argument("--validator-required", action="store_true", help="Require independent Validator evidence before Architect acceptance.")
    validator_group.add_argument("--validator-not-required", action="store_true", help="Record that independent Validator evidence is not required.")
    draft_parser.add_argument("--validator-gate-reason", required=True, help="Reason for the Validator gate decision.")
    draft_parser.add_argument("--validator-risk-level", choices=tuple(sorted(VALID_VALIDATOR_RISK_LEVELS)))
    draft_parser.add_argument("--validator-contract-source", default="")
    draft_parser.add_argument("--validator-expected-artifact", action="append", default=[])
    draft_parser.add_argument("--validator-evidence-item", action="append", default=[])
    draft_parser.add_argument("--validator-failure-behavior", default="")
    draft_parser.add_argument("--force", action="store_true")

    show_parser = subparsers.add_parser("show", help="Show one ticket row.")
    show_parser.add_argument("--ticket-id", required=True)

    list_parser = subparsers.add_parser("list", help="List ticket rows.")
    list_parser.add_argument("--status", default="")
    list_parser.add_argument("--next-actor", default="")

    events_parser = subparsers.add_parser("events", help="List workflow events for one ticket.")
    events_parser.add_argument("--ticket-id", required=True)
    events_parser.add_argument("--limit", type=int, default=20)

    next_parser = subparsers.add_parser("next", help="Return the next ready ticket for an actor.")
    next_parser.add_argument("--actor", choices=("runner", "architect"), required=True)

    claim_parser = subparsers.add_parser("claim", help="Claim a ticket for an actor.")
    claim_parser.add_argument("--ticket-id", required=True)
    claim_parser.add_argument("--actor", choices=("runner", "architect"), required=True)
    claim_parser.add_argument("--claimed-by", default="")

    start_review_parser = subparsers.add_parser("start-review", help="Claim an awaiting-review ticket for architect review.")
    start_review_parser.add_argument("--ticket-id", required=True)
    start_review_parser.add_argument("--claimed-by", default="")

    runner_result_parser = subparsers.add_parser("mark-runner-result", help="Record runner completion and hand off to architect review.")
    runner_result_parser.add_argument("--ticket-id", required=True)
    runner_result_parser.add_argument("--runner-result", choices=tuple(sorted(VALID_RUNNER_RESULTS)), required=True)
    runner_result_parser.add_argument("--outbox-path", default="")

    recover_parser = subparsers.add_parser(
        "recover-stale",
        help="Recover an interrupted running Runner ticket when no outbox result JSON exists.",
    )
    recover_parser.add_argument("--ticket-id", required=True)
    recover_parser.add_argument("--actor", choices=("runner",), required=True)
    recover_parser.add_argument("--reason", required=True)
    recover_parser.add_argument("--dry-run", action="store_true")

    review_parser = subparsers.add_parser("mark-review-result", help="Record architect review result.")
    review_parser.add_argument("--ticket-id", required=True)
    review_parser.add_argument("--review-result", choices=tuple(sorted(VALID_REVIEW_RESULTS)), required=True)
    review_parser.add_argument("--supersedes-ticket-id", default="")
    review_parser.add_argument(
        "--validator-report-record",
        default="",
        help="Path to a hash-bound independent Validator report reference record when the ticket requires the gate.",
    )
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
            payload = command_sync_mailbox(conn, project_root, args.lane, args.ticket_id or None, args.reset_lifecycle)
        elif args.command == "draft":
            payload = command_draft(
                conn=conn,
                project_root=project_root,
                lane=args.lane,
                ticket_id=args.ticket_id or None,
                epic_id=args.epic_id,
                title=args.title,
                task=args.task,
                task_mode=args.task_mode,
                task_type=args.task_type,
                priority=args.priority,
                scope=args.scope,
                acceptance_checks=args.acceptance_check,
                constraints=args.constraint,
                notes=args.note,
                review_focus=args.review_focus,
                validator_required=args.validator_required,
                validator_gate_reason=args.validator_gate_reason,
                validator_risk_level=args.validator_risk_level,
                validator_contract_source=args.validator_contract_source or None,
                validator_expected_artifacts=args.validator_expected_artifact,
                validator_evidence_pack=args.validator_evidence_item,
                validator_failure_behavior=args.validator_failure_behavior or None,
                force=args.force,
            )
        elif args.command == "show":
            payload = fetch_row(conn, args.lane, args.ticket_id)
            if payload is None:
                raise RuntimeError(f"{lane_table(args.lane)} row not found for {args.ticket_id}")
        elif args.command == "list":
            payload = command_list(conn, args.lane, args.status or None, args.next_actor or None)
        elif args.command == "events":
            payload = command_events(conn, args.lane, args.ticket_id, args.limit)
        elif args.command == "next":
            payload = command_next(conn, args.lane, args.actor)
        elif args.command == "claim":
            payload = command_claim(conn, args.lane, args.ticket_id, args.actor, args.claimed_by or None)
        elif args.command == "start-review":
            payload = command_claim(conn, args.lane, args.ticket_id, "architect", args.claimed_by or None, action="start-review")
        elif args.command == "mark-runner-result":
            payload = command_mark_runner_result(conn, project_root, args.lane, args.ticket_id, args.runner_result, args.outbox_path or None)
        elif args.command == "recover-stale":
            payload = command_recover_stale(conn, project_root, args.lane, args.ticket_id, args.actor, args.reason, args.dry_run)
        elif args.command == "mark-review-result":
            payload = command_mark_review_result(
                conn,
                args.lane,
                args.ticket_id,
                args.review_result,
                args.supersedes_ticket_id or None,
                project_root=project_root,
                validator_report_record_path=args.validator_report_record or None,
            )
        else:
            raise RuntimeError(f"Unsupported command: {args.command}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
