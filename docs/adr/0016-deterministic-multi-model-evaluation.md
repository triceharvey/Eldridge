# ADR 0016: Deterministic Multi-Model Evaluation and Refinement

## Status

Accepted for implementation on 2026-09-10 as the first Phase 5.2 production slice.

## Context

Eldridge is intended to test authorized asks and prompt variants across eligible models, use each
model's demonstrated strengths, and refine outputs through a CI/CD-like feedback loop. Naive model
voting, model-written scores, or unbounded retries would create correlated errors, cost surprises,
evidence poisoning, and an unclear promotion authority. Candidate arrival order must not decide which
artifact wins, and a better-sounding response must not outrank failed deterministic controls.

## Decision

Add a pure, fail-closed `MultiModelEvaluator` that consumes externally produced candidate evidence;
it does not invoke providers or execute tools. A versioned policy bounds unique provider/model
candidates, prompt variants, iterations, and total cost. Every successful candidate is bound to its
provider, family, model and profile versions, prompt contract and variant, iteration, output digest,
latency, cost, routing score, and named deterministic checks.

Only candidates that succeeded and passed every required deterministic check may be ranked. The
existing policy-derived routing score is the first ranking input, followed by lower cost and latency
and stable identity tie-breakers. Provider self-scores and prose judgments are not accepted. Medium
risk may configure independent review; high and critical risk require at least two passed reviews from
distinct provider IDs outside the producing provider family, with exact reviewer model and profile
identity retained. Every check and review explicitly names the candidate output digest it assessed,
preventing stale evidence from being attached to a different output. A batch that exceeds any ceiling fails
closed. If no candidate passes, the evaluator requests the next bounded iteration or records
exhaustion when the iteration or cost boundary prevents further work.

## Consequences

- Candidate selection is deterministic, replayable, and independent of completion order.
- Zero-cost local candidates can iterate under a zero-dollar ceiling; any positive cost still fails.
- Failed check names provide bounded refinement feedback without allowing a model to rewrite policy,
  tests, permissions, or approval requirements.
- A winning candidate is an evaluation result, not permission to merge, deploy, publish, alter canon,
  or impersonate human approval.
- Model fan-out, durable campaign records, API commands, retry scheduling, artifact promotion, and
  project-specific validators remain separate implementation slices.

## Alternatives

- **Ask models to vote or grade one another:** rejected because persuasive prose is not objective
  evidence and related models can share failure modes.
- **Choose the first passing response:** rejected because network timing would make outcomes
  nondeterministic and systematically favor speed over the operator's routing objective.
- **Retry until something passes:** rejected because unbounded iteration can hide systematic failure
  and create uncontrolled spend.
- **Let a winner advance workflow state automatically:** rejected because evaluation cannot replace
  revision-bound validation, independent review, or human approval.
