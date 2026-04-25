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

## `runner_status`

Allowed values:
- `done`
- `partial`
- `blocked`
- `invalid`

## Notes

- Keep the result machine-readable.
- Use arrays for `files_changed` and `validation`.
- Include short, review-friendly notes rather than long prose dumps.
