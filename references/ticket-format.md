# Ticket Format

Ticket files live under:
- `docs/domain/inbox/`
- `docs/system/inbox/`

Recommended filename:
- `<ticket_id>.md`

## Required Frontmatter Fields

- `ticket_id`
- `epic_id`
- `task_mode`
- `task_type`
- `priority`

## Optional Frontmatter Fields

- `outbox_result_path`
- `parent_ticket_id`
- `supersedes_ticket_id`

## Required Body Sections

- `# <TICKET-ID> - <Title>`
- `## Task`
- `## Scope`
- `## Acceptance Checks`

## Optional Body Sections

- `## Constraints`
- `## Notes`
- `## Review Focus`

Runner execution should stay inside the ticket body. Review should not rely on unstated expectations.
