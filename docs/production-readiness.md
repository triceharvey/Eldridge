# Hosted Production Readiness

Eldridge is not declared production-ready until one exact release revision satisfies all eight
environment-specific gates below. Run the evaluator with an operator-owned manifest:

```bash
.venv/bin/control-plane production readiness \
  --manifest production-readiness.json
```

Use `production-readiness.example.json` as a field guide. The real manifest must remain outside Git
if its surrounding evidence references reveal internal infrastructure. The command prints bounded
JSON and returns:

- `0` when every gate passes;
- `2` when evidence is missing, invalid, stale, unbound, or records a failed check; and
- a nonzero command error when the file is unreadable, oversized, or invalid JSON.

## Required Gates

| Gate | Required evidence |
|---|---|
| Immutable images | Digest-pinned API, worker, and ingress images; passing scan; zero high or critical findings |
| Public TLS | Real HTTPS endpoint, DNS resolution, valid certificate, and minimum TLS 1.2 |
| OIDC | Real HTTPS issuer/JWKS, stable mapped subject, and successful positive and negative authentication tests |
| PostgreSQL | Non-local host, verified TLS, migration at head, and successful connectivity test |
| GitHub webhook | Delivery ID, valid signature, duplicate-delivery behavior, and exact release revision |
| Metrics | Successful authenticated scrape and rejected unauthenticated scrape |
| Backup/restore | Backup identity, ordered timestamps, restored release revision, verified restore, RPO at most 24 hours, and RTO at most one hour |
| Protected workflow | Workflow and PR identity, blocked direct push, required `gitleaks`, `package`, and `test` checks, human approval, exact head revision, and merge commit |

Every observation must be timezone-aware and no more than 30 days old. The canonical manifest digest
binds the full declaration. The evaluator does not contact endpoints or authenticate claims; retain
the underlying scan, TLS, identity, webhook, metrics, database, recovery, and GitHub records for
independent review.

Passing this command is necessary but not sufficient to activate production. The owner must still
approve the exact host, cost ceiling, release revision, environment plan, credentials, and rollback
procedure.
