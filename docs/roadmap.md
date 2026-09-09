# Phased Implementation Roadmap

Each phase is an independently reviewable vertical slice. Later phases begin only when the prior exit criteria are demonstrated. Dates are intentionally omitted until scope and available engineering time are known.

## Phase 0 — Architecture and threat model (completed)

Approved by the project owner on 2026-09-06.

Deliverables: architecture, workflow, security model, provider contract, data model, API boundaries, threat model, observability plan, ADRs, and explicit approval decisions.

Exit criteria:

- the human owner approves or amends the decisions below;
- unresolved high-risk assumptions have an owner and disposition; and
- Phase 1 scope remains implementation-free until approval.

## Phase 1 — Deterministic control-plane skeleton (implemented locally)

Build a Python package with FastAPI, Pydantic, PostgreSQL persistence/migrations, an API process, a worker process, a deterministic mock provider, and an in-memory/mock executor that cannot run arbitrary commands. Implement one workflow template through `AWAITING_HUMAN_APPROVAL`.

Required tests cover state transitions, role/grant authorization, SoD, approval binding/invalidation, task leases, idempotency, retry classification, mock-provider conformance, audit atomicity, and API commands.

Exit criteria: a local demonstration completes the mock workflow, forbidden transitions fail, an agent cannot approve itself, restart preserves state, and tests run in CI.

Local exit evidence is complete: the demonstration reaches an exact-revision human gate, the SQLite-backed suite and PostgreSQL lease integration test pass, migrations match the ORM schema, and CI configuration is present. The first hosted CI run remains pending until the repository is connected to a remote Git platform.

## Phase 2 — Isolated code-change execution (in progress)

Add per-task Git branches/worktrees and an ephemeral Docker-compatible executor. Use a pinned non-root image, no host socket, resource limits, limited mounts, network deny-by-default, typed tools, and artifact validation. Add one low-risk real provider only after its egress and credential decisions are approved.

Exit criteria: two agents can work concurrently without collision; a sandbox cannot access control-plane credentials or host paths; timeouts/cancellation clean up safely; and a real-provider task cannot bypass the same policy gates as the mock.

Implemented: concurrent Git worktree lifecycle, operator-controlled repository registration, worker-composed worktree and Docker execution, real Git revision binding, digest-pinned non-root containers, network-deny and least-mount policy, typed/path-scoped tools, output/resource bounds, timeout and branch cleanup, Claude and Devin adapters, authenticated localhost Windsurf MCP handoff, persisted workflow classification, capability routing, auditable decisions, versioned performance observations, cross-provider review diversity, configurable high-risk qualification, adversarial-input containment, human-only contained resume/rejection, transaction splitting around external calls, active lease heartbeats, late-result rejection, human retry/fail reconciliation for unknown outcomes, and strict runtime provider activation with egress, classification, risk, cost, secret-reference, and policy-version controls. Remaining: operator review of provider account terms and pricing, explicit policy approval, and the separately opted-in live-provider probes. Devin task routing remains gated on Phase 3 revision and CI-evidence ingestion.

## Phase 3 — Git and CI integration (implemented locally; activation evidence pending)

Integrate pull-request creation, status checks, branch-protection verification, test/security evidence ingestion, OIDC-backed human identity, metrics, and basic dashboards. The system may propose a merge, but the Git platform independently enforces protected-branch rules.

Exit criteria: evidence is bound to a commit digest, stale approval is rejected after a new commit, branch protection blocks direct push, and reconciliation handles callback duplication or loss.

Implemented: disabled-by-default GitHub `check_run` webhook ingestion with HMAC-SHA256 verification, a least-authority CI integration identity, bounded payload validation, exact repository/revision matching, durable delivery idempotency, normalized success and failure evidence, payload digests, audit events, and no webhook-driven state transition. A separately disabled GitHub App client uses RS256 app identity and repository-scoped installation tokens to verify a remote branch head, create draft pull requests, and independently assess exact-revision checks and branch protection against operator policy. It requests no contents-write or merge authority. Durable human-authorized workflow commands persist PR intent before external execution, prevent idempotency-key drift, contain ambiguous outcomes as `UNKNOWN`, reconcile through a read-only remote search, and record revision-bound readiness snapshots. Production commands require allowlisted, signed OIDC identity. After a human merges in GitHub, the control plane independently confirms the exact PR, head revision, and merge commit through read-only GitHub calls before entering `MERGED`. Durable low-cardinality Prometheus metrics, an authenticated aggregate dashboard, separate non-root API and worker images, one-shot migrations, internal service networking, and Caddy automatic-TLS ingress are packaged with fail-closed operator inputs.

Current rough status: Phase 0 and Phase 1 are complete; Phase 2 is about 95% complete pending operator-approved live-provider evidence. Phase 3 engineering is locally complete, and its least-authority GitHub App has been installed and exercised against a revision-bound draft PR. Final operational sign-off still requires environment-specific proof that cannot be fabricated in source code: immutable published image digests and scans, public DNS/TLS, a real OIDC tenant mapping, hosted PostgreSQL, protected-branch enforcement, webhook delivery, metrics scrape, backup/restore, and one complete human-approved PR. Phase 4.1 is implemented locally; Phase 5 remains evidence-gated.

## Phase 4 — Controlled deployment path

Design this phase separately before implementation. Add environment inventory, deployment plans, scoped approval, short-lived deployment credentials, post-deploy verification, rollback decision support, and stronger secret management. Begin with a non-production environment.

The project owner approved the provider-neutral design in `docs/phase-4-deployment-design.md` and ADR 0008 on 2026-09-08. Phase 4.1 is implemented locally with immutable environment and plan records, exact deployment approvals, durable attempts and verification, and a no-credential dry-run adapter. OpenTofu was approved as the provider-neutral infrastructure layer in ADR 0009 on 2026-09-08. The managed Azure and open-source K3s profiles remain proposals; no hosting provider, cloud resource, paid service, credential, or real deployment is authorized.

Exit criteria: the control plane cannot deploy without a valid environment- and revision-bound approval; credentials are short lived; partial deployment enters reconciliation or `ROLLBACK_REQUIRED`; recovery is exercised.

## Phase 5 — Scale and platform evolution

Only measured needs justify Kubernetes jobs, external object storage, PostgreSQL HA, dedicated policy service, queue/broker, multi-tenancy, quotas, and immutable audit export. The provider-routing policy foundation was advanced into Phase 2; Phase 5 operationalizes it with durable performance telemetry, quotas, model-version requalification, load/failure evidence, and SLOs before choosing scaling technology.

## Rough completion estimate

Four roadmap phases remain after Phase 1, with Phase 2 awaiting only provider-policy approval and live integration evidence. Phase 2 makes real code changes safely; Phase 3 produces a strong portfolio-ready control plane with Git, CI, identity, and telemetry; Phase 4 adds controlled deployment; Phase 5 adds production scale and multi-tenant hardening only where justified. In practical terms, this is roughly three full phases plus the remaining live-provider decision and proof.

For one engineer working consistently with AI assistance, a defensible rough range is 8–16 weeks to complete Phases 2–4 and another 4–8 or more weeks for the Phase 5 capabilities that prove necessary. A narrower portfolio demonstration could be ready around the end of Phase 3, while a production-capable deployment path requires Phase 4 and operational testing. These are planning ranges, not delivery commitments; provider integration, identity systems, cloud choice, and deployment target will materially affect them.

## Dependencies and their reasons

| Dependency | Phase | Reason |
|---|---:|---|
| Python | 1 | Clear ecosystem for APIs, validation, automation, and security tooling |
| FastAPI | 1 | Typed HTTP boundary and OpenAPI for a small control API |
| Pydantic | 1 | Validate commands, policies, provider contracts, and evidence |
| PostgreSQL | 1 | Durable transactions, constraints, leasing, JSON metadata, and audit state |
| Database migration tool | 1 | Reproducible, reviewable schema evolution |
| Docker-compatible runtime | 2 | Enforce process/filesystem/resource isolation for task execution |
| Git | 2 | Authoritative artifacts, revisions, branches, and worktree isolation |
| Prometheus/OpenTelemetry libraries | 3 | Standard metrics/tracing interfaces after useful signals exist |

Redis is not justified for the MVP. OpenTofu is approved for Phase 4 infrastructure planning,
but a Kubernetes distribution, Grafana, and a secret manager remain target-specific additions
that require demonstrated need and explicit approval.

## Decisions requiring human approval

These Phase 0 decisions were approved by the project owner on 2026-09-06.

1. **MVP architecture:** approve a Python modular monolith with separate API and worker processes. Alternative: multiple services now. Recommendation: modular monolith.
2. **Durability and queue:** approve PostgreSQL for state, audit, and transactional task leasing, with no Redis in Phase 1. Alternative: broker/Redis. Recommendation: PostgreSQL only.
3. **Phase 1 execution safety:** approve a mock provider and non-command-running fake executor only; arbitrary repository commands wait for Phase 2 sandboxing. Alternative: run subprocesses on the host. Recommendation: never run model-proposed host commands.
4. **Phase 2 isolation:** approve dedicated Git worktrees plus ephemeral containers with network denied by default. Alternative: worktrees alone or stronger microVMs. Recommendation: containers for the first real execution backend, with microVM evaluation if threat exposure grows.
5. **Authorization:** approve role baselines narrowed by immutable task-scoped capability grants and deny-by-default policy. Alternative: simple RBAC only. Recommendation: capability-scoped RBAC.
6. **Approvals:** approve separate, exact-revision-bound approvals for protected merge and deployment; agent identities are never eligible human approvers. Alternative: one final approval for both. Recommendation: separate approvals.
7. **Audit:** approve PostgreSQL append-only, hash-chained audit events for the MVP, recognizing that external immutable retention comes later. Alternative: deploy a dedicated event system now. Recommendation: database event log first.
8. **First provider:** approve mock-only Phase 1 and defer selection of the first commercial/local provider until its data-egress, retention, credential, and cost requirements are reviewed. The provider choice itself remains open.
9. **MVP completion boundary:** approve ending the first demonstration at `AWAITING_HUMAN_APPROVAL`/recorded approval, without automatic merge or deploy. Alternative: implement Git merge in the MVP. Recommendation: approval demonstration only.
10. **Human identity for local development:** approve a development operator identity with an explicit warning that it is not production authentication; require OIDC before shared or remote use. Alternative: build OIDC immediately. Recommendation: development identity in Phase 1, OIDC in Phase 3 or before multi-user access.

Approval of the architecture does not grant approval to deploy infrastructure, connect a paid provider, access secrets, create external repositories, or merge protected branches. Those remain separate actions.
