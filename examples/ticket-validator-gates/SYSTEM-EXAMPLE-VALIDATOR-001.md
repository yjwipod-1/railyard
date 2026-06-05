---
ticket_id: SYSTEM-EXAMPLE-VALIDATOR-001
epic_id: SYSTEM-EXAMPLE
task_mode: general
task_type: change
priority: high
outbox_result_path: docs/system/outbox/SYSTEM-EXAMPLE-VALIDATOR-001.result.json
parent_ticket_id:
supersedes_ticket_id:
validator_required: true
validator_gate_reason: Produces a source-to-derived artifact with measurable mapping constraints.
validator_risk_level: high
validator_contract_source: Acceptance Checks and contracts/example-mapping.json
validator_expected_artifacts: data/source.json; data/derived.json; contracts/example-mapping.json
validator_evidence_pack: source schema; representative source values; mapping fixture; validation command outputs
validator_failure_behavior: Block Architect acceptance unless an independent Validator report permits acceptance.
---

# SYSTEM-EXAMPLE-VALIDATOR-001 - Required Validator gate example

## Task

Implement a generic source-to-derived transformation.

## Scope

- Produce the declared derived artifact from the source artifact.
- Preserve the field mappings declared by the contract.

## Acceptance Checks

- Every derived field maps to an independent source field or declared transformation.
- An independent Validator report permits Architect acceptance.
