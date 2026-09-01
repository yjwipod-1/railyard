# Governance Document Inventory

Human-readable companion to the machine-readable inventory at `references/governance-document-inventory.json`. The JSON file is the sole machine-readable governance metadata authority. This markdown is for human reference only.

**Total documents**: 54. **Distribution**: Protocol 4, Policy 8, Contract 18, Registry 4, Schema 11, Guide 9. The runtime gate decision schema entry is v2.2.1 and remains compatible with the frozen v2.2.0 contract. The runtime action policy schema is v2.0.0 (active); the prior v1.0.0 schema is superseded.

## Document Inventory

| Path | Kind | Authority | Overrideability | Canonical For | Status | Mixed Sections |
|---|---|---|---|---|---|---|
| README.md | guide | informational | informational | none | active | none |
| README.zh-CN.md | guide | informational | informational | none | active | none |
| SKILL.md | policy | canonical | stricter_only | agent-workflow-rules, role-constraints | active | none |
| references/roles.md | policy | canonical | stricter_only | role-responsibilities | active | Default Closed-Loop Ownership (protocol -> lifecycle), Reject And Redispatch (protocol -> lifecycle) |
| references/runtime-adapter-contract.md | contract | canonical | non_overridable | runtime-adapter-contract | active | none |
| references/routing.md | policy | canonical | stricter_only | lane-routing | active | none |
| references/platform-dispatch.md | policy | canonical | stricter_only | platform-dispatch-rules | active | Restricted-Runner Mode (protocol -> startup-sequence) |
| references/lifecycle.md | protocol | canonical | non_overridable | ticket-lifecycle | active | Runner Result Boundaries (contract -> result-format), Failure Taxonomy (policy -> roles), More-Evidence Handoff (policy -> roles), Permission And Scratch-State Boundary (policy -> roles) |
| references/startup-sequence.md | protocol | canonical | non_overridable | startup-sequence | active | Architect Dispatch (policy -> platform-dispatch), Runner Protocol Requirements (policy -> roles) |
| references/validator-protocol.md | protocol | canonical | non_overridable | validator-protocol | active | Validator Input Slots (contract -> validation-contract), Remediation Ownership (policy -> roles) |
| references/validator-verdict-handoff-tree.md | protocol | canonical | non_overridable | validator-verdict-handoff | active | More-Evidence Mapping (policy -> roles) |
| references/epic-format.md | contract | canonical | non_overridable | epic-metadata-format | active | none |
| references/ticket-format.md | contract | canonical | non_overridable | ticket-metadata-format | active | none |
| references/result-format.md | contract | canonical | non_overridable | runner-result-format | active | none |
| references/governance-document-taxonomy.md | contract | canonical | non_overridable | governance-document-taxonomy, governance-metadata-convention, governance-precedence-model | active | none |
| references/sql-contract.md | contract | canonical | non_overridable | sql-schema-contract | active | Ticket Columns (schema -> governance-document-metadata-v1 schema) |
| references/validation-contract.md | contract | canonical | non_overridable | validation-contract | active | Ownership by Role (policy -> roles) |
| references/knowledge-contract.md | contract | canonical | non_overridable | knowledge-contract | active | none |
| references/semantic-validation-contract.md | contract | canonical | non_overridable | semantic-validation-contract | active | none |
| references/runtime-architecture.md | contract | canonical | non_overridable | runtime-architecture | active | none |
| references/runtime-state-contract.md | contract | canonical | non_overridable | runtime-state-contract | active | none |
| references/runtime-artifact-visibility-contract.md | contract | canonical | non_overridable | runtime-artifact-visibility | active | none |
| references/runtime-evidence-export-contract.md | contract | canonical | non_overridable | runtime-evidence-export | active | none |
| references/runtime-gate-decision-contract.md | contract | canonical | non_overridable | runtime-gate-decision-contract | active | none |
| references/runtime-action-policy-contract.md | contract | canonical | non_overridable | runtime-action-policy-contract | active | none |
| references/runtime-validator-mesh-contract.md (v1.2.0) | contract | canonical | non_overridable | runtime-validator-mesh-contract | active | none |
| references/runtime-v080-smoke-contract.md (v1.2.0) | contract | canonical | non_overridable | runtime-v080-smoke-contract | active | none |
| references/runtime-v080-staging-manifest-contract.md (v2.0.0; supersedes v1.0.0; v1.1.0 rejected) | contract | canonical | non_overridable | runtime-v080-staging-manifest-contract | active | none |
| references/validation-primitive-registry.md | registry | canonical | stricter_only | validation-primitive-registry | active | none |
| references/helper-commands.md | registry | canonical | stricter_only | helper-commands | active | Ticket Helpers (guide -> README) |
| references/governance-document-inventory.json | registry | canonical | stricter_only | governance-document-inventory | active | none |
| references/governance-read-routing.json | registry | canonical | stricter_only | governance-read-routing | active | none |
| references/model.md | guide | informational | informational | none | active | none |
| references/primitive-coverage-status.md | guide | informational | informational | none | active | none |
| references/governance-document-inventory.md | guide | informational | informational | none | active | none |
| assets/schemas/governance-document-metadata-v1.schema.json | schema | canonical | non_overridable | governance-document-metadata-schema | active | none |
| assets/schemas/runtime-gate-decision-v2.schema.json | schema | canonical | non_overridable | runtime-gate-decision-schema | active | none |
| assets/schemas/runtime-action-policy-v1.schema.json | schema | canonical | non_overridable | runtime-action-policy-schema | superseded | none |
| assets/schemas/runtime-action-policy-v2.schema.json | schema | canonical | non_overridable | runtime-action-policy-schema | active | none |
| assets/schemas/runtime-artifact-visibility-v1.schema.json | schema | canonical | non_overridable | runtime-artifact-visibility-schema | active | none |
| assets/schemas/runtime-evidence-export-v1.schema.json | schema | canonical | non_overridable | runtime-evidence-export-schema | active | none |
| assets/schemas/governance-document-inventory-v1.schema.json | schema | canonical | non_overridable | governance-document-inventory-structure | active | none |
| assets/schemas/governance-read-routing-v1.schema.json | schema | canonical | non_overridable | governance-read-routing-structure | active | none |
| assets/schemas/runtime-validator-mesh-v1.schema.json | schema | canonical | non_overridable | runtime-validator-mesh-schema | active | none |
| assets/schemas/runtime-v080-staging-manifest-v1.schema.json (v1.1.0; rejected historical candidate) | schema | canonical | non_overridable | runtime-v080-staging-manifest-schema-v1-historical | superseded | none |
| assets/schemas/runtime-v080-staging-manifest-v2.schema.json (v2.0.0) | schema | canonical | non_overridable | runtime-v080-staging-manifest-schema | active | none |
| assets/skeleton/.github/agents/railyard-architect.agent.md | policy | normative_reference | stricter_only | host-agent-architect-profile | active | none |
| assets/skeleton/.github/agents/railyard-runner.agent.md | policy | normative_reference | stricter_only | host-agent-runner-profile | active | none |
| assets/skeleton/.github/agents/railyard-explorer.agent.md | policy | normative_reference | stricter_only | host-agent-explorer-profile | active | none |
| assets/skeleton/.github/agents/railyard-reviewer.agent.md | policy | normative_reference | stricter_only | host-agent-reviewer-profile | active | none |
| assets/skeleton/docs/templates/EPIC.md | guide | informational | informational | none | active | none |
| assets/skeleton/docs/templates/TICKET.md | guide | informational | informational | none | active | none |
| assets/skeleton/docs/templates/RESULT.json | guide | informational | informational | none | active | none |
| assets/skeleton/docs/templates/VALIDATION-CONTRACT.json | guide | informational | informational | none | active | none |

## Exclusions

| Pattern | Reason |
|---|---|
| references/roadmap.md | Gitignored; future planning, not release governance |
| examples/ | Test fixtures and examples; not normative governance |
| scripts/ | Executable code; not declarative governance documents |
| dist/ | Build/distribution artifacts; derived, not source governance |
| agents/ | Secondary agent config; covered by skeleton profiles |
| .workflow/ | Local workflow state; not public release governance |
| CHANGELOG.md | Release history; informational, not normative governance |
| LICENSE | Legal document; outside governance taxonomy scope |
| .gitignore | Tool configuration; not governance document |
