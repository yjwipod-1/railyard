#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib


def build_payload(project_root: pathlib.Path, lane: str, role: str, ticket_id: str | None, epic_id: str | None) -> dict[str, object]:
    if bool(ticket_id) == bool(epic_id):
        raise ValueError("Provide exactly one of --ticket-id or --epic-id.")
    object_type = "ticket" if ticket_id else "epic"
    object_id = ticket_id or epic_id
    helper = "ticket.py" if object_type == "ticket" else "epic.py"
    table = f"{lane}_{object_type}"
    body_path = None
    outbox_path = None
    if object_type == "ticket":
        body_path = str((project_root / "docs" / lane / "inbox" / f"{object_id}.md").resolve())
        outbox_path = str((project_root / "docs" / lane / "outbox" / f"{object_id}.result.json").resolve())
        next_command = f"python railyard/scripts/ticket.py --lane {lane} show --ticket-id {object_id}"
    else:
        body_path = str((project_root / "docs" / lane / "epics" / f"{object_id}.md").resolve())
        next_command = f"python railyard/scripts/epic.py --lane {lane} show --epic-id {object_id}"
    return {
        "lane": lane.upper(),
        "role": role,
        "object_type": object_type,
        "object_id": object_id,
        "table": table,
        "helper": helper,
        "next_command": next_command,
        "body_path": body_path,
        "outbox_path": outbox_path,
        "notes": [
            "Resolve lane and role before acting.",
            "Use the official helper script instead of direct SQL unless there is no helper."
        ]
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Route the next helper command for dual-lane workflow operations.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--lane", choices=("domain", "system"), required=True)
    parser.add_argument("--role", choices=("architect", "runner"), required=True)
    parser.add_argument("--ticket-id", default="")
    parser.add_argument("--epic-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(
        pathlib.Path(args.project_root).resolve(),
        args.lane,
        args.role,
        args.ticket_id or None,
        args.epic_id or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
