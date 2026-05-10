#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]

VALID_LANES = {"domain", "system"}
VALID_PRIORITIES = {"high", "medium", "low"}
VALID_EPIC_STATUSES = {"queued", "in_progress", "partial", "blocked", "done", "superseded"}
VALID_RUNNER_STATUSES = {"done", "partial", "blocked", "invalid"}

TICKET_REQUIRED_FIELDS = {"ticket_id", "epic_id", "task_mode", "task_type", "priority"}
TICKET_REQUIRED_SECTIONS = ("Task", "Scope", "Acceptance Checks")
EPIC_REQUIRED_FIELDS = {"epic_id", "lane", "status", "priority"}
EPIC_REQUIRED_SECTIONS = ("Summary", "Done Definition")
RESULT_REQUIRED_FIELDS = {
    "ticket_id",
    "runner_status",
    "summary",
    "files_changed",
    "validation",
    "notes",
    "protocol_reads",
    "created_at",
}
QUEUE_ITEM_REQUIRED_FIELDS = {"epic_id", "title", "status", "priority", "summary", "done_definition"}


class ValidationError(ValueError):
    pass


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc


def extract_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not match:
        raise ValidationError("missing YAML-style frontmatter block")
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValidationError(f"invalid frontmatter line: {line!r}")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def missing_fields(values: dict[str, Any], required: set[str]) -> list[str]:
    return sorted(key for key in required if key not in values or values[key] in ("", None))


def require_sections(text: str, section_names: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for section_name in section_names:
        if not re.search(rf"^##\s+{re.escape(section_name)}\s*$", text, re.MULTILINE):
            missing.append(section_name)
    return missing


def relative(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def validate_ticket(path: pathlib.Path) -> None:
    text = read_text(path)
    frontmatter = extract_frontmatter(text)
    missing = missing_fields(frontmatter, TICKET_REQUIRED_FIELDS)
    if missing:
        raise ValidationError(f"missing required frontmatter fields: {', '.join(missing)}")
    ticket_id = frontmatter["ticket_id"]
    if path.stem != ticket_id:
        raise ValidationError(f"filename stem must match ticket_id {ticket_id!r}")
    if not re.search(rf"^#\s+{re.escape(ticket_id)}\s+-\s+\S", text, re.MULTILINE):
        raise ValidationError(f"missing title heading '# {ticket_id} - <Title>'")
    section_missing = require_sections(text, TICKET_REQUIRED_SECTIONS)
    if section_missing:
        raise ValidationError(f"missing required sections: {', '.join(section_missing)}")
    priority = frontmatter["priority"].lower()
    if priority not in VALID_PRIORITIES:
        raise ValidationError(f"invalid priority {priority!r}")


def validate_epic(path: pathlib.Path) -> None:
    text = read_text(path)
    frontmatter = extract_frontmatter(text)
    missing = missing_fields(frontmatter, EPIC_REQUIRED_FIELDS)
    if missing:
        raise ValidationError(f"missing required frontmatter fields: {', '.join(missing)}")
    epic_id = frontmatter["epic_id"]
    if path.stem != epic_id:
        raise ValidationError(f"filename stem must match epic_id {epic_id!r}")
    if frontmatter["lane"] not in VALID_LANES:
        raise ValidationError(f"invalid lane {frontmatter['lane']!r}")
    status = frontmatter["status"].lower()
    if status not in VALID_EPIC_STATUSES:
        raise ValidationError(f"invalid status {status!r}")
    priority = frontmatter["priority"].lower()
    if priority not in VALID_PRIORITIES:
        raise ValidationError(f"invalid priority {priority!r}")
    if not re.search(rf"^#\s+{re.escape(epic_id)}\s+-\s+\S", text, re.MULTILINE):
        raise ValidationError(f"missing title heading '# {epic_id} - <Title>'")
    section_missing = require_sections(text, EPIC_REQUIRED_SECTIONS)
    if section_missing:
        raise ValidationError(f"missing required sections: {', '.join(section_missing)}")


def validate_result(path: pathlib.Path) -> None:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValidationError("result JSON must be an object")
    missing = missing_fields(payload, RESULT_REQUIRED_FIELDS)
    if missing:
        raise ValidationError(f"missing required fields: {', '.join(missing)}")
    runner_status = payload["runner_status"]
    if runner_status not in VALID_RUNNER_STATUSES:
        raise ValidationError(f"invalid runner_status {runner_status!r}")
    for key in ("files_changed", "validation", "notes"):
        if not isinstance(payload[key], list):
            raise ValidationError(f"{key} must be an array")
    protocol_reads = payload["protocol_reads"]
    if not isinstance(protocol_reads, list) or not protocol_reads:
        raise ValidationError("protocol_reads must be a non-empty array")
    if not all(isinstance(item, str) and item.strip() for item in protocol_reads):
        raise ValidationError("protocol_reads must be an array of non-empty strings")
    for key in ("ticket_id", "summary", "created_at"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ValidationError(f"{key} must be a non-empty string")
    expected_ticket_id = path.name.removesuffix(".result.json")
    if payload["ticket_id"] != expected_ticket_id:
        raise ValidationError(f"ticket_id must match result filename {expected_ticket_id!r}")


def require_string(payload: dict[str, Any], key: str) -> None:
    if not isinstance(payload.get(key), str) or not payload[key].strip():
        raise ValidationError(f"{key} must be a non-empty string")


def require_string_list(payload: dict[str, Any], key: str) -> None:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValidationError(f"{key} must be an array of non-empty strings")


def validate_queue(path: pathlib.Path) -> None:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValidationError("queue JSON must be an object")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError("items must be a non-empty array")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(f"items[{index}] must be an object")
        missing = missing_fields(item, QUEUE_ITEM_REQUIRED_FIELDS)
        if missing:
            raise ValidationError(f"items[{index}] missing required fields: {', '.join(missing)}")
        for key in ("epic_id", "title", "status", "priority", "summary"):
            require_string(item, key)
        if item["status"] not in VALID_EPIC_STATUSES:
            raise ValidationError(f"items[{index}].status is invalid: {item['status']!r}")
        if item["priority"] not in VALID_PRIORITIES:
            raise ValidationError(f"items[{index}].priority is invalid: {item['priority']!r}")
        for key in ("done_definition", "preferred_entrypoints", "notes"):
            if key in item:
                require_string_list(item, key)


def collect_artifacts(project_root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    artifacts: list[tuple[str, pathlib.Path]] = []
    for lane in sorted(VALID_LANES):
        inbox = project_root / "docs" / lane / "inbox"
        epic_dir = project_root / "docs" / lane / "epics"
        outbox = project_root / "docs" / lane / "outbox"
        if inbox.exists():
            artifacts.extend(("ticket", path) for path in sorted(inbox.glob("*.md")))
        if epic_dir.exists():
            artifacts.extend(("epic", path) for path in sorted(epic_dir.glob("*.md")))
        if outbox.exists():
            artifacts.extend(("result", path) for path in sorted(outbox.glob("*.result.json")))
    examples = project_root / "examples"
    if examples.exists():
        artifacts.extend(("queue", path) for path in sorted(examples.glob("**/queue.json")))
    return artifacts


def run_validation(project_root: pathlib.Path) -> dict[str, Any]:
    validators = {
        "ticket": validate_ticket,
        "epic": validate_epic,
        "result": validate_result,
        "queue": validate_queue,
    }
    checked: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    counts = {kind: 0 for kind in validators}

    for kind, path in collect_artifacts(project_root):
        counts[kind] += 1
        try:
            validators[kind](path)
        except Exception as exc:
            errors.append({"kind": kind, "path": relative(path, project_root), "error": str(exc)})
        else:
            checked.append({"kind": kind, "path": relative(path, project_root)})

    return {
        "status": "ok" if not errors else "failed",
        "project_root": str(project_root),
        "counts": counts,
        "checked": checked,
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Railyard workflow artifacts and example queues.")
    parser.add_argument("--project-root", default=str(ROOT), help="Repository or disposable project root to validate.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = pathlib.Path(args.project_root).resolve()
    payload = run_validation(project_root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
