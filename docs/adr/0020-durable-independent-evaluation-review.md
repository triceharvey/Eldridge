# ADR 0020: Durable Independent Evaluation Review and Reconciliation

## Status

Accepted for implementation on 2026-09-10 as the fifth Phase 5.2 production slice.

## Context

Phase 5.2D produces controller-validated candidate artifacts, but deterministic checks cannot supply
the independent judgment required for high-risk work. Provider and reviewer calls can also time out
after the remote system may have accepted work. Blind retry could duplicate cost and silently count
different outputs as the same evidence.

## Decision

For campaigns requiring independent review, persist one review intent for every deterministically
eligible candidate/reviewer pair before contacting a provider. Candidates that already failed a
required controller check consume no reviewer capacity. Review routing excludes the producer, requires
a different provider family for high-risk work, selects distinct reviewer families, and retains the
reviewer model/profile versions. The current USD 0 slice permits local reviewers only.

Each reviewer receives the untrusted candidate output and its digest in a bounded code-review
request. The returned schema is validated by the controller. A durable review records pass/fail,
reviewer provenance, evidence digest, reviewed-output digest, sanitized output, usage, latency, and
status. Only successful, digest-bound records become independent-review evidence.

An ambiguous producer or reviewer call enters `UNKNOWN`. Because the present provider interface has
no authoritative read-only result lookup, a human may only reconcile it as failed with a rationale.
The original run is never retried. Producer reconciliation makes the execution `PARTIAL` or
`FAILED`; reviewer reconciliation makes the assessment review-ready and lets normal campaign policy
decide whether another bounded iteration is allowed.

## Consequences

- High-risk selection now has executable independent-review evidence instead of a caller-supplied
  placeholder.
- Review intent survives process failure and evidence is bound to the exact candidate digest.
- Unknown outcomes cannot be converted into success by an operator or model.
- The control-plane API and database gain explicit reconciliation records and states.
- Interrupted `REVIEW_PREPARED` or `REVIEWS_RUNNING` process recovery remains a later operational
  hardening item.
- Claude, Devin, and other external reviewers remain disabled until their separate egress, account,
  cost, and credential conditions are satisfied.

## Alternatives

- **Let the producer review itself:** rejected because agreement with itself is not independence.
- **Retry timeouts automatically:** rejected because the prior call may have succeeded remotely.
- **Permit a human to mark an unknown call successful:** rejected because judgment is not provider
  result evidence.
- **Require an external reviewer now:** rejected because it would violate the approved zero-cost
  default and make local acceptance dependent on a commercial account.
