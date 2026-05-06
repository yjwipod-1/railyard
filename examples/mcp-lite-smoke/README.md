# MCP-lite Smoke Example

This example shows the smallest useful MCP-lite workflow:

1. create a disposable Railyard project workspace
2. seed one System epic from `queue.json`
3. create one ready System ticket with the helper script
4. use MCP-lite tools to inspect, dispatch, claim, validate, and close the ticket

MCP-lite is intentionally not used to create or rewrite ticket Markdown. Ticket and epic creation stay in the script/file workflow. MCP-lite exposes a narrow control surface over the existing helper-backed lifecycle.

## Files

```text
examples/mcp-lite-smoke/
|-- README.md
|-- queue.json
`-- expected-output.md
```

## Setup

Run these commands from the Railyard repository root.

Install the optional MCP dependency:

```powershell
python -m pip install -r requirements-mcp.txt
```

Create a disposable project workspace:

```powershell
$project = "demo-workspace"
python scripts/init_workflow.py --project-root $project
```

Seed the example epic:

```powershell
python scripts/bootstrap_epics.py --lane system --db "$project/.workflow/workflow.db" --input examples/mcp-lite-smoke/queue.json
```

Create one ready ticket:

```powershell
python scripts/ticket.py --lane system --db "$project/.workflow/workflow.db" --project-root $project draft `
  --ticket-id SYSTEM-001 `
  --epic-id SYSTEM-E001 `
  --title "Run MCP-lite smoke workflow" `
  --task "Create docs/smoke-output.md with a short MCP-lite smoke summary." `
  --task-type validation `
  --priority high `
  --scope "Create docs/smoke-output.md." `
  --scope "Write the runner result JSON to docs/system/outbox/SYSTEM-001.result.json." `
  --acceptance-check "MCP-lite claim, result validation, runner result, review start, and review result transitions all complete." `
  --constraint "Do not use raw SQL." `
  --constraint "Do not write probe or scratch files inside .workflow/."
```

Start the MCP-lite server for the disposable project:

```powershell
python scripts/railyard_mcp_server.py --db "$project/.workflow/workflow.db" --project-root $project
```

Connect an MCP client to that stdio process. The calls below are representative tool calls and arguments.

## Architect: Inspect And Dispatch

Find the next System ticket for a Runner:

```json
{
  "tool": "next_ticket",
  "arguments": {
    "lane": "system",
    "actor": "runner"
  }
}
```

Build a spawn-ready Runner payload:

```json
{
  "tool": "dispatch_next_runner",
  "arguments": {
    "lane": "system",
    "runner_name": "smoke-runner-1"
  }
}
```

Apply `references/platform-dispatch.md`, select a safe execution-capable platform agent, then pass `spawn.prompt` to that Runner agent when the host platform supports subagents.

## Runner: Claim And Produce Result

Claim the ticket:

```json
{
  "tool": "claim_ticket",
  "arguments": {
    "lane": "system",
    "ticket_id": "SYSTEM-001",
    "actor": "runner",
    "claimed_by": "smoke-runner-1"
  }
}
```

Create the requested output file:

```powershell
New-Item -ItemType Directory -Force -Path "$project/docs" | Out-Null
Set-Content -Path "$project/docs/smoke-output.md" -Encoding utf8 -Value "# MCP-lite smoke output`n`nThe Runner completed the smoke task through MCP-lite lifecycle tools.`n"
```

Write the runner result file:

```powershell
New-Item -ItemType Directory -Force -Path "$project/docs/system/outbox" | Out-Null
@'
{
  "ticket_id": "SYSTEM-001",
  "runner_status": "done",
  "summary": "Created docs/smoke-output.md and completed the MCP-lite smoke task.",
  "files_changed": [
    "docs/smoke-output.md",
    "docs/system/outbox/SYSTEM-001.result.json"
  ],
  "validation": [
    "MCP-lite claim_ticket returned status=running next_actor=runner",
    "MCP-lite validate_result_payload returned valid=true"
  ],
  "notes": [
    "No raw SQL was used.",
    "No scratch files were written inside .workflow/."
  ],
  "created_at": "2026-01-01T00:00:00+00:00"
}
'@ | Set-Content -Path "$project/docs/system/outbox/SYSTEM-001.result.json" -Encoding utf8
```

Validate the result payload before marking completion:

```json
{
  "tool": "validate_result_payload",
  "arguments": {
    "lane": "system",
    "ticket_id": "SYSTEM-001",
    "outbox_path": "docs/system/outbox/SYSTEM-001.result.json",
    "expected_runner_result": "done"
  }
}
```

Mark the Runner result:

```json
{
  "tool": "mark_runner_result",
  "arguments": {
    "lane": "system",
    "ticket_id": "SYSTEM-001",
    "runner_result": "done",
    "outbox_path": "docs/system/outbox/SYSTEM-001.result.json"
  }
}
```

The ticket should now be `awaiting_review` for `architect`.

## Architect: Review And Accept

Start Architect review:

```json
{
  "tool": "start_review",
  "arguments": {
    "lane": "system",
    "ticket_id": "SYSTEM-001",
    "claimed_by": "smoke-architect-1"
  }
}
```

Record the review result:

```json
{
  "tool": "mark_review_result",
  "arguments": {
    "lane": "system",
    "ticket_id": "SYSTEM-001",
    "review_result": "accept"
  }
}
```

Confirm the final ticket state:

```json
{
  "tool": "validate_ticket_state",
  "arguments": {
    "lane": "system",
    "ticket_id": "SYSTEM-001",
    "expected_status": "finalised",
    "expected_actor": "none"
  }
}
```

See `expected-output.md` for representative response shapes.
