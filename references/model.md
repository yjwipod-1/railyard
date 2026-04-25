# Workflow Model

This skill models a reusable two-lane operating system for agentic work.

## Core Objects

- `Lane`
  - `Domain` for product delivery, analysis, content, and domain logic
  - `System` for tooling, platform, storage, and integrations
- `Epic`
  - unresolved work truth
  - cross-session planning and dependency surface
  - ids: `DOMAIN-E001`, `SYSTEM-E001`
- `Ticket`
  - bounded execution unit
  - ids: `DOMAIN-001`, `SYSTEM-001`
- `Role`
  - `architect` creates, routes, narrows, and reviews work
  - `runner` executes one bounded ticket at a time
- `Helper`
  - the canonical script or tool for reading and mutating control-plane state
- `Mailbox`
  - inbox and outbox files that hold task bodies and result bodies

## Truth Boundaries

- SQLite control tables are the canonical workflow truth.
- Epic tables store unresolved work, dependencies, and planning context.
- Ticket tables store execution lifecycle, ownership, and review state.
- Inbox and outbox files store the body of work, not the queue truth.

## Why This Split Exists

- Epics carry long-lived work and dependency context.
- Tickets keep execution narrow and reviewable.
- Lanes reduce cross-domain confusion.
- Helper scripts protect workflow rules that direct SQL would bypass.
