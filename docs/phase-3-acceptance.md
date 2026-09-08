# Phase 3 Acceptance Record

Phase 3 engineering is implemented locally. Operational acceptance remains a deliberate human-controlled activation step because the repository has no remote and no real shared environment, identity tenant, GitHub App installation, public DNS name, or hosted database.

## Implemented evidence

| Control | Local evidence |
|---|---|
| Revision-bound CI ingestion | HMAC verification, delivery idempotency, repository/revision matching, normalized conclusions, and negative tests |
| Least-privilege GitHub App | Repository-scoped token requests, no contents-write/merge authority, draft PR proposal, protection/check assessment, read-only merge confirmation |
| Human identity | RS256 OIDC signature, exact issuer/audience/subject, bounded lifetime, HTTPS JWKS, explicit principal mapping, production fail-closed startup |
| Durable Git workflow | PR intent, readiness, reconciliation, exact approval, merge confirmation, state transition, and audit persistence |
| Operations | Durable low-cardinality Prometheus exposition, separate monitoring credential, authorized aggregate JSON, and CSP-hardened dashboard |
| Packaging | Digest-pinned Python base, separate non-root API/worker targets, read-only service filesystems, one-shot migrations, internal network, and automatic-TLS ingress model |
| Portability | Operator policy mounts, hosted PostgreSQL URL, project-root mount, and immutable registry image inputs are configuration rather than baked credentials |

## Required activation evidence

The owner or deployment operator must supply and retain:

1. an initial Git commit and remote with a successful CI packaging run;
2. scanned API, worker, and ingress image digests from the selected registry;
3. hosted PostgreSQL migration plus backup/restore evidence;
4. a real OIDC issuer, audience, JWKS endpoint, stable subject mapping, and positive/negative login evidence;
5. a GitHub App installation permission record, protected base-branch configuration, signed webhook delivery, and enforced checks;
6. public DNS, successful ACME issuance, redirect and TLS scan results;
7. an authenticated Prometheus scrape and authorized dashboard check; and
8. one end-to-end PR that binds the candidate commit, CI evidence, human approval, external merge, merge commit, and audit chain.

These are not code-generation tasks and cannot be represented honestly by fixtures. Phase 4 should not receive production deployment authority until this activation record is completed or the owner explicitly accepts a documented exception.
