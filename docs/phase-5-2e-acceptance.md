# Phase 5.2E Independent Review and Reconciliation Acceptance

## Outcome

Eldridge can now execute durable independent reviews over trusted evaluation artifacts. High-risk
candidates require two policy-eligible, cross-family local reviewers, and ambiguous producer or
reviewer calls stop for an immutable human reconciliation instead of being retried.

## Verified controls

| Control | Evidence |
|---|---|
| Durable review intent | Candidate/reviewer rows commit before provider contact |
| Producer independence | Review routing excludes the producer; high-risk routing also excludes its provider family |
| Reviewer diversity | High-risk orchestration selects distinct reviewer families for each candidate |
| Exact provenance | Reviewer provider, family, model, profile, request digest, usage, latency, and status are retained |
| Digest binding | Each review stores both its evidence digest and the exact candidate-output digest reviewed |
| Controller validation | Only bounded, schema-valid code-review results can become independent-review evidence |
| Cost containment | Candidates that fail required deterministic checks do not consume reviewer calls |
| Unknown containment | Provider exceptions become `UNKNOWN`; review decisions and campaign submission pause |
| Conservative reconciliation | A human may mark unknown runs failed with rationale; success and blind retry are unavailable |
| Replay safety | One reconciliation exists per target and idempotent replay does not repeat provider calls or campaign decisions |
| Existing authority | Deterministic checks, independent reviews, evaluation policy, and human-only commands remain separate controls |

## Evidence scope

Acceptance uses deterministic providers, API tests, PostgreSQL migration checks, and the normal local
CI/package pipeline. It does not claim that Claude Pro supplies Anthropic API credits, that a live
Claude or Devin call ran, or that external review is production-qualified. External review remains
an explicit policy and spending decision.

The next Phase 5.2 slice is bounded repair plus revision-bound, human-controlled promotion. Process
recovery for an assessment interrupted while validators or reviewers are running is also still
required before hosted production.
