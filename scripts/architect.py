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
from governance_read_router import resolve_governance_reads, GovernanceRoutingConfigurationError

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


def render_runner_prompt(lane: str, ticket: dict[str, object], runner_name: str, startup_reads: list[str]) -> str:
    ticket_id = str(ticket["ticket_id"])
    inbox_path = str(ticket["inbox_path"])
    outbox_path = str(ticket.get("outbox_path") or f"docs/{lane}/outbox/{ticket_id}.result.json")
    lane_label = lane.upper()
    startup_reads_text = "\n".join(f"- {path}" for path in startup_reads)
    return f"""You are a runner in the {lane_label} lane. You are not alone in the codebase. Do not revert edits made by others.

Your only task is ticket {ticket_id}.

Before claiming or editing anything, read these Railyard protocol files:

{startup_reads_text}

If a listed protocol path is not present because the project keeps Railyard elsewhere, read the equivalent Railyard file and record the actual path in protocol_reads. If no equivalent file exists, stop and report a blocker instead of guessing the role contract.

Then claim the ticket:

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
- protocol_reads
- created_at

Then mark the runner result:

python railyard/scripts/ticket.py --lane {lane} mark-runner-result --ticket-id {ticket_id} --runner-result <done|partial|blocked|invalid>

Final response must list protocol reads, changed files, and validation performed.
"""


def _parse_ref_arg(value: str) -> tuple[str, str]:
    """Parse FORM=VALUE ref argument. Raises ValueError on malformed input."""
    if "=" not in value:
        raise ValueError(f"invalid ref format: {value!r} (expected FORM=VALUE)")
    form, _, val = value.partition("=")
    if not form.strip() or not val.strip():
        raise ValueError(f"invalid ref format: {value!r} (empty form or value)")
    return form.strip(), val.strip()


def build_runner_spawn_payload(lane: str, ticket: dict[str, object], runner_name: str,
                               governance_request: dict[str, object] | None = None) -> dict[str, object]:
    role_contract = ROLE_CAPABILITY_CONTRACTS["runner"]

    if governance_request is None:
        governance_request = {"role": "runner"}
    else:
        governance_request = dict(governance_request)  # don't mutate caller
        governance_request["role"] = "runner"  # force runner role

    # Invoke resolver exactly once
    governance_route_request = dict(governance_request)
    try:
        governance_route_result = resolve_governance_reads(governance_request)
    except GovernanceRoutingConfigurationError as exc:
        return {
            "status": "blocked",
            "reason": "governance_configuration_error",
            "detail": str(exc),
            "governance_route_request": governance_route_request,
        }

    # On blocked routing, return structured blocker (no prompt, no claim)
    if governance_route_result.get("status") == "blocked":
        return {
            "status": "blocked",
            "reason": governance_route_result.get("reason"),
            "ref": governance_route_result.get("ref"),
            "governance_route_request": governance_route_request,
            "governance_route_result": governance_route_result,
        }

    # Extract paths and prefix with railyard/
    normative_reads = governance_route_result.get("normative_reads", [])
    startup_reads = [f"railyard/{p}" for p in normative_reads]

    return {
        "contract": "railyard.runner_dispatch.v5",
        "adapter": "platform-dispatch",
        "workflow_role": "runner",
        "role": "runner",
        "runner_name": runner_name,
        "required_startup_reads": startup_reads,
        "required_capabilities": role_contract["required_capabilities"],
        "reject_if_only": role_contract["reject_if_only"],
        "capability_match_policy": role_contract["match_policy"],
        "fallback_profile": role_contract["fallback_profile"],
        "profile_priority": "fallback_after_platform_native",
        "profile_hints": {
            "execution_profile": "default",
            "preferred_execution_profile": "default",
            "allowed_execution_profiles": [],
            "advisory": True,
            "_contract_note": (
                "profile_hints are advisory routing hints for dispatch adapters. "
                "They do not trigger automatic model selection or automatic model routing. "
                "When no dispatch adapter consumes profile_hints, they fall back to normal Runner behavior. "
                "preferred_execution_profile and allowed_execution_profiles enter the dispatch contract "
                "as metadata only; the dispatcher may use them for capability matching but must not "
                "treat them as hard constraints on model choice."
            ),
        },
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
        "prompt": render_runner_prompt(lane, ticket, runner_name, startup_reads),
        "governance_route_request": governance_route_request,
        "governance_route_result": governance_route_result,
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
    # Governance routing typed flags — caller-declared, never inferred from ticket prose
    parser.add_argument("--validator-required", action="store_true", help="Validator required flag")
    parser.add_argument("--epic-closure", action="store_true", help="Epic closure flag")
    parser.add_argument("--validation-task", action="store_true", help="Validation task flag")
    parser.add_argument("--governance-task", action="store_true", help="Governance task flag")
    parser.add_argument("--knowledge-task", action="store_true", help="Knowledge task flag")
    parser.add_argument("--runtime-task", action="store_true", help="Runtime task flag")
    parser.add_argument("--validation-topic", default=None, help="Validation topic (e.g., semantic)")
    parser.add_argument("--contract-ref", action="append", default=[], help="Contract ref (FORM=VALUE)")
    parser.add_argument("--guide-ref", action="append", default=[], help="Guide ref (FORM=VALUE)")
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
                # Build governance request from CLI args
                governance_request: dict[str, object] = {"role": "runner"}
                if args.validator_required: governance_request["validator_required"] = True
                if args.epic_closure: governance_request["epic_closure"] = True
                if args.validation_task: governance_request["validation_task"] = True
                if args.governance_task: governance_request["governance_task"] = True
                if args.knowledge_task: governance_request["knowledge_task"] = True
                if args.runtime_task: governance_request["runtime_task"] = True
                if args.validation_topic: governance_request["validation_topic"] = args.validation_topic
                if args.contract_ref:
                    refs = []
                    for ref_str in args.contract_ref:
                        form, val = _parse_ref_arg(ref_str)
                        refs.append({"form": form, "value": val})
                    governance_request["explicit_contract_refs"] = refs
                if args.guide_ref:
                    refs = []
                    for ref_str in args.guide_ref:
                        form, val = _parse_ref_arg(ref_str)
                        refs.append({"form": form, "value": val})
                    governance_request["explicit_guide_refs"] = refs

                spawn = build_runner_spawn_payload(args.lane, ticket, args.runner_name, governance_request)

                if spawn.get("status") == "blocked":
                    payload = {
                        "status": "ready",
                        "lane": args.lane,
                        "synced": sync_payload,
                        "ticket": ticket,
                        "spawn": spawn,
                    }
                else:
                    payload = {
                        "status": "ready",
                        "lane": args.lane,
                        "synced": sync_payload,
                        "ticket": ticket,
                        "spawn": spawn,
                    }
        else:
            raise RuntimeError(f"Unsupported command: {args.command}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
