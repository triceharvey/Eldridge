# ADR-0004: Worktree Plus Ephemeral-Container Isolation

- Status: Accepted
- Date: 2026-09-06

## Context

Concurrent changes require collision isolation, while model-proposed commands and repository dependencies require security containment. Git worktrees address only the first problem.

## Decision

Give each modifying task a dedicated branch/worktree and run commands in an ephemeral, non-root container with minimal mounts, no host runtime socket, resource limits, and network denied by default. Phase 1 uses a fake executor and runs no arbitrary commands.

## Alternatives

- Host subprocesses are simpler but expose host credentials and files.
- Worktrees alone prevent overlap but provide no command isolation.
- MicroVMs offer a stronger kernel boundary at higher operational cost.

## Consequences

The first real executor has understandable isolation and supports concurrent agents. Container hardening and cleanup become first-class work. Higher-risk multi-tenant use may require microVMs.

## Failure behavior

Timeout or cancellation terminates the sandbox, records the attempt, and quarantines incomplete artifacts when integrity is uncertain. Orphan reconciliation removes only explicitly identified task resources after confirming no live lease.
