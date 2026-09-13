# ADR-0034: Preserve Substantive Negative Reviews as Evidence

## Status

Accepted on 2026-09-12.

## Context

The first reusable operator-command exercise produced a candidate that passed its isolated tests but
received a non-passing security decision from the independent local reviewer. Eldridge's response
schemas required successful test and security decisions, and validation treated an explicit
rejection like a malformed provider response. The task retried and the durable attempt retained only
a generic error, erasing the findings needed for controlled refinement.

An independent reviewer must be able to say no. Retrying the same substantive rejection wastes model
capacity and can create pressure toward an unjustified pass. Discarding its findings also breaks the
continuous-improvement evidence chain.

## Decision

Test, security, and code-review contracts accept boolean decisions. A structurally valid false
decision raises a distinct `ProviderReviewRejectedError` carrying the provider's structured output.
The workflow records that output as negative-review evidence, terminates the task and workflow after
one attempt, and does not advance the candidate.

Malformed, unavailable, or timed-out provider responses retain the existing bounded retry policy and
generic redaction behavior. A model's negative decision remains advisory evidence; it does not claim
human authority or place the workflow in the human `REJECTED` state.

## Consequences

- Independent reviewers can block a candidate without violating their output schema.
- Their findings remain available for a new, separately identified refinement workflow.
- Substantive rejections do not consume a second provider attempt.
- Provider observations distinguish structurally valid rejection from malformed output.
- Rejected candidates cannot reach the human approval gate.

## Alternatives Rejected

- Require every reviewer to return success: converts independent review into confirmation pressure.
- Retry false decisions: spends capacity without changing the candidate under review.
- Store only a generic validation error: preserves redaction but destroys actionable review evidence.
- Automatically repair and resubmit: would cross the deliberate workflow and human-control boundary.
