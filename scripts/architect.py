#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ticket import command_next, command_sync_mailbox
from workflow_schema import ensure_schema

ROLE_CAPABILITY_CONTRACTS: dict[str, dict[str, object]] = {
    "runner": {
        "required_capabilities": [
            "read",
            "write",
            "execute",
            "scoped_file_edit",
            "result_json",
        ],
        "reject_if_only": [
            "read_only",
            "planning_only",
            "review_only",
        ],
        "fallback_profile": "railyard-runner",
        "match_policy": "conservative_fuzzy",
    }
}


def render_runner_prompt(lane: str, ticket: dict[str, object], runner_name: str) -> str:
    ticket_id = str(ticket["ticket_id"])
    inbox_path = str(ticket["inbox_path"])
    outbox_path = str(ticket.get("outbox_path") or f"docs/{lane}/outbox/{ticket_id}.result.json")
    lane_label = lane.upper()
    return f"""You are a runner in the {lane_label} lane. You are not alone in the codebase. Do not revert edits made by others.

Your only task is ticket {ticket_id}.

First claim it:

python railyard/scripts/ticket.py --lane {lane} claim --ticket-id {ticket_id} --actor runner --claimed-by {runner_name}

Then read the ticket inbox file:

{inbox_path}

Stay inside the ticket scope. Do not widen the task, redefine acceptance checks, or perform work owned by another lane.

When done, write the required result JSON to:

{outbox_path}

The result JSON must include:
- ticket_id
- runner_status
- summary
- files_changed
- validation
- notes
- created_at

Then mark the runner result:

python railyard/scripts/ticket.py --lane {lane} mark-runner-result --ticket-id {ticket_id} --runner-result <done|partial|blocked|invalid>

Final response must list changed files and validation performed.
"""


def build_runner_spawn_payload(lane: str, ticket: dict[str, object], runner_name: str) -> dict[str, object]:
    role_contract = ROLE_CAPABILITY_CONTRACTS["runner"]
    return {
        "contract": "railyard.runner_dispatch.v2",
        "adapter": "platform-dispatch",
        "workflow_role": "runner",
        "role": "runner",
        "runner_name": runner_name,
        "required_capabilities": role_contract["required_capabilities"],
        "reject_if_only": role_contract["reject_if_only"],
        "capability_match_policy": role_contract["match_policy"],
        "fallback_profile": role_contract["fallback_profile"],
        "profile_priority": "fallback_after_platform_native",
        "agent_type": None,
        "platform_agent_type": None,
        "agent_type_policy": (
            "Select a documented or discovered platform-native execution agent first. "
            "If platform-native selection is missing, ambiguous, or unsafe, use the Railyard fallback profile "
            "when the platform supports custom or prompt-defined agents. Do not require or blindly pass a literal "
            "worker agent type."
        ),
        "fallback_agent_types": [
            "general-purpose",
            "generalist",
            "Agent",
            "Code",
            "default",
        ],
        "excluded_for_implementation": [
            "Explore",
            "explore",
            "Plan",
            "Ask",
            "codebase_investigator",
        ],
        "prompt_format": "plain_text",
        "prompt": render_runner_prompt(lane, ticket, runner_name),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Architect helper for dispatching runner tickets.",
        epilog=(
            "Helper commands:\n"
            "  python railyard/scripts/architect.py --lane domain dispatch-next-runner\n"
            "  python railyard/scripts/architect.py --lane system --runner-name system-runner-1 dispatch-next-runner"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--lane", choices=("domain", "system"), required=True)
    parser.add_argument("--db", default=".workflow/workflow.db")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runner-name", default="runner-1")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("dispatch-next-runner", help="Return the next runner ticket and a spawn-ready prompt.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = pathlib.Path(args.db).resolve()
    project_root = pathlib.Path(args.project_root).resolve()
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        if args.command == "dispatch-next-runner":
            sync_payload = command_sync_mailbox(conn, project_root, args.lane, ticket_id=None, reset_lifecycle=False)
            ticket = command_next(conn, args.lane, "runner")
            if ticket is None:
                payload = {"status": "idle", "lane": args.lane, "synced": sync_payload, "ticket": None}
            else:
                payload = {
                    "status": "ready",
                    "lane": args.lane,
                    "synced": sync_payload,
                    "ticket": ticket,
                    "spawn": build_runner_spawn_payload(args.lane, ticket, args.runner_name),
                }
        else:
            raise RuntimeError(f"Unsupported command: {args.command}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
