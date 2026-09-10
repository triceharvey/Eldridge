# Phase 4.3 Acceptance Record

## Scope

Phase 4.3 is complete for the approved local non-production slice as of 2026-09-09. The accepted
scope is one explicitly activated adapter that updates and verifies a pre-created release-marker
ConfigMap in an ephemeral k3d cluster. It does not deploy the Eldridge application, run
`tofu apply`, create a hosted resource, enable production, or authorize rollback execution.

## Implemented controls

| Boundary | Evidence |
|---|---|
| Default denial | The normal service still has no credentialed adapter and installs `DenyCredentialBroker`; local execution also requires the explicit activation flag and a session broker or factory |
| Exact human authority | The service accepts only one active local development environment and one exact, unexpired, unconsumed `DEPLOY` approval bound to environment, merged revision, plan digest, and policy version |
| Durable intent | The `RUNNING` attempt and consumed approval commit before broker construction, credential issuance, or target contact |
| Exact plan | One `UPDATE_SERVICE` operation targets only `configmap/eldridge-release` and binds one SHA-256 artifact, Git revision, plan digest, fixed probes, and fixed rollback reference |
| Short-lived delivery | A fresh plan-bound broker validates and delivers a 600-second Kubernetes token only inside the trusted adapter callback; durable evidence stores only sanitized handle metadata |
| Network boundary | The adapter accepts only an explicit loopback HTTPS Kubernetes API URL and disables proxy inheritance in the live client |
| Least privilege | Four intended permissions passed; eight secret, create, delete, other-resource, lateral-namespace, and cluster-scope actions were denied |
| Observation and verification | A separate target read verified exact revision, plan digest, and artifact digest rather than trusting the update response |
| Idempotency | The first live execution reported `changed=true`; the exact replay reported `changed=false`, both verified successfully |
| Ambiguous outcome | A write timeout enters durable `UNKNOWN`; the same key cannot be replayed automatically |
| Failed verification | A post-change observation failure enters `ROLLBACK_REQUIRED`; no success transition is possible without verified evidence |
| Teardown and cost | The named cluster and matching containers were absent after the run; no cloud resource or paid service was used, so incremental infrastructure cost remained USD 0 |

## Live evidence

The live exercise ran against implementation commit
`85aafcb89d46a7798ae0462fbdeb3cada4e70fc6`. The single artifact was the identity-boundary
manifest at
`sha256:3feafc42cf6ad60dfaeae88a25c75ee5bcd960562f9321d0a7fa246f0e5267ba`; its immutable
deployment-plan digest was
`5d0748a1f7ee39c86fe577a437ccff32e44a3be7757e430059a1fe9c5d6bfe15`.

The concrete `kubernetes-token-request-v1` broker issued a 600-second credential for
`system:serviceaccount:eldridge-validation:deployment-worker`, audience
`https://kubernetes.default.svc.cluster.local`, and resource scope
`namespace/eldridge-validation/configmap/eldridge-release`. Sanitized output confirmed a real,
non-simulated update and successful replay verification without exposing the credential.

The complete suite passed with 201 tests and four intentional opt-in skips. The separate live k3d
selection passed with one test and 204 deselected tests. Ruff, formatting, mypy, shell syntax, and
diff checks passed before the live run.

## Remaining boundary

Phase 4.4 must deliberately inject failed verification, require an explicit rollback decision,
execute only the recorded rollback reference, independently verify recovery, and retain timing
and audit evidence. Until then, production deployment and rollback automation remain disabled.
