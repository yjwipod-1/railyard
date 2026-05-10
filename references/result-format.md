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
- `protocol_reads`
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
- `protocol_reads` is a non-empty array of the Railyard role and startup protocol files the Runner read before claiming or editing. It should normally include `railyard/SKILL.md`, `railyard/references/roles.md`, and `railyard/references/startup-sequence.md`, or the equivalent Railyard paths used by the project.
- Missing `protocol_reads` means the Runner did not leave evidence that it loaded the role contract. The helper rejects such result files before Architect review.
- Include short, review-friendly notes rather than long prose dumps.
- `mark-runner-result` validates this JSON before handing the ticket to architect review.
- The CLI `--runner-result` value must match `runner_status` in the result JSON.
