---
name: planner-validator-release-review
description: Example showing Planner-side Validator dispatch for release readiness evidence
type: example
---

# Planner Validator Release Review Example

This example shows how a Planner uses a Validator as release-readiness
evidence. The Planner receives an epic summary, ticket state table, and release
evidence pack, then decides whether to dispatch a Validator before making a
closure or release-readiness decision.

The Validator report is evidence only. It does not close the Epic, approve the
release, or write workflow state. The Planner retains the final decision.

## Files

| File | Purpose |
|---|---|
| `epic-summary.json` | Example epic scope and done definition |
| `ticket-state-table.json` | Example ticket state table |
| `release-evidence.json` | Example release-readiness evidence pack |
| `planner-validator-input.json` | Example Validator dispatch shape; not an orchestration input |

## Structural validation

Run from the repository root:

```powershell
python scripts\validate_artifacts.py --project-root .
```

The output should finish with `"errors": []`.

## Protocol-fidelity test

This example should be tested as a single Planner orchestration flow. The
Planner must decide whether Validator evidence is useful, dispatch the
Validator if the platform supports subagents, receive the Validator report,
and then make the closure or release-readiness decision. The user should not
manually run the Validator unless the current platform cannot dispatch a
subagent.

Start a fresh Planner session and give it this read-only prompt:

```text
You are acting as a Railyard Planner in a dev-time release-readiness simulation.

Do not modify files. Do not create tickets. Do not write workflow state.
Do not commit or push. Do not close any real Epic.

Assume scoped work for an example release candidate has been completed and
reviewed. Review this example directory:

examples/planner-validator-release-review/

Read:
- epic-summary.json
- ticket-state-table.json
- release-evidence.json

Tasks:
1. Decide whether Planner-side Validator evidence is useful for this
   release-readiness decision.
2. If Validator evidence is useful and the platform can dispatch subagents,
   dispatch a Validator automatically. Do not ask the user to run the
   Validator manually.
   - Validator is a workflow role, not necessarily a platform agent_type.
   - Prefer a dedicated validator agent type if available.
   - If no validator agent type exists, use a default/general-purpose
     subagent and make the Validator role explicit in the prompt.
3. The Validator may read only the artifacts included in your dispatch
   payload. It must not use this README as evidence and must not edit files or
   workflow state.
4. Require the Validator to return a structured Validation Report JSON with:
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
5. After the Validator returns a report, continue as Planner and decide
   whether to proceed, block, request more evidence, or require human review.
6. Do not write lifecycle state in this simulation.
7. Do not read planner-validator-input.json before dispatch. It is only a
   shape comparison artifact after your own dispatch payload is built.

If the platform cannot dispatch any subagent, stop after producing the exact
spawn-ready Validator prompt and payload. That prompt must include the same
structured Validation Report JSON output requirement.

Do not create temporary validation scripts, ad hoc validators, direct shell
validators, or replacement tooling inside the Planner session. Do not label
Planner-generated checks as a Validator report. Role collapse is allowed only
when explicitly authorized, and must be labeled as role-collapsed evidence.
```
