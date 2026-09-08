# ADR-0005: Scoped Human Approvals

- Status: Accepted
- Date: 2026-09-06

## Context

A generic approval boolean is replayable and ambiguous. Merge and production deployment have different targets, risks, timing, and evidence.

## Decision

Represent approval as an immutable human decision bound to action, workflow, repository, exact revision/artifact digest, target environment, policy version, rationale, and expiry. Merge and deployment require separate approvals. Agents are ineligible approvers.

## Alternatives

- One final workflow approval is simpler but becomes stale after changes and over-authorizes later actions.
- Approval only in a Git/CI UI may be sufficient for some actions but leaves the control plane unable to explain its gate state; external enforcement is still retained.

## Consequences

Approvals are auditable and invalidate safely when inputs change. User experience requires clear presentation of exactly what is being approved.

## Failure behavior

Expired, consumed, mismatched, or ineligible approvals are denied. Uncertain external action state triggers reconciliation rather than reusing or recreating approval automatically.
