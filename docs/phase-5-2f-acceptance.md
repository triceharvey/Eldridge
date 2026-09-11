# Phase 5.2F Bounded Repair, Recovery, and Promotion Acceptance

## Outcome

Eldridge now closes the durable Phase 5.2 refinement loop. Failed evaluation evidence can lead only
to a bounded, human-committed repair plan; interrupted assessments have a conservative recovery path;
and only a human can promote the exact digest selected by the trusted decision core.

## Verified controls

| Control | Evidence |
|---|---|
| Repair provenance | Source batch, rejected-candidate snapshot, source/target iterations, prompt variants, rationale, and workflow snapshot are immutable |
| Repair bounds | Every post-initial execution requires one matching plan and remains under campaign iteration, prompt-variant, candidate, cost, routing, and egress limits |
| No prompt drift | Execution rejects variants that differ from the committed repair plan |
| Conservative recovery | Interrupted checks and never-dispatched review intents become failed evidence; dispatched reviews become `UNKNOWN` |
| Multi-interruption support | Recovery uniqueness is scoped by assessment and prior state, allowing a later review-stage interruption without overwriting history |
| Human promotion | Capability policy and principal type both require an eligible human |
| Exact winner binding | Promotion binds campaign, assessment, batch, candidate, artifact ID/digest, workflow version, and candidate revision |
| No authority inheritance | Promotion changes no workflow state and grants no merge, deployment, publication, secret, or provider-enablement authority |
| Audit visibility | Campaign reads include repairs/promotions; assessment reads include recoveries/promotion; append-only audit events retain each decision |

## Evidence scope

Acceptance is based on deterministic providers, focused API/service tests, static type and lint checks,
and PostgreSQL migration verification. Claude Code supplied a read-only independent review using the
owner's Claude Pro session; its repeat-recovery finding was corrected. Claude did not edit, merge, or
receive credentials. No Anthropic Messages API or Devin call was made.

The next pre-production step is the service-boundary decomposition already identified by external
review, followed by one explicitly authorized real model-driven engineering workflow through
generation, validation, independent review, protected PR, and human disposition.
