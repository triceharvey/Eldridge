# Phase 5.2C Committed Provider Fan-Out Acceptance

## Outcome

Eldridge can now execute a human-authorized, policy-eligible local evaluation matrix while preserving
durable intent before contact, exact output provenance, replay safety, and conservative failure
semantics. The path remains bounded to the approved USD 0 operating profile.

## Verified controls

| Control | Evidence |
|---|---|
| Human-only execution | `EXECUTE_EVALUATION` is granted to the human approver and denied to agents |
| Authorization before I/O | Provider health checks cannot be triggered before human authorization |
| Committed intent | Execution and provider/variant rows are committed before submit; providers observe `RUNNING` |
| Policy fan-out | Current capability, classification, risk, evidence, health, egress, and campaign ceilings select candidates |
| Exact matrix | Every selected provider is paired with every authorized prompt variant, bounded to eight concurrent calls |
| Snapshot binding | Workflow version, candidate revision, iteration, prompt contract, provider/model/profile, and request digests are retained |
| Replay safety | Actor-scoped idempotency returns the original execution and performs no additional provider calls |
| Zero-cost enforcement | Nonzero-cost campaigns require a future estimator; external-egress candidates are explicitly rejected |
| Output validation | Provider identity, exact model, task schema, integer usage, and a one-MiB output limit fail closed |
| Ambiguous outcome containment | Provider exceptions and unexpected worker failures become `UNKNOWN` without blind retry |
| Stale snapshot containment | Workflow drift before dispatch fails every prepared run without provider contact |
| No promotion bypass | `OUTPUTS_READY` grants no validation, campaign winner, merge, deployment, or publication authority |

## Current boundary

This slice captures structured provider output and its digest but does not yet run trusted repository
validators or translate those results into campaign candidate evidence. `UNKNOWN` runs require an
explicit reconciliation design. The next slice should create revision-bound artifacts, run trusted
deterministic checks outside the provider boundary, request independent review where policy requires
it, and submit the resulting evidence to the existing campaign decision core. Bounded prompt repair
and explicit human-controlled artifact promotion follow that connection.
