# ADR 0018: Committed, Zero-Cost Evaluation Fan-Out

## Status

Accepted for implementation on 2026-09-10 as the third Phase 5.2 production slice.

## Context

ADR 0017 made evaluation campaigns durable but deliberately accepted already assembled evidence. A
production fan-out boundary must prove what Eldridge intended to send before contacting a provider,
prevent retries from duplicating work, preserve exact provider and prompt provenance, and contain a
timeout whose remote outcome cannot be proven. The approved local operating ceiling remains USD 0.

## Decision

Add a human-only `EXECUTE_EVALUATION` command. It selects every currently eligible candidate up to
the campaign ceiling and expands each selected provider across the authorized prompt variants. The
service records one immutable execution snapshot and one provider-run record per provider/variant
pair before any submit call. The snapshot binds the campaign iteration, workflow version, candidate
revision, prompt contract, prompt variants, routing policy, provider policy, provider family, model,
profile, and canonical request digest.

Health I/O occurs only after human authorization and outside the persistence transaction. The
execution plan commits as `PREPARED`, then a short transaction verifies that the workflow snapshot is
still current and commits every run as `RUNNING`. Only then may bounded concurrent provider calls
begin. Returned identity, exact model, task schema, usage counters, and the one-MiB output ceiling are
validated before content and its SHA-256 digest are stored.

The slice is deliberately local and zero-cost. A campaign with a nonzero ceiling is refused until a
trusted estimator and reservation ledger exist; a provider with external egress is excluded even if
the global runtime policy permits it. Provider exceptions are `UNKNOWN`, not failed, because the
control plane cannot prove whether the provider accepted work. Malformed or identity-mismatched
responses are `FAILED`. Neither state is retried automatically.

## Consequences

- A restart or network retry can inspect or replay the original command without issuing duplicate
  model calls.
- Every output has exact provider, model, profile, prompt, workflow snapshot, and digest provenance.
- External Claude, Devin, Windsurf, and other priced execution remains available as a future adapter
  path but cannot spend money through this slice.
- `OUTPUTS_READY` means only that structured candidate outputs were captured. It does not mean they
  passed project checks, independent review, artifact promotion, merge, or deployment gates.
- Trusted validator ingestion, bounded repair scheduling, explicit winner promotion, and
  reconciliation of `UNKNOWN` runs remain subsequent slices.

## Alternatives

- **Submit and then write a record:** rejected because a crash could produce an untracked or repeated
  provider call.
- **Retry timeouts automatically:** rejected because a timeout does not prove the remote side did no
  work.
- **Use cost tiers as monetary estimates:** rejected because `LOW` is not equivalent to free and
  cannot enforce a currency ceiling.
- **Let a model promote its own output:** rejected because generation is not independent validation
  or human authorization.
