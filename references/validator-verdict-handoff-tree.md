---
name: validator-verdict-handoff-tree
description: Canonical handoff decision tree mapping Validator overall_verdict values to explicit Architect, Planner, Runner, Validator, and Human responsibilities
type: protocol
version: 0.7.3
---

# Validator Verdict Handoff Tree

This document defines the canonical handoff decision tree for Validator
`overall_verdict` values and finding status combinations. It states, for each
verdict, who acts next, whether acceptance is allowed, and whether remediation,
more evidence, redesign, blocked handling, or Human escalation is required.

This tree is the single authoritative reference for verdict-based handoff
decisions. All role-specific mappings in `lifecycle.md`, `roles.md`, and
`validator-protocol.md` derive from this tree.

## Handoff Dimensions

Each verdict handoff is described along these dimensions:

| Dimension | Description |
|---|---|
| **Consumer roles** | Which roles receive this verdict and must act |
| **Acceptance allowed** | Whether Architect `accept` / `accept_with_changes` is permitted |
| **Closure allowed** | Whether Planner epic/release closure is permitted |
| **Next action** | What the consumer must do next |
| **Remediation** | Whether the consumer should request Runner remediation |
| **More evidence** | Whether the consumer should collect or request additional evidence |
| **Redesign** | Whether the ticket should be redesigned |
| **Blocked handling** | Whether the ticket should be marked blocked |
| **Human escalation** | Whether Human decision is required before proceeding |

## Canonical Handoff Tree

```
Validator Report Produced (overall_verdict = <value>)
 |
 +-- pass              -> Architect: may accept after independent review
 |                     -> Planner: may close epic or proceed release
 |
 +-- fail              -> Architect: reject or redispatch Runner
 |                     -> Planner: open follow-up ticket or block closure
 |
 +-- blocked           -> Architect: collect missing evidence; do not accept
 |
 +-- inconclusive      -> Architect: do not accept (high-risk);
 |                        provide more evidence or escalate
 |
 +-- human_review_     -> Architect: STOP; request Human decision
    required              before recording any review result
```

## Verdict Handoff Table

### `pass`

| Dimension | Handoff |
|---|---|
| Consumer roles | Architect, Planner |
| Acceptance allowed | Yes -- Architect may accept after independent scope/diff review |
| Closure allowed | Yes -- Planner may close epic or proceed release after judgment |
| Next action | Architect: complete review and record result. Planner: assess closure readiness. |
| Remediation | Not required |
| More evidence | Not required, but Architect/Planner may request additional checks |
| Redesign | Not required |
| Blocked handling | Not applicable |
| Human escalation | Not required, unless risk level or project policy requires it |

**Validator-required gate:** For tickets that declare `validator_required: true`,
only `overall_verdict=pass` permits `accept` or `accept_with_changes`. All
other verdicts (`fail`, `blocked`, `inconclusive`, `human_review_required`)
reject acceptance and require consumer action as defined in this tree. This
gate is enforced by the lifecycle helper before recording acceptance.

**Warning policy note:** `warn` + `fail` findings are permitted under
`overall_verdict=pass` unless the contract sets `warnings_as_errors=true`.
When `warnings_as_errors=true`, `warn` + `fail` findings escalate the verdict
to `fail`, and the `fail` handoff applies.

**Edge case -- pass with non-blocking warnings:** If `overall_verdict=pass`
but `warn` + `fail` findings exist (without `warnings_as_errors`), the
Architect evaluates whether those warnings affect acceptance based on risk
level and ticket acceptance criteria. The handoff is still `pass`; the
warnings do not block acceptance but the Architect may reject for warning
reasons independently.

### `fail`

| Dimension | Handoff |
|---|---|
| Consumer roles | Architect (primary), Planner (if pre-closure) |
| Acceptance allowed | No -- `accept` and `accept_with_changes` are prohibited |
| Closure allowed | No -- Planner must not close epic until resolution |
| Next action | Architect: reject (`review_result=reject`) or redispatch Runner with focused remediation prompt |
| Remediation | Required -- Runner must fix the error-level finding and resubmit |
| More evidence | Not applicable -- evidence exists; the finding is definitive |
| Redesign | Possible -- if the error reveals a flawed approach, Architect may record `review_result=redesign` |
| Blocked handling | Not applicable -- the check ran and produced a definitive result |
| Human escalation | Optional -- Architect may escalate if remediation is infeasible or the error has broader implications |

**Error fail rules:**
- `severity=error` + `status=fail` always forces `overall_verdict=fail`.
- This is the only combination that unconditionally forces `fail`.
- `warnings_as_errors=true` additionally escalates `severity=warn` + `status=fail`
  to `overall_verdict=fail`.

**Remediation boundary:**
- The Architect requests remediation from the Runner; the Architect must not
  personally implement the fix unless explicitly authorized.
- The Validator does not remediate -- it only reports findings.
- After remediation, the Runner resubmits, and a new Validator cycle may be
  needed if the ticket is Validator-required.

### `blocked`

| Dimension | Handoff |
|---|---|
| Consumer roles | Architect (primary), Planner (if pre-closure) |
| Acceptance allowed | No -- blocked evidence prevents meaningful evaluation |
| Closure allowed | No -- Planner must not close epic while blocked conditions exist |
| Next action | Architect: collect or request missing evidence, permission, or artifact |
| Remediation | Not applicable -- the check could not run |
| More evidence | Required -- the blocking condition must be resolved first |
| Redesign | Possible -- if the artifact or environment cannot support the required check |
| Blocked handling | Yes -- the ticket or scope may need `runner_result=blocked` if the Runner cannot proceed |
| Human escalation | Required if -- the blocked condition requires Human-granted permission, missing environment, or external dependency that the Architect cannot resolve |

**Blocked evidence rules:**
- At least one finding with `status=blocked` exists.
- The `blocked` verdict takes precedence over `inconclusive` and `human_review_required`
  when both are present, because the check was prevented from running.
- If a blocked finding exists alongside error+fail findings, the overall verdict
  is `fail` (error+fail takes precedence over blocked).

### `inconclusive`

| Dimension | Handoff |
|---|---|
| Consumer roles | Architect (primary), Planner (if pre-closure) |
| Acceptance allowed | Not permitted for `validator_required: true` tickets (only `pass` permits gated acceptance). For optional/non-required Validator evidence only: not permitted for high-risk tickets; Architect may accept low-risk tickets after documented independent assessment. |
| Closure allowed | No for high-risk epics; Planner may conditionally proceed for low-risk if other evidence is sufficient |
| Next action | Architect: provide missing contract/evidence, or escalate for Human guidance |
| More evidence | Required -- the inconclusive finding indicates insufficient information |
| Remediation | Not applicable -- no definitive error was found |
| Redesign | Possible -- if the inconclusive finding reveals that the contract or approach is fundamentally underspecified |
| Blocked handling | Not applicable -- the check ran but could not reach a conclusion |
| Human escalation | Required for high-risk tickets where missing evidence cannot be supplied by the Architect |

**Inconclusive evidence rules:**
- At least one finding with `status=inconclusive` exists.
- No `severity=error` + `status=fail` findings exist (those would force `fail`).
- `inconclusive` is the lowest-precedence non-pass verdict:
  - `fail` always overrides `inconclusive`.
  - `blocked` overrides `inconclusive`.
  - `human_review_required` overrides `inconclusive`.
- For `validator_required: true` tickets, `inconclusive` blocks acceptance
  under all risk levels. The gate requires `pass`.
- For non-gated tickets or optional Validator evidence, the `risk_level`
  determines whether inconclusive findings block acceptance:
  - `high` -- do not accept; require resolution or Human escalation.
  - `medium` -- Architect discretion; may reject or request more evidence.
  - `low` -- Architect may accept with documented note.

### `human_review_required`

| Dimension | Handoff |
|---|---|
| Consumer roles | Architect (primary), Human (required escalation) |
| Acceptance allowed | No -- `accept` and `accept_with_changes` are prohibited |
| Closure allowed | No -- Planner must not close epic until Human resolves |
| Next action | Architect: STOP -- request Human decision before recording any review result |
| Remediation | Not applicable -- the need is for Human judgment, not automated fix |
| More evidence | Required -- Human provides the missing judgment or policy decision |
| Redesign | Possible -- Human may determine that the approach requires redesign |
| Blocked handling | Not applicable -- the check ran; the finding is about unresolved judgment |
| Human escalation | Required -- this verdict exists specifically to trigger Human intervention |

**Human escalation rules:**
- The Validator sets `overall_verdict=human_review_required` when findings
  require manual review before a verdict can be assigned.
- The Architect stops and records the escalation, including the exact findings,
  missing context, and required Human decision.
- The Architect does not substitute their own judgment, skip the gate, or
  convert the verdict to another value.
- There is no lifecycle transition for Human escalation. The ticket remains in
  `in_review`; review is stopped pending Human input. The lifecycle does not
  move until the Architect records a review result after Human resolution.

## Cross-Cutting Rules

### Warning Policy

| Condition | Effect on verdict | Handoff |
|---|---|---|
| `warn` + `fail`, no `warnings_as_errors` | No change to `overall_verdict` | Follow the verdict's handoff; Architect evaluates warning impact independently |
| `warn` + `fail`, `warnings_as_errors=true` | Escalates verdict to `fail` | Follow `fail` handoff |
| `warn` + `pass` | No change | Normal pass flow |
| `warn` + `blocked` | If no error+fail exists, verdict may be `blocked` | Follow `blocked` handoff |

### Error Fail Precedence

Any `severity=error` + `status=fail` finding forces `overall_verdict=fail`
regardless of other findings. The `fail` handoff supersedes all other handoffs.

### Verdict Computation Precedence

When multiple finding statuses exist, the overall verdict follows the
computation precedence defined in `references/validator-protocol.md` Section 6:

```
fail > blocked > human_review_required > inconclusive > pass
```

The handoff always follows the computed `overall_verdict` regardless of which
finding combinations produced it.

### Scope Exclusion

This handoff tree does not define:
- Runtime orchestration or automatic repair
- Semantic calibration fixtures
- New lifecycle statuses or review results
- Validator-side remediation
- Ticket retry policy or pipeline resumption

Those concerns are addressed by `references/lifecycle.md`, `references/roles.md`,
and project-specific protocol documents.

## More-Evidence Mapping

More-evidence is a **handoff action**, not a new ticket status or review
result. It is expressed using the existing lifecycle actions and review results
defined in `references/lifecycle.md`. No new statuses or review results are
introduced by this mapping.

### Mapping Table

| Evidence gap scenario | Description | Lifecycle action | Resulting state | Ownership |
|---|---|---|---|---|
| **Runner evidence gaps** | Runner could not produce sufficient validation evidence for the delivered work | `reject` | Ticket returns to `ready` for Runner | Architect marks `review_result=reject`; Runner resubmits with improved evidence |
| **Runner blocked on evidence** | Required evidence depends on an external artifact, permission, or environment that the Runner cannot access | `blocked` handling | Runner records `runner_result=blocked`; ticket stays for Architect assessment | Runner reports blocker; Architect resolves or escalates |
| **Missing contract details** | The ticket's Validation Contract or acceptance criteria are insufficient for the Validator or Runner to produce a definitive outcome | `redesign` | Ticket returns to `drafted` for Architect to refine the contract | Architect records `review_result=redesign` and provides a refined contract |
| **Missing external artifacts** | Evidence depends on an artifact owned by another team, external system, or unresolved dependency | `blocked` or follow-up ticket | Cross-ticket dependency; may spawn a separate ticket for artifact delivery | Planner opens follow-up ticket for artifact production; or Architect records blocked |
| **Unresolved Human business judgment** | A decision requires Human authority, policy interpretation, or business-domain knowledge that no automated role can provide | **Human escalation** | Architect stops review and awaits Human decision; no lifecycle transition | Architect escalates with exact findings and required decision; Human responds |

### Role-Specific Handoff Rules

#### Architect requesting more evidence

The Architect uses these existing lifecycle actions to request more evidence,
depending on the gap category:

| Gap | Lifecycle action | When to use |
|---|---|---|
| Runner evidence insufficient for current scope | `review_result=reject` -> ticket to `ready` | Runner delivered work but evidence is incomplete or unreliable |
| Contract or acceptance criteria underspecified | `review_result=redesign` -> ticket to `drafted` | The ticket itself needs more definition before Runner can produce valid evidence |
| External dependency unavailable | Record blocker; do not accept | The evidence cannot be produced until the dependency is resolved |
| Human judgment required | Stop review; escalate to Human | A business or policy decision is needed before any lifecycle action |

The Architect must not invent a new status, skip evidence collection, or
personally implement Runner evidence fixes. The Architect requests evidence
through the appropriate lifecycle action and allows the responsible role to
respond.

#### Planner requesting more evidence

The Planner uses these existing mechanisms to request more evidence before
epic closure or release readiness:

| Gap | Mechanism | When to use |
|---|---|---|
| Cross-ticket evidence insufficient | Open follow-up ticket | Multiple tickets need coordinated evidence collection |
| Pre-closure validation inconclusive | Request more evidence via Validator re-dispatch | The Planner-side Validator returned `inconclusive` or `blocked` |
| Missing external delivery | Block epic closure | An external dependency is unresolved |
| Human business judgment required | Escalate to Human | A policy or business decision blocks closure readiness |

The Planner does not define a new lifecycle state for more-evidence. The
Planner uses existing epic-level and ticket-level mechanisms.

### Principles

1. **No new statuses.** More-evidence is always expressed through existing
   lifecycle actions: `reject`, `redesign`, `blocked`, follow-up ticket, or
   Human escalation.
2. **Ownership preserved.** The role responsible for the gap receives the
   lifecycle signal and responds. The requesting role does not cross boundaries.
3. **Validator remains read-only.** The Validator reports evidence gaps via
   `inconclusive`, `blocked`, or `human_review_required` verdicts and
   `missing_evidence` fields. It does not perform remediation or lifecycle
   transitions.
4. **Human escalation is explicit.** When more evidence requires Human
   decision, the requesting role stops and escalates. No automated role
   substitutes Human judgment.

This mapping defines how more-evidence handoffs are represented in the
current lifecycle. A later ticket may explicitly change the lifecycle model,
but this ticket does not introduce new states or results.

## No-Remediation Boundary

The Validator is a **read-only evidence producer**. The handoff tree defined in
this document maps Validator verdicts to **consumer actions**, not Validator
actions. The Validator's role ends when the report is produced. All remediation,
fixes, follow-up direction, and lifecycle decisions belong to other roles.

### Core Boundary

| Action | Permitted for Validator | Owned by |
|---|---|---|
| Produce a Validation Report | Yes -- sole Validator output | Validator |
| Repair artifacts (edit files, fix data, correct output) | **No** | Runner (via reject/redispatch) |
| Mutate workflow state (change ticket/epic status, write outbox) | **No** | Architect/Planner (via lifecycle helpers) |
| Retry pipelines or re-execute commands | **No** | Architect (via redispatch) |
| Broaden scope (add checks, invent criteria, extend contract) | **No** | Architect (via redesign) |
| Decide lifecycle (accept, reject, close epic, mark result) | **No** | Architect/Planner (via review/closure) |
| Request more evidence from Runner | **No** | Architect (via reject) |
| Refine or redefine the contract | **No** | Architect (via redesign) |
| Open follow-up tickets | **No** | Planner (via draft) |
| Escalate to Human | **No** (reports the need; does not escalate) | Architect/Planner (via stop-and-escalate) |

### Non-Pass Verdict -> Remediation Owner Mapping

Each non-pass verdict triggers a bounded consumer action. The Validator does
not participate in remediation.

| Validator verdict | Validator action (ends here) | Remediation owner | Consumer action |
|---|---|---|---|
| `fail` | Report error findings with evidence | Runner (fix errors); Architect (reject/redispatch) | `review_result=reject` -> Runner fixes; or `review_result=redesign` -> Architect refines approach |
| `blocked` | Report blocked findings with missing evidence description | Architect (collect evidence); Human (grant permission) | Architect resolves blocking condition or escalates; Runner does not retry automatically |
| `inconclusive` | Report inconclusive findings with evidence gaps | Architect (provide contract/evidence); Planner (follow-up ticket) | Architect provides missing evidence or escalates for high-risk tickets |
| `human_review_required` | Report findings requiring Human judgment, then stop | Human (provide judgment) | Architect stops review and escalates; Human decides |

### Rules

1. **Validator output is final.** The Validator produces one report and stops.
   It does not retry, re-validate, or produce follow-up reports unless
   explicitly re-dispatched by an authorized consumer.
2. **Non-pass verdicts do not authorize automatic repair.** A `fail` verdict
   means someone must fix the errors; it does not mean the Validator should
   fix them. A `blocked` verdict means someone must unblock; the Validator
   does not retry the check.
3. **Remediation ownership is role-bound.** The role that receives the
   lifecycle signal owns the response. The Architect owns reject/redesign;
   the Runner owns fixes; the Planner owns follow-up tickets; the Human owns
   escalation decisions.
4. **Bounded consumer actions.** The consumer action for each non-pass verdict
   is bounded to the specific lifecycle transition. `reject` returns the ticket
   to `ready` for Runner fixes; it does not authorize scope expansion,
   contract redefinition, or automatic redispatch without Architect review.
5. **No Validator lifecycle authority.** The Validator does not make lifecycle
   decisions. It reports findings that inform Architect and Planner decisions.
   The `recommended_next_action` field is advisory only; it does not bind the
   consumer.

### Relationship to Existing Validator Boundaries

The Validator role boundary defined in `references/validator-protocol.md` Section 1
and `references/lifecycle.md` Validator Boundary section already establishes the
read-only constraint. This section makes explicit what each non-pass verdict
means for remediation ownership, bridging the gap between Validator evidence
and consumer action.

## Relationship to Other References

| Document | Relationship |
|---|---|
| `references/lifecycle.md` | Defines ticket lifecycle transitions and contains per-role verdict mappings that derive from this tree |
| `references/validator-protocol.md` | Defines verdict semantics, computation algorithm, and Planner-side verdict-to-action mapping that derives from this tree |
| `references/roles.md` | Defines role-specific handoff ownership that aligns with this tree |
| `references/startup-sequence.md` | References this tree in the context of Validator dispatch and review workflow |
| `references/validation-contract.md` | Defines the contract ownership and handoff sequence; this tree specifies what happens after a Validator report is produced |
