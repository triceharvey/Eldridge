# ADR-0003: Task-Scoped Capability Authorization

- Status: Accepted
- Date: 2026-09-06

## Context

Static roles such as implementer or reviewer are too broad to constrain repository, paths, tools, network, secrets, time, or budget.

## Decision

Use role baselines narrowed by immutable task-scoped capability grants. Authorization requires authenticated principal, allowed role action, matching grant context, valid policy version, and current workflow state. Deny by default.

## Alternatives

- Plain RBAC is easier but cannot express least privilege for individual assignments.
- A general policy language/OPA could be powerful but adds another runtime and policy system before rules stabilize.

## Consequences

Grants are more verbose but auditable and testable. Policy evaluation starts in application code behind an interface so it can move to a dedicated engine later if complexity warrants.

## Failure behavior

Missing, expired, malformed, or mismatched grants deny the action and emit an audit event. Policy service/error paths fail closed for privileged actions.
