# SQL Contract

This workflow uses four canonical lane tables:

- `domain_epic`
- `domain_ticket`
- `system_epic`
- `system_ticket`

It also uses workflow support tables:

- `workflow_event`
- `schema_version`

## Design Rules

- Use one SQLite database as workflow truth.
- Keep one epic table and one ticket table per lane.
- Prefer JSON columns for small structured fields such as blockers and notes.
- Access workflow data through helper scripts by default.

## Epic Columns

Expected columns:
- `epic_id`
- `title`
- `status`
- `priority`
- `source`
- `summary`
- `blocked_by_epic_ids_json`
- `blocked_by_external_json`
- `preferred_entrypoints_json`
- `done_definition_json`
- `notes_json`
- `linked_ticket_id`
- `completed_at`
- `created_at`
- `updated_at`

## Event Columns

Expected columns:
- `lane`
- `object_type`
- `object_id`
- `actor`
- `action`
- `from_status`
- `to_status`
- `payload_json`
- `created_at`

## Ticket Columns

Expected columns:
- `ticket_id`
- `epic_id`
- `task_mode`
- `task_type`
- `priority`
- `inbox_path`
- `outbox_path`
- `status`
- `next_actor`
- `runner_result`
- `review_result`
- `supersedes_ticket_id`
- `parent_ticket_id`
- `summary`
- `claimed_by`
- `claimed_at`
- `created_at`
- `updated_at`
