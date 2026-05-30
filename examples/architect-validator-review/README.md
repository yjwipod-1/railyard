---
name: architect-validator-review
description: Example showing how an Architect uses a Validator to review a Runner output
type: example
---

# Architect Validator Review Example

This example shows how an Architect dispatches a Validator to review a
Runner-produced derived artifact. The Validator returns a **read-only review
report** that serves as evidence. The Architect retains full acceptance or
rejection authority.

## What this example demonstrates

- An Architect receives a Runner output (a derived artifact) after a task
  completes.
- The Architect dispatches a Validator with three inputs:
  - the **source artifact** (original data)
  - the **derived artifact** (Runner output, a transformed copy)
  - the **field mapping contract** (rules for the transformation)
- The Validator inspects the artifacts against the contract and returns a
  structured Validation Report.
- The Architect reads the report alongside their own independent judgment
  and records an accept or reject decision.

## Key principle

The Validator report is **evidence only**. It does not accept or reject the
Runner output. The Architect makes the final decision.

## Files in this example

| File | Purpose |
|---|---|
| `source-artifact.json` | The original source dataset |
| `derived-artifact.json` | The Runner-produced derived (transformed) dataset |
| `field-mapping-contract.json` | Contract defining source-to-derived mapping rules |
| `validator-input.json` | Example Validator dispatch shape; not an orchestration input |

## How it fits the Architect workflow

1. **Receive** the Runner result artifact (derived-artifact.json).
2. **Dispatch** a Validator with the source artifact, derived artifact, and
   field mapping contract.
3. **Review** the generated Validation Report alongside independent judgment.
4. **Decide** accept or reject based on the report and own assessment.

## Structural validation

Run the structural validation from the repository root:

```powershell
python scripts\validate_artifacts.py --project-root .
```

The output should finish with `"errors": []`. That command confirms the
example JSON shapes are valid. It does not replace the Architect-to-Validator
workflow.

## Protocol-fidelity test

This example should be tested as a single Architect orchestration flow. The
Architect must decide whether Validator evidence is needed, dispatch the
Validator if the platform supports subagents, receive the Validator report,
and then make the review decision. The user should not manually run the
Validator unless the current platform cannot dispatch a subagent.

Start a fresh Architect session and give it this read-only prompt:

```text
You are acting as a Railyard Architect in a dev-time review simulation.

Do not modify files. Do not create tickets. Do not write workflow state.
Do not commit or push.

Assume a Runner has completed work and returned a derived artifact.
Review this example directory:

examples/architect-validator-review/

Read:
- source-artifact.json
- derived-artifact.json
- field-mapping-contract.json

Tasks:
1. Explain what a pre-v0.7 Architect might miss if it only checked that the
   Runner reported completion.
2. Decide whether v0.7 requires Validator evidence for this review.
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
