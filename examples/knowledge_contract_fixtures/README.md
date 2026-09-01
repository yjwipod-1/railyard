# Knowledge Contract Calibration Fixtures

Generic, public-safe calibration fixtures for the Railyard v0.8.0 Knowledge Contract. All identifiers use the fictional `EXAMPLE-*` namespace. Each fixture is self-contained: all Knowledge target entries and runtime artifact inventories are declared within the fixture file.

## Fixture Catalog

| Fixture | Description | Expected Outcome |
|---|---|---|
| `fixture-valid-knowledge-entry.json` | A valid `technical_fact` entry with all targets resolved within the fixture corpus. Includes parent feature, constraint, capability, and domain entries plus artifact inventory. | Passes all structural, eligibility, relationship, and hierarchy checks. |
| `fixture-ineligible-entry.json` | An entry with provenance containing `contract_ref` but missing `epic_id`, `ticket_ids`, and `artifact_version` (fails Grounded). Evidence and relationships are valid to isolate one failure. | Fails with `eligibility-grounded` rule. |
| `fixture-missing-provenance.json` | An entry missing the `provenance` object entirely. Has a valid relationship target but no provenance. | Fails with `required-field-provenance` rule. |
| `fixture-supersession-chain.json` | A valid supersession chain: v1 frozen with `valid_until` and `superseded_by`; v2 with `supersedes` and new provenance. Both entries present with proper level hierarchy. | Passes all checks including supersession inverse consistency. |
| `fixture-broken-supersession.json` | An entry declaring `superseded_by` targeting a non-existent `entry_id`. Otherwise structurally valid with proper hierarchy. | Fails with `relationship-target-resolution` rule. |
| `fixture-multi-ticket-aggregation.json` | A feature entry aggregating three tickets, correctly chaining `part_of` through capability to domain. | Passes all checks including aggregation rules. |
| `fixture-ticket-multi-functionality.json` | One ticket contributing to two distinct feature entries with different scope notes. Both chain `part_of` through the same capability. | Passes all checks including multi-contribution rules. |

## Structure

Each fixture is a standalone JSON file with:
- `fixture_id`, `description`, `expected_outcome` at the top level
- `expected_failure_rule` for fail fixtures (declares the exact rule that should be observed)
- `entries` array containing all Knowledge entries (primary + context targets)
- `artifact_inventory` array declaring all runtime artifacts referenced in provenance and relationships

The validator derives observed rule IDs from fixture content. No bypass based on `expected_failure_rule` naming a semantic rule. For expected-fail fixtures, the observed rule set must contain exactly the declared `expected_failure_rule`.
