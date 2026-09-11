# ADR 0019: Trusted Evaluation Artifacts and Checks

## Status

Accepted for implementation on 2026-09-10 as the fourth Phase 5.2 production slice.

## Context

Phase 5.2C captures policy-eligible model output but `OUTPUTS_READY` is not proof that an answer is
correct, safe, or suitable for promotion. The decision core already rejects missing, failed, or
unbound checks. It now needs a controller-owned path that creates those artifacts and checks without
accepting a model's claims about its own output.

## Decision

Add a human-authorized evaluation assessment command. It accepts only a terminal, non-ambiguous
provider execution whose workflow version, candidate revision, campaign, and iteration are still
current. Before validation, Eldridge persists an assessment, one immutable artifact per successful
provider/variant run, and one check intent per campaign-required validator.

Validators are named, versioned, controller-configured code. They run only after the intents commit.
Each completed check stores a bounded JSON result, pass/fail decision, validator version, validated
output digest, and SHA-256 evidence digest. The default local validators revalidate the task schema
and canonical output digest, reject sensitive or authority-bearing output fields, and verify the
stored workflow snapshot binding. A missing validator fails closed before artifact creation.

After checks commit, the service constructs candidate evidence from the stored provider run,
artifact, and check rows and submits it through the existing campaign decision boundary. Submission
is replay-safe, and a crash after batch creation can replay the same derived command without creating
a second decision. The finalized assessment also creates one append-only, version-specific routing
observation per provider run. Only controller-derived execution and decision results enter that
evidence; producer self-scores do not. Provider health checks remain outside campaign transactions.

## Consequences

- A model cannot assert that its own output passed validation.
- Every check is explicitly bound to the exact output digest it assessed.
- Failed checks drive the existing bounded refinement decision rather than hidden repair.
- Trusted results join direct task observations in the same bounded recent routing window, while
  provider, model, profile, and capability versions remain isolated.
- High-risk candidates still cannot win without the required independent model reviews.
- Assessment artifacts are bound to an exact workflow version and to the candidate Git revision when
  one exists; planning output with no candidate commit remains version-bound, not falsely described
  as commit-bound.
- Durable independent-review execution, recovery of interrupted assessments, `UNKNOWN` provider
  reconciliation, bounded repair, and explicit promotion remain subsequent gates.

## Alternatives

- **Trust provider-reported checks:** rejected because the producer is not an independent authority.
- **Run checks before recording intent:** rejected because a crash would leave untracked evidence.
- **Treat schema validity as engineering correctness:** rejected; schema is only one required check.
- **Satisfy high-risk review with deterministic validators:** rejected because that would mislabel
  validation diversity as independent model review.
