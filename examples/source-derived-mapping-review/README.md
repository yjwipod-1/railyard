---
name: source-derived-mapping-review
description: Example showing an Architect-to-Validator source-to-derived mapping review
type: example
---

# Source-Derived Mapping Review Example

This example models a development-time review where a Runner extracts tabular
source data into a derived JSON artifact. The Architect must decide whether to
dispatch a Validator and then use the Validator report as review evidence.

The example is generic. It does not depend on a real dataset, database,
workflow state, or private business domain.

## What this example demonstrates

- Candidate output must not be treated as the truth source.
- Field mapping contracts must identify the source location for each derived
  field.
- Numeric values must be checked against the declared transform rules.
- Similar-looking fields require an independent mapping contract.
- A Validator report gives Architect review evidence; the Architect still
  owns the reject or accept decision.

## Files

| File | Purpose |
|---|---|
| `source-table.json` | Generic source record with column-style values |
| `candidate-output.json` | Runner-produced derived output |
| `mapping-contract.json` | Field mapping contract declaring expected source columns |
| `validator-input.json` | Example Validator dispatch shape; not an orchestration input |

## Structural validation

Run from the repository root:

```powershell
python scripts\validate_artifacts.py --project-root .
```

The output should finish with `"errors": []`.

## Protocol-fidelity test

This example should be tested as a single Architect orchestration flow. The
Architect must decide whether Validator evidence is needed, dispatch the
Validator if the platform supports subagents, receive the Validator report,
and then make the review decision. The user should not manually run the
Validator unless the current platform cannot dispatch a subagent.

Start a fresh Architect session and give it this read-only prompt:

```text
You are acting as a Railyard Architect in a dev-time validation simulation.

Do not modify files. Do not create tickets. Do not write workflow state.
Do not commit or push.

Assume a Runner has completed an extraction task and produced a derived JSON
artifact. Review this example directory:

examples/source-derived-mapping-review/

Read:
- source-table.json
- candidate-output.json
- mapping-contract.json

Tasks:
1. Identify whether this is a source-to-derived validation case.
2. Decide whether Validator evidence is required before review.
3. If Validator evidence is required and the platform can dispatch subagents,
   dispatch a Validator automatically. Do not ask the user to run the
   Validator manually.
   - Validator is a workflow role, not necessarily a platform agent_type.
   - Prefer a dedicated validator agent type if available.
   - If no validator agent type exists, use a default/general-purpose
     subagent and make the Validator role explicit in the prompt.
4. The Validator may read only the artifacts included in your dispatch
   payload. It must not use this README as evidence and must not edit files or
   workflow state.
5. Require the Validator to return a structured Validation Report JSON with:
   validator_role, contract_id, contract_version, overall_verdict, confidence,
   artifact_summary, findings, missing_evidence, recommended_next_action,
   validated_artifacts, commands_run, and notes.
   - Return one JSON object only, not Markdown tables.
   - overall_verdict must be lowercase.
   - confidence must be high, medium, or low, not a number.
   - every finding must include rule_id, severity, status, message, evidence.
   - severity/status values must be lowercase and match references/validator-protocol.md Section 3.
   - missing_evidence, validated_artifacts, and commands_run must be arrays.
   - artifact_summary must be an object.
6. After the Validator returns a report, continue as Architect and make the
   review decision: accept, reject, blocked, inconclusive, or
   human_review_required.
7. If the Runner output is not acceptable, produce the remediation instruction
   that would be sent back to the Runner.
8. Do not write lifecycle state in this simulation.
9. Do not read validator-input.json before dispatch. It is only a shape
   comparison artifact after your own dispatch payload is built.

If the platform cannot dispatch any subagent, stop after producing the exact
spawn-ready Validator prompt and payload. That prompt must include the same
structured Validation Report JSON output requirement.

Do not create temporary validation scripts, ad hoc validators, direct shell
validators, or replacement tooling inside the Architect session. Do not label
Architect-generated checks as a Validator report. Role collapse is allowed only
when explicitly authorized, and must be labeled as role-collapsed evidence.
```
