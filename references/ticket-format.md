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

New tickets also require:

- `validator_required`
- `validator_gate_reason`

## Optional Frontmatter Fields

- `outbox_result_path`
- `parent_ticket_id`
- `supersedes_ticket_id`
- `validator_risk_level`
- `validator_contract_source`
- `validator_expected_artifacts`
- `validator_evidence_pack`
- `validator_failure_behavior`

## Validator Gate Metadata

The Planner or Architect that drafts or publishes a ticket must explicitly set
`validator_required` to `true` or `false` and record the decision rationale in
`validator_gate_reason`.

Historical tickets with neither field remain valid for reading, mailbox sync,
dispatch, lifecycle use, and artifact-shape validation. Their missing metadata
is a legacy unknown state. It must not be inferred, rewritten, or recorded as
`validator_required: false`. Partial metadata, including only one field or
blank declared fields, is invalid.

Validator gate consideration is required for tickets involving:

- data transform, ingest, or migration
- source-to-derived artifacts
- generated artifacts with measurable constraints
- high-risk implementation
- derived authoritative data used by later workflow steps

When `validator_required: true`, these fields are required:

- `validator_risk_level`: `low`, `medium`, or `high`
- `validator_contract_source`: the explicit contract or ticket acceptance
  criteria that the Validator must apply
- `validator_expected_artifacts`: semicolon-separated source, candidate,
  contract, implementation, or other expected artifact references
- `validator_evidence_pack`: semicolon-separated evidence references such as
  schemas, source values, fixtures, or command outputs
- `validator_failure_behavior`: the required behavior when dispatch, evidence,
  or verdict cannot support acceptance

The metadata is a review gate declaration, not runtime orchestration. For a
required gate, Architect acceptance is prohibited until an independent
Validator role report is available and its verdict permits acceptance under
the declared failure behavior. If Validator dispatch is unavailable, the
Architect must stop and return the exact spawn-ready Validator prompt and
payload.

`scripts/validate_artifacts.py` checks ticket artifact shape, including the
presence and shape of this metadata. Its output is not independent Validator
role evidence. Runner verification commands and Architect self-review are also
not substitutes for a required independent Validator report.

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

## Validation Contract Placement

The executable Validation Contract lives at the ticket level. It may be:

- Embedded in the ticket body as concrete acceptance checks and field mapping rules.
- Referenced as a separate ticket-scoped artifact file via `validator_contract_source`.
- Constructed by the Architect from ticket acceptance criteria when no explicit contract artifact exists.

Contract ownership rules:

- **Planner** defines contract intent at the epic level (done definition, closure criteria, unacceptable failure modes).
- **Architect** produces the executable contract at the ticket level (rules, field mappings, schemas, Validator dispatch payload).
- **Runner** implements against the contract; does not redefine it.
- **Validator** applies the contract as given; does not modify it.

When `validator_required: true`, the ticket must reference or embed sufficient contract detail for the Architect to construct the Validator dispatch without inventing missing criteria during review. If the contract is missing or insufficient, the Architect stops and escalates rather than passing the check.

For the full ownership, placement, and handoff protocol, see `references/validation-contract.md`.
