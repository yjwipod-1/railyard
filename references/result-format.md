# Result Format

Runner result files live under:
- `docs/domain/outbox/`
- `docs/system/outbox/`

Recommended filename:
- `<ticket_id>.result.json`

## Required Fields

- `ticket_id`
- `runner_status`
- `summary`
- `files_changed`
- `validation`
- `notes`
- `created_at`

## Recommended Optional Fields

- `protocol_reads`
- `confidence`
- `evidence`
- `runner_trace`

## `runner_status`

Allowed values:
- `done`
- `partial`
- `blocked`
- `invalid`

Boundaries:

- `done`: the ticket scope is complete and validation evidence is available.
- `partial`: a useful, reviewable subset is complete inside the ticket scope, but the full ticket is not complete. Include remaining work and a concrete `next_action`.
- `blocked`: progress cannot continue without outside action. Use this for permission denial, network denial, missing secrets, missing required tools, unsupported dispatch, unresolved dependencies, and required Human authorization.
- `invalid`: the result cannot be trusted or does not satisfy the result contract.

Do not report `partial` when work is actually blocked by permission, network, missing secret, missing tool, unsupported dispatch, or unresolved dependency. Do not fake or bypass blockers with dummy credentials, skipped validation, unrelated tools, raw SQL, or alternate workflow state.

## `confidence`

Allowed values:
- `high`
- `medium`
- `low`

`confidence` is recommended for new results. Historical results without this field remain valid.

## `evidence`

An array of strings representing the evidence used to justify the confidence level (e.g., file paths, command outputs, or logs).

`evidence` is recommended for new results. Historical results without this field remain valid.

## `validation`

An array describing exact validation commands and pass/fail status. String entries are accepted for older results, but new results should use objects:

```json
{
  "command": "python scripts/validate_artifacts.py --project-root .",
  "status": "pass",
  "summary": "artifact validation returned status ok"
}
```

Use `status` values such as `pass`, `fail`, or `not_run`. For `fail` or `not_run`, include the exact error or blocker reason.

## `runner_trace`

`runner_trace` is a lightweight audit record that helps Architects and Planners compare Runner outputs across platforms. It is not telemetry, an observability pipeline, automatic model routing, token accounting, or failure redispatch discipline.

When present, `runner_trace` must be an object with these fields:

- `platform_name`: string platform name when the Runner knows it, otherwise `null`.
- `agent_profile`: string profile or agent configuration name used when known, otherwise `null`.
- `attempts`: integer count of attempts for this ticket result, starting at `1`.
- `commands`: ordered array of exact command strings the Runner executed for the ticket. Use an empty array only when no commands were run.
- `blocker_category`: one failure taxonomy value when `runner_status` is `blocked`, otherwise `null`.
- `next_action`: non-empty string when `runner_status` is `blocked` or `partial`, otherwise a string or `null`.

Allowed `blocker_category` values:

- `permission_denied`
- `command_failed`
- `sandbox_boundary`
- `authorization_required`
- `environment_issue`
- `unresolved_dependency`

## Human-Required Blockers

When `runner_status` is `blocked` because Human action is required, include a standard blocker detail in `notes` or another structured result field used by the project:

```json
{
  "category": "authorization_required",
  "ticket_id": "SYSTEM-001",
  "lane": "system",
  "intended_operation": "run validation requiring network access",
  "commands_attempted": ["python scripts/probe_railyard_mcp_server.py --db .workflow/workflow.db --project-root ."],
  "exact_errors": ["network access denied by sandbox"],
  "current_ticket_state": "running",
  "outbox_exists": false,
  "required_human_action": "Approve network access or provide an offline fixture.",
  "recommended_next_action": "Redispatch Runner after the Human action is complete."
}
```

Stop after reporting this blocker. Do not try alternate lifecycle helpers, raw SQLite updates, unapproved credentials, unapproved network access, or broader filesystem access.

## Notes

- Keep the result machine-readable.
- Use arrays for `files_changed` and `validation`.
- `runner_trace` should be short and factual. Do not include token counts, cost statistics, full logs, model-routing decisions, or automatic retry policy.
- Helper validation rejects malformed `runner_trace` fields when `runner_trace` is present. Missing `runner_trace` is allowed for backward compatibility with historical results.
- `protocol_reads` is a recommended non-empty array of the Railyard role and startup protocol files the Runner read before claiming or editing. It should normally include `railyard/SKILL.md`, `railyard/references/roles.md`, and `railyard/references/startup-sequence.md`, or the equivalent Railyard paths used by the project.
- Helper validation rejects malformed `protocol_reads` when present. Missing `protocol_reads` is allowed for backward compatibility with historical results.
- In restricted-runner mode, return exact JSON-compatible result content for the Architect to write; do not write Control lifecycle state or Control outbox directly.
- Include short, review-friendly notes rather than long prose dumps.
- `mark-runner-result` validates this JSON before handing the ticket to architect review.
- The CLI `--runner-result` value must match `runner_status` in the result JSON.
