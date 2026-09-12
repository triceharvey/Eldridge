# ADR-0030: Permit bounded subscription evaluation at the zero-dollar ceiling

- Status: Accepted
- Date: 2026-09-12

## Context

Eldridge's evaluation pipeline rejected every external provider whenever a campaign's monetary
ceiling was zero. That was correct for metered APIs, but it also blocked the separately approved
Claude Code subscription boundary even though that boundary consumes existing plan allowance and
cannot silently fall back to API billing.

Treating a subscription as unlimited or cost-free would be equally inaccurate. Plan allowance is a
finite operator-owned resource, and external egress, data classification, risk, provider health,
and independent review still require explicit policy.

## Decision

Classify provider funding as `LOCAL`, `SUBSCRIPTION`, or `METERED`. A zero-dollar campaign continues
to reject every metered external provider. It may select an external subscription provider only when
normal routing policy permits it and the requested prompt count does not exceed that provider's
explicit per-execution invocation ceiling. The routing snapshot records both the funding mode and
ceiling used for the decision.

Claude Code also receives a task-specific JSON Schema through its CLI boundary. Prose instructions
remain useful context, but only schema-constrained output is admitted to the deterministic validator.
The live acceptance path uses Claude as producer and a digest-pinned, loopback-only Qwen model as an
independent cross-family reviewer. Promotion remains a separate human command bound to the exact
validated artifact digest.

## Alternatives considered

- Keep all external providers blocked at zero dollars. Rejected because it prevents controlled use
  of an already approved subscription while adding no protection against plan-allowance depletion.
- Treat the subscription as local or unlimited. Rejected because it crosses an external egress
  boundary and consumes finite plan allowance.
- Depend on the model to follow a prose JSON contract. Rejected after the first live attempt failed
  closed on an invalid response shape.
- Let the producer review its own result. Rejected because a distinct local provider family was
  available within the approved zero-dollar boundary.

## Consequences

- Existing subscription capacity can support narrowly bounded evaluation without authorizing API
  charges or changing the Phase 4.2 monetary ceiling.
- Routing evidence distinguishes funding source from cost tier and records the exact invocation
  allowance applied.
- Schema violations, allowance exhaustion, provider ambiguity, or reviewer failure remain contained;
  none can trigger fallback, promotion, merge, or deployment.
- This decision does not grant repository read/write tools, create a pull request, or qualify a
  hosted production environment.

## Failure behavior

Metered external selection under a zero-dollar campaign and subscription requests above the exact
invocation ceiling fail before provider contact. Invalid provider output fails closed. Ambiguous
producer or reviewer calls enter the existing human reconciliation path; they are never retried
blindly or treated as successful.
