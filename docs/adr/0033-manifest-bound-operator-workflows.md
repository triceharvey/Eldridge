# ADR-0033: Use manifest-bound operator workflows

## Status

Accepted on 2026-09-12.

## Context

Phase 5.4 proved the model pipeline through evidence-specific test harnesses. Reusing Eldridge across
projects requires an operator interface that preserves the same repository, path, provider, egress,
retry, and human-approval controls without copying Python code for every task.

## Decision

Eldridge accepts a strict JSON operator manifest that binds an immutable repository revision,
narrower writable paths, every model stage to an enabled provider identity, workflow risk metadata,
and a maximum task-lease count. A read-only preflight validates the manifest against the independent
repository registry and provider policy. Execution requires an explicit confirmation, plus a second
confirmation when any assigned provider crosses the approved external-egress boundary.

The command stops at the human approval gate. It does not approve, push, create a PR, merge, or
deploy.

The manifest's commit must equal the registry's independently approved base after both are resolved.
Task leasing is scoped to the workflow created by the command so a shared database cannot feed it a
ready task belonging to another workflow.

## Consequences

- The same Eldridge installation can govern multiple registered repositories.
- Provider selection is reviewable before execution rather than inferred from whichever account is
  logged in.
- Preflight rejects stage assignments that exceed a provider's configured risk, classification, or
  subscription workflow ceiling.
- A manifest cannot broaden the repository registry's path grant.
- A manifest cannot select an arbitrary historical or unapproved commit from the registered Git
  repository.
- Operator execution cannot consume ready tasks from another workflow.
- Exact base commits and idempotency keys make reruns attributable.
- Operators must still run repository-specific checks and disposition the exact candidate.

## Alternatives rejected

- Embed a Python harness per project: difficult to reuse and audit consistently.
- Accept repository paths and provider choices as ad hoc CLI strings: too easy to mistype or expand.
- Automatically approve or open a PR after model review: collapses the human authority boundary.
