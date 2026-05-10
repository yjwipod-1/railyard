# Platform Dispatch

Railyard roles are workflow roles. They are not host-platform agent type names.

This reference defines how an Architect or dispatcher maps a Railyard workflow role to the current platform's available agent, subagent, mode, or background-agent surface.

## Authority

The dispatch contract does not change lifecycle authority:

- SQLite remains the canonical workflow state.
- Helper scripts remain the lifecycle implementation authority.
- MCP-lite remains an optional helper-backed tool adapter.
- Platform-specific agent files, memories, modes, and hidden directories do not override Railyard workflow rules.

## Core Rule

`runner`, `worker`, `architect`, and `reviewer` are Railyard workflow roles.

They must not be treated as portable platform `agent_type` values.

```text
workflow_role != platform_agent_type
```

The dispatcher may record the chosen platform type for audit or debugging, but the Runner must only be required to follow the workflow role, lane, ticket scope, helper authority, validation contract, and result payload contract.

## Platform Awareness

Do not require a Runner or subagent to identify its own host platform.

Platform identification and capability normalization are dispatcher responsibilities. A Runner receives explicit workflow instructions:

- lane
- ticket id
- workspace paths
- helper path authority
- files and references to inspect
- validation commands
- result JSON contract
- blocker reporting contract

If platform identity is unknown, the Runner should not guess it. If the dispatcher cannot identify a safe execution-capable dispatch path, dispatch must fail fast.

## Capability Profile

Adapters should normalize host-specific details into this shape before dispatch:

```json
{
  "platform": "claude-code",
  "profile_source": "documented-adapter",
  "can_dispatch_subagent": true,
  "available_agent_types": ["Explore", "Plan", "general-purpose"],
  "preferred_runner_agent_type": "general-purpose",
  "read_only_agent_type": "Explore",
  "planning_agent_type": "Plan",
  "review_agent_type": null,
  "supports_implicit_default": false,
  "confidence": "documented"
}
```

If discovery is unavailable or inconclusive, use a conservative profile:

```json
{
  "platform": "unknown",
  "profile_source": "conservative-fallback",
  "can_dispatch_subagent": false,
  "available_agent_types": null,
  "preferred_runner_agent_type": null,
  "read_only_agent_type": null,
  "planning_agent_type": null,
  "review_agent_type": null,
  "supports_implicit_default": false,
  "confidence": "unknown"
}
```

## Role Capability Contract

Dispatch maps workflow roles to capabilities, not to matching names.

| Workflow role | Required capabilities | Reject native agents that are only | Railyard fallback profile |
| --- | --- | --- | --- |
| `runner` | `read`, `write`, `execute`, `scoped_file_edit`, `result_json` | `read_only`, `planning_only`, `review_only` | `railyard-runner` |
| `architect` | `read`, `workflow_state`, `dispatch`, `review`, `lifecycle_write` | `runner_only`, `read_only` | `railyard-architect` |
| `explorer` | `read`, `search` | any write or lifecycle mutation surface | `railyard-explorer` |
| `reviewer` | `read`, `diff_inspect`, `validation_inspect`, `result_json` | implementation-only surfaces without review evidence access | `railyard-reviewer` |

Capability names are normalized adapter concepts. Platforms do not need to use these exact names natively.

## Conservative Fuzzy Matching

Adapters may use fuzzy capability matching, but must be conservative.

Acceptable synonym groups:

| Canonical capability | Acceptable platform terms |
| --- | --- |
| `read` | inspect, search, browse, codebase read |
| `write` | edit, modify, patch, file write |
| `execute` | run commands, terminal, shell, task execution |
| `workflow_state` | ticket state, queue state, lifecycle state |
| `dispatch` | spawn, delegate, background agent, subagent handoff |
| `review` | inspect result, review output, assess changes |
| `lifecycle_write` | claim, recover stale running ticket, mark result, start review, review result |
| `diff_inspect` | diff view, changed-file inspect, patch inspect |
| `validation_inspect` | test output inspect, command output inspect, check status |

Hard rejection categories:

- `read_only` cannot satisfy `runner`.
- `planning_only` cannot satisfy `runner`.
- `review_only` cannot satisfy `runner`.
- `runner_only` cannot satisfy `architect`.
- unknown or ambiguous write authority cannot satisfy lifecycle writes.

When a native platform agent partially matches but has unclear write or execution authority, the adapter must treat it as ambiguous and move to the Railyard fallback profile if the platform supports custom or prompt-defined agents.

The matcher should return an auditable decision shape:

```json
{
  "workflow_role": "runner",
  "required_capabilities": ["read", "write", "execute", "scoped_file_edit", "result_json"],
  "match_policy": "conservative_fuzzy",
  "selected": {
    "source": "platform-native",
    "platform_agent_type": "general-purpose",
    "confidence": "documented",
    "selection_reason": "agent satisfies read/write/execute for implementation tickets"
  }
}
```

If native matching fails but fallback profiles are supported:

```json
{
  "workflow_role": "runner",
  "match_policy": "conservative_fuzzy",
  "selected": {
    "source": "railyard-fallback-profile",
    "platform_agent_type": "railyard-runner",
    "confidence": "fallback",
    "selection_reason": "platform-native selection was missing or ambiguous"
  }
}
```

## Dispatch Selection Order

For execution tickets, select the first safe option:

1. A documented or discovered platform-native execution agent, such as `general-purpose`, `generalist`, `Agent`, `Code`, or the current platform's explicit implementation agent.
2. A Railyard fallback profile, such as `railyard-runner`, when platform-native selection is missing, ambiguous, or unsafe and the platform supports custom or prompt-defined agents.
3. A documented implicit default execution path, only if the platform explicitly marks it execution-capable.
4. Fail fast with `unsupported-dispatch`.

The dispatcher must not:

- invent an `agent_type`
- require a literal `worker` agent type
- use a read-only exploration agent for implementation
- use a planning-only agent for implementation
- retry indefinitely when the platform rejects an agent type
- bypass lifecycle helpers to compensate for dispatch failure
- treat a platform spawn authorization boundary as completed workflow

Railyard fallback profiles are not stronger than platform-native types. They exist to give unknown, custom, or plugin-based platforms a stable target when those platforms cannot clearly report their own execution-capable agent type.

If a platform requires explicit Human authorization before subagent spawn, the Architect must not invent implicit approval. It reports a spawn authorization blocker with the exact spawn-ready Runner prompt or dispatch command. If authorization is granted, spawning a Runner remains Architect dispatch work and does not violate the Architect/Runner implementation boundary.

## Official Platform Notes

These platform notes are based on official public documentation and should be treated as adapter guidance, not as a universal agent standard.

| Platform | Official surface | Execution-capable choice | Read-only / planning choice | Dispatch note |
| --- | --- | --- | --- | --- |
| OpenAI Codex | Codex CLI, IDE, and cloud coding agent; public docs do not define stable subagent type names | Use session-exposed tool metadata if available; otherwise no portable default | Ask/read-only modes may exist in specific surfaces | Do not assume `worker` exists across Codex surfaces |
| Claude Code | Built-in subagents plus project/user/custom subagents | `general-purpose` | `Explore` for read-only, `Plan` for planning | Do not use `Explore` or `Plan` for Runner implementation tickets |
| Gemini CLI | Built-in subagents | `generalist` | `codebase_investigator`, `cli_help`; `browser_agent` is experimental | `generalist` is the execution-capable generic fallback |
| GitHub Copilot CLI | Built-in agents and custom agents | `general-purpose`; `task` for command-heavy validation | `explore`, `code-review`, `research` | `research` is invoked through `/research`, not generic execution |
| VS Code Agents / Copilot | Built-in agents plus `.github/agents` custom agents | `Agent` or project custom `railyard-runner` | `Ask`, `Plan`, or read-only custom agents | Workspace agents live in `.github/agents`; Claude-format agents in `.claude/agents` are also detected |
| Windsurf Cascade | Modes | `Code` | `Ask` read-only, `Plan` planning | Code mode is Windsurf's default agentic implementation mode |
| Cursor | Agent and Background Agents | Cursor Agent or Background Agent | Ask mode where available | Public docs do not expose a stable named subagent type list |
| JetBrains AI Assistant / Junie | Junie, Codex, Claude Agent, ACP agents | Junie `Code`, Codex `Agent`, or an ACP execution agent | Junie `Ask`, Claude `Plan Mode` | Treat mode choice as adapter configuration and preserve human approval boundaries |

## Default Railyard Agent Profiles

Initialized projects include default VS Code / GitHub Copilot-compatible agent profiles under:

```text
.github/agents/
```

The default profile names are:

| Profile | Purpose |
| --- | --- |
| `railyard-architect` | lane scoping, dispatch, review, and epic closure |
| `railyard-runner` | execution-capable bounded ticket work |
| `railyard-explorer` | read-only codebase and workflow inspection |
| `railyard-reviewer` | review of Runner output, validation, and changed files |

These profiles are convenience adapters. They do not replace the workflow contract. If a platform cannot read `.github/agents`, it can still use the same role text as prompt material.

## Shared Workspace Rules

Multiple agent platforms may access the same project directory. Their local identity files must not override Railyard protocol.

Examples:

- Claude Code must not treat `.codex/` as workflow authority.
- Codex must not treat `.claude/agents/` as workflow authority.
- Cursor, Windsurf, JetBrains, and VS Code integrations must not treat another platform's hidden configuration as Railyard lifecycle authority.
- `.workflow/` remains workflow state and must be mutated only through stable helper-backed transitions.

Platform-specific files are adapter context, not protocol authority.

## Sources

- OpenAI Codex documentation: https://platform.openai.com/docs/codex
- OpenAI Codex CLI help: https://help.openai.com/en/articles/11096431-openai-codex-ligetting-started
- Claude Code subagents: https://code.claude.com/docs/en/sub-agents
- Gemini CLI subagents: https://github.com/google-gemini/gemini-cli/blob/main/docs/core/subagents.md
- GitHub Copilot custom agents: https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-custom-agents
- VS Code custom agents: https://code.visualstudio.com/docs/copilot/customization/custom-agents
- VS Code agents overview: https://code.visualstudio.com/docs/copilot/agents/overview
- Windsurf Cascade modes: https://docs.windsurf.com/windsurf/cascade/modes
- Cursor Background Agents: https://docs.cursor.com/en/background-agents
- JetBrains Junie: https://www.jetbrains.com/help/ai-assistant/junie-agent.html
