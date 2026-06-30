---
name: semantic-validation-contract
description: Generic semantic validation contract shape with fixed claim types, evidence states, and verdict branch definitions for v0.7.4
type: contract
version: 0.7.4
---

# Semantic Validation Contract

This document defines the generic semantic validation contract shape for Railyard
v0.7.4. It specifies the fixed claim types, evidence states, and verdict branch
definitions that govern semantic validation. Semantic validation evaluates
logical consistency, cross-artifact coherence, domain-level correctness, and
meaning-based properties beyond structural and deterministic field-level
constraints.

## 1. Purpose

Semantic validation operates at the artifact level rather than the field level.
It evaluates logical consistency, cross-artifact coherence, and meaning-based
properties that cannot be determined by structural or field-level deterministic
checks alone. Semantic findings are advisory or escalation signals; they must
not override deterministic primitive findings under the deterministic precedence
rule defined in `references/validation-contract.md`.

## 2. Fixed Claim Types

A semantic validation claim is a bounded assertion about one or more artifacts.
The contract defines four fixed claim types:

| Claim Type | Description | Example Claim |
|---|---|---|
| `coherence` | Cross-artifact logical consistency. Related artifacts must not contain logically conflicting statements about the same concept. | "Ticket T-001 acceptance criteria do not contradict Epic E-001 done definition." |
| `contradiction` | Direct contradictory assertions across artifacts. If one artifact states a requirement and another states the opposite. | "Artifact A states X; Artifact B states not-X; at least one is incorrect." |
| `completeness` | Concept coverage verification. Required concepts from a parent artifact must be addressed in child artifacts. | "Epic E-001 declares concept C as required; Ticket T-001 scope must address C." |
| `plausibility` | Value, relationship, or assertion plausibility. Values and relationships must fall within reasonable bounds derived from artifact context. | "Numeric value X is implausible given the range implied by the artifact's declared constraints." |

### 2.1 Claim Shape

Each claim is a JSON object with these fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `claim_id` | string | Yes | Stable identifier for this claim. |
| `claim_type` | string | Yes | One of `coherence`, `contradiction`, `completeness`, `plausibility`. |
| `description` | string | Yes | Human-readable description of the claim. |
| `primary_artifact` | string | Yes | Path or identifier of the primary artifact under evaluation. |
| `related_artifacts` | array of strings | No | Paths or identifiers of related artifacts for cross-artifact claims. |
| `assertion` | string | Yes | The specific assertion being claimed. |
| `expected_evidence` | array of strings | No | Description of evidence items expected to support the claim. |
| `plausibility_rules` | object | No | For `plausibility` claims: bounded rules derived from artifact context. |

## 3. Fixed Evidence States

Each claim is evaluated against available evidence. The contract defines four
fixed evidence states:

| Evidence State | Description | Finding Status |
|---|---|---|
| `enough_evidence` | Sufficient determinable evidence exists to evaluate the claim. The claim can be verified or refuted. | `pass` or `fail` depending on claim evaluation |
| `missing_evidence` | Evidence expected by the claim or contract is not available. The claim cannot be fully evaluated. | `inconclusive` or `blocked` |
| `conflicting_evidence` | Evidence from different sources supports conflicting conclusions about the claim. No single evidence source is clearly authoritative. | `inconclusive` with escalated severity |
| `unsupported_semantic_claim` | The claim type, scope, or assertion is not supported by available evidence and cannot be meaningfully evaluated. | `human_review_required` or `inconclusive` |

### 3.1 Evidence State Determination

The evidence state is determined before claim evaluation:

1. **Enumerate expected evidence** from the claim's `expected_evidence` field,
   the semantic primitive's `required_inputs`, and the evidence pack.
2. **Check evidence availability** against the provided artifacts and evidence
   pack.
3. **Resolve conflicts** when evidence from different sources disagrees.
4. **Assess support** for the claim type and scope given available evidence.

### 3.2 Evidence State Priority

When multiple evidence conditions exist, the worst state prevails:

```
unsupported_semantic_claim > conflicting_evidence > missing_evidence > enough_evidence
```

A claim that is unsupported takes priority over one that merely has missing
evidence.

## 4. Fixed Verdict Branches

The verdict for a semantic claim is determined by the claim type, evidence
state, and claim evaluation result:

| Claim Type | Evidence State | Claim Evaluation | Verdict |
|---|---|---|---|
| Any | `enough_evidence` | Claim verified | `pass` |
| Any | `enough_evidence` | Claim refuted | `fail` |
| Any | `missing_evidence` | N/A | `inconclusive` |
| Any | `conflicting_evidence` | N/A | `inconclusive` |
| `coherence` | `unsupported_semantic_claim` | N/A | `human_review_required` |
| `contradiction` | `unsupported_semantic_claim` | N/A | `human_review_required` |
| `completeness` | `unsupported_semantic_claim` | N/A | `inconclusive` |
| `plausibility` | `unsupported_semantic_claim` | N/A | `inconclusive` |

### 4.1 Verdict Branch Rules

- **`pass`**: Claim is verified with `enough_evidence`. The assertion holds
  under the available evidence.
- **`fail`**: Claim is refuted with `enough_evidence`. The assertion does not
  hold under the available evidence.
- **`inconclusive`**: Claim cannot be resolved. Evidence is missing, conflicting,
  or the claim is unsupported for `completeness` and `plausibility` types.
- **`human_review_required`**: `coherence` and `contradiction` claims with
  `unsupported_semantic_claim` require Human judgment because cross-artifact
  consistency decisions may have architectural or planning implications that
  cannot be resolved deterministically.

### 4.2 Aggregate Semantic Verdict

When multiple semantic claims are evaluated, the aggregate semantic verdict
follows the worst-verdict hierarchy:

```
fail > human_review_required > inconclusive > pass
```

This is consistent with the overall verdict computation defined in
`references/validator-protocol.md` Section 6.

## 5. Deterministic Precedence

Semantic validation findings are always subordinate to deterministic primitive
findings. The precedence hierarchy is:

1. **Deterministic primitive findings** -- highest authority.
2. **Semantic inference findings** -- advisory or escalation signals.
3. **Undefined or inconclusive** -- lowest authority.

When a deterministic finding contradicts a semantic finding, the deterministic
finding prevails. Semantic findings that conflict with deterministic results are
downgraded to `severity=warn` regardless of their original severity, or removed
from the aggregate verdict computation if the conflict is direct.

### 5.1 Conflict Detection

A semantic finding conflicts with a deterministic finding when both address the
same artifact, field, or assertion and produce incompatible conclusions. The
Validator records the conflict in the finding's `evidence` field and applies
the downgrade.

## 6. Escalation from Semantic Findings

Semantic findings that produce `human_review_required` or `inconclusive` with
`high` risk level trigger escalation under the confidence and escalation matrix
in `references/validator-protocol.md` Section 11.

## 7. Contract Shape

A semantic validation contract is a JSON object with these fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `contract_id` | string | Yes | Stable identifier for the contract. |
| `version` | string | Yes | Semver-like version string. |
| `applies_to` | array of strings | Yes | Artifact kinds the contract targets. |
| `claims` | array of objects | Yes | Array of claim objects following the claim shape in Section 2.1. |
| `coherence_scope` | object or null | No | Bounded scope for coherence claims (artifact pairs, concept domains). |
| `contradiction_domain` | object or null | No | Bounded domain for contradiction detection. |
| `completeness_scope` | object or null | No | Bounded scope for completeness verification (concept registry). |
| `plausibility_rules` | object or null | No | Bounded plausibility rules for value and relationship checks. |

## 8. Non-Goals

Semantic validation in v0.7.4 does not include:

- Runtime orchestration or automatic repair
- Model routing or open-ended LLM-based semantic review
- Business-specific rules or domain-specific field names
- Workflow state relocation
- New lifecycle transitions or role definitions
- Executable semantic Validator behavior
- Validator output JSON schema changes

## 9. See Also

- `references/validation-contract.md` -- Validation Contract foundation and
  semantic validation boundary definition
- `references/validation-primitive-registry.md` Section 11 -- Semantic inference
  primitive contract logic
- `references/validator-protocol.md` Section 11 -- Confidence and Human
  Escalation Matrix
- `references/validator-verdict-handoff-tree.md` -- Verdict handoff decision tree
