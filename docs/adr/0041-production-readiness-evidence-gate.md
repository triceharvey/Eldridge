# ADR 0041: Require a Fail-Closed Production Readiness Evidence Gate

- Status: Accepted
- Date: 2026-09-13
- Accepted by: project owner on 2026-09-13

## Context

Eldridge's local engineering controls are substantially complete, but hosted production status
depends on environment-specific facts that source code cannot prove. Those facts include image
publication and scanning, DNS and TLS, OIDC behavior, hosted PostgreSQL, GitHub webhook delivery,
metrics access, backup restoration, and the protected end-to-end workflow. A prose checklist is too
easy to interpret inconsistently or declare complete without revision-bound evidence.

## Decision

Add a strict, bounded, machine-readable production readiness manifest and CLI evaluator. The
evaluator must reject missing and unknown fields, mutable image references, placeholder endpoints,
credential-bearing URLs, non-production environment declarations, failed checks, stale or future
timestamps, revision mismatches, duplicate required checks, nonzero high or critical image findings,
and backup restorations that precede their backups. The accepted manifest is canonicalized and bound
to a SHA-256 digest.

The command exits zero only when every gate passes, and exits two for a complete but non-ready or
invalid evidence declaration. It must never interpret the manifest as authorization to deploy,
merge, issue credentials, create infrastructure, or contact an external service.

## Consequences

- Production status becomes a deterministic evidence decision instead of a narrative claim.
- A release revision must agree with webhook, restored-backup, and protected-workflow evidence.
- Evidence older than 30 days must be refreshed before release.
- The evaluator validates the submitted metadata and bindings; it does not independently prove that
  an external observation is truthful. Collection and retention of the underlying evidence remain
  trusted operator responsibilities until separately automated.
- A passing readiness report remains subject to the owner's final production activation approval.

## Rejected Alternatives

- **Declare production readiness from passing unit tests:** unit tests cannot prove hosted identity,
  network, storage, or recovery behavior.
- **Use an unstructured checklist:** it cannot enforce required fields, revision bindings, freshness,
  or deterministic automation behavior.
- **Automatically deploy when readiness passes:** evidence validation and deployment authority are
  deliberately separate controls.
