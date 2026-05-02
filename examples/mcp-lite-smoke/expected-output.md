# Expected Output

The exact timestamps and event ordering can vary. The important behavior is the lifecycle shape.

## Seeded Epic

After `bootstrap_epics.py`, the command returns:

```json
{
  "status": "ok",
  "lane": "system",
  "synced_epic_ids": [
    "SYSTEM-E001"
  ]
}
```

## Ready Ticket

After drafting the ticket, `next_ticket` for the Runner returns a ready ticket:

```json
{
  "lane": "system",
  "actor": "runner",
  "ticket": {
    "ticket_id": "SYSTEM-001",
    "epic_id": "SYSTEM-E001",
    "status": "ready",
    "next_actor": "runner",
    "runner_result": null,
    "review_result": null
  }
}
```

## Dispatch Payload

`dispatch_next_runner` returns a spawn-ready payload:

```json
{
  "status": "ready",
  "lane": "system",
  "ticket": {
    "ticket_id": "SYSTEM-001",
    "status": "ready",
    "next_actor": "runner"
  },
  "spawn": {
    "contract": "railyard.runner_dispatch.v1",
    "adapter": "generic",
    "agent_type": "worker",
    "role": "runner",
    "runner_name": "smoke-runner-1",
    "prompt_format": "plain_text",
    "prompt": "..."
  }
}
```

## Claim

`claim_ticket` moves the ticket into Runner execution:

```json
{
  "lane": "system",
  "ticket": {
    "ticket_id": "SYSTEM-001",
    "status": "running",
    "next_actor": "runner",
    "claimed_by": "smoke-runner-1"
  }
}
```

## Result Validation

`validate_result_payload` accepts the result JSON:

```json
{
  "valid": true,
  "lane": "system",
  "ticket_id": "SYSTEM-001",
  "runner_status": "done",
  "expected_runner_result": "done",
  "missing_fields": [],
  "errors": []
}
```

## Runner Result

`mark_runner_result` hands the ticket to Architect review:

```json
{
  "lane": "system",
  "ticket": {
    "ticket_id": "SYSTEM-001",
    "status": "awaiting_review",
    "next_actor": "architect",
    "runner_result": "done",
    "review_result": null
  }
}
```

## Review Start

`start_review` moves the ticket into Architect review:

```json
{
  "lane": "system",
  "ticket": {
    "ticket_id": "SYSTEM-001",
    "status": "in_review",
    "next_actor": "architect",
    "runner_result": "done"
  }
}
```

## Review Result

`mark_review_result` accepts and finalises the ticket:

```json
{
  "lane": "system",
  "ticket": {
    "ticket_id": "SYSTEM-001",
    "status": "finalised",
    "next_actor": "none",
    "runner_result": "done",
    "review_result": "accept"
  }
}
```

## Final State Validation

`validate_ticket_state` confirms the completed lifecycle:

```json
{
  "valid": true,
  "lane": "system",
  "ticket_id": "SYSTEM-001",
  "expected_status": "finalised",
  "expected_actor": "none",
  "actual_status": "finalised",
  "actual_actor": "none",
  "errors": []
}
```
