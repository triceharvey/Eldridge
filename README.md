# Multi-Agent AI Engineering Control Plane

[![CI](https://github.com/triceharvey/Eldridge/actions/workflows/ci.yml/badge.svg)](https://github.com/triceharvey/Eldridge/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)

**Status:** Phase 4 controlled-deployment implementation and private validation. The control plane is not yet a
hosted service, and all commercial providers and privileged integrations remain disabled by
default.

This repository defines a production-minded control plane for coordinating specialized AI engineering agents under explicit policy, isolation, audit, and human approval. The system is not a group chat for models. It is a workflow engine in which agents are treated as untrusted, non-human service identities and deterministic controls outrank model recommendations.

The repository has completed **Phase 0** and the **Phase 1 deterministic control-plane skeleton** approved on 2026-09-06. Phase 2 engineering controls are complete locally except for explicitly approved paid-provider canaries. Phase 3 engineering is locally complete: revision-bound GitHub CI evidence, draft PR proposals, independent merge-readiness assessment, OIDC-backed production identity, read-only post-merge confirmation, durable Prometheus metrics, an authenticated dashboard, and TLS-ready container packaging. Phase 4 engineering is complete for the approved USD 0 local target: immutable plans, exact expiring deployment and rollback approvals, short-lived operation-bound workload identity, bounded deployment, verification, containment, and controlled recovery are implemented and exercised. Environment-specific hosted activation evidence remains pending. The system cannot execute a merge, perform a production deployment, access production secrets, or contact an external model provider by default.

The worker composes an operator-registered repository, per-task Git worktree, typed model tool proposals, and the hardened Docker executor. Successful task-branch commits are bound to the real Git revision. Provider and executor calls occur outside database transactions under renewable leases; expired running work blocks for explicit human reconciliation, and late results are rejected. External provider activation remains an explicit human decision.

Strength-aware routing is now in the worker path. It persists every decision, learns from bounded version-specific validation evidence, discounts weak samples, enforces provider-family diversity for high-risk review, and contains obfuscated or suspicious inputs before egress or tool use. Claude and Devin adapters remain disabled by default. Windsurf can use a separately activated, localhost-only MCP handoff boundary.

## Executive architecture summary

The proposed MVP is a Python modular monolith with two runtime entry points: a FastAPI control-plane API and a worker. Both use PostgreSQL as the durable source of workflow state. The worker leases queued tasks transactionally, selects an eligible provider and isolated executor, and records results as immutable audit events. A policy enforcement point validates every state transition and privileged action. Human approvals are scoped records—not chat messages or booleans—and cannot be created by an agent.

Initial execution uses a deterministic mock provider so workflow, permissions, failure handling, and approval gates can be tested without sending code or prompts to an external model. The first real execution backend should create a dedicated Git worktree and run the assigned task in an ephemeral container with a read-only base image, resource limits, no host socket, and network denied by default.

PostgreSQL is sufficient for the MVP's state, audit, leasing, and retry needs. Redis, Kubernetes, Kafka, vector databases, service meshes, and a full telemetry stack are deferred until measured requirements justify them.

```mermaid
flowchart LR
    H["Human operator"] -->|authenticated request or approval| API["Control-plane API"]
    API --> PEP["Policy and transition guard"]
    PEP --> DB[("PostgreSQL\nstate, leases, approvals, audit")]
    W["Workflow worker"] -->|lease ready task| DB
    W --> PEP
    W --> R["Agent runtime"]
    R --> PA["Provider adapter"]
    PA --> MP["Mock provider"]
    PA -. later .-> EP["External or local providers"]
    R --> EX["Isolated executor"]
    EX --> WT["Per-task Git worktree"]
    EX --> C["Ephemeral container"]
    C -->|artifacts and result only| R
    W --> OBS["Structured logs and metrics"]
```

## Smallest useful MVP

The MVP accepts an engineering request, runs a mock planning and independent review sequence, creates an implementation task, records mock implementation/test/security/code-review evidence, and stops at a scoped human merge-approval gate. It demonstrates:

- a durable workflow and task model;
- enforced transitions, including rejection of skipped stages;
- agent identities, roles, capabilities, and assignment scope;
- provider and execution interfaces with deterministic fakes;
- append-only audit events correlated by workflow, task, run, and agent;
- retries, timeouts, cancellation, and manual recovery paths;
- explicit human approval that agents cannot self-grant; and
- unit, integration, policy, state-machine, and failure-path tests.

The MVP does **not** merge, deploy, manage production secrets, or call every commercial provider. A later slice can add a sandboxed real provider and create a proposed change branch, while protected-branch rules remain outside the control plane as an independent enforcement layer.

## Phase 1 implementation

The current implementation provides:

- a FastAPI command/query API and independently runnable worker;
- SQLAlchemy persistence with an Alembic migration and PostgreSQL task leasing;
- a deterministic state machine with forbidden-transition enforcement;
- human, service, and agent principals with role baselines and task-scoped grants;
- six specialized mock-agent stages from planning through final code review;
- typed provider and executor interfaces with structured result validation;
- bounded retries, lease expiry/reclamation, cancellation, and idempotent submission;
- committed execution intent, active lease heartbeats, late-result rejection, and human retry/fail reconciliation;
- exact-revision human merge approvals that agents cannot create;
- hash-chained structured audit events and revision-bound artifacts; and
- unit, API, policy, failure, provider, audit, and PostgreSQL integration tests.

## Phase 4.1 controlled-deployment foundation

The approved Phase 4.1 slice provides:

- operator-owned immutable development or staging environment records;
- deterministic SHA-256 deployment plans bound to a confirmed merge revision and artifact digests;
- typed deployment operations with no shell or arbitrary provider request surface;
- a separate, expiring human `DEPLOY` approval bound to environment, revision, plan digest, and policy version;
- execution intent committed before adapter invocation and one-time approval consumption;
- durable deployment attempts and independent verification records; and
- a dry-run adapter that is structurally prohibited from requesting credentials or contacting a target.

Production environments and credential-requiring adapters fail closed in Phase 4.1. A successful
dry run proves orchestration and policy behavior but never marks a workflow `DEPLOYED`.

## Phase 4.2 zero-cost identity boundary

The approved first target is local Docker with optional ephemeral k3d and a USD 0 incremental
infrastructure ceiling. OpenTofu is the provider-neutral infrastructure layer. A deny-by-default
credential broker prevents accidental issuance, while a policy-bound fake broker returns only
simulated issuance metadata: it creates no token, exposes no secret, and contacts no issuer.

A later temporary hosted exercise is capped at approximately USD 5 total and remains subject to
a separate provider, plan, and execution decision. Azure and persistent hosted K3s remain future
alternatives rather than active dependencies.

The first OpenTofu validator now produces sanitized, digest-bound evidence from JSON plan
fixtures and rejects unpinned providers, destructive actions, imports, provisioners, child
modules, drift, sensitive markers, changed outputs, and any cost above zero. Its local adapter is
simulation-only and proves that it starts no subprocess and contacts no target.

The ephemeral k3d identity exercise is also complete. A digest-pinned, single-server local
cluster verified two intended namespace permissions, five sensitive/write/lateral denials, a
subject- and audience-bound ten-minute projected service-account token, and automatic teardown.
The token was never logged or persisted, and no named cluster, container, network, or volume
remained afterward.

Phase 4.2 now connects that mechanism to the `CredentialBroker` contract through an explicitly
enabled Kubernetes TokenRequest broker. It rejects any environment, adapter, operation, scope,
audience, subject, digest, or lifetime mismatch before issuance; revalidates the returned claims;
discards the token; and returns only a random opaque reference plus sanitized metadata. The
normal runtime still installs the deny-all broker, and no credential-requiring deployment adapter
is enabled.

## Phase 4.3 bounded local deployment

The explicitly activated `local-k3d-configmap-v1` adapter updates only the pre-created
`eldridge-release` ConfigMap over a loopback Kubernetes API. It accepts one artifact-bound typed
operation and the fixed local environment, verification probes, and rollback reference. The
service commits intent and consumes the exact deployment approval before a plan-bound broker can
issue a 600-second credential. Raw token material exists only inside the broker-to-adapter
callback and is excluded from durable evidence.

The live exercise proved a real first update and an idempotent no-change replay, separate target
observation, exact revision/plan/artifact verification, four intended RBAC permissions, eight
explicit denials, ambiguous-write containment, and complete cluster cleanup at USD 0. This is a
control-path release marker, not a full application deployment. Phase 4.4 extends this exact
boundary with separately authorized recovery.

## Phase 4.4 controlled recovery

Recovery is a separate privileged command with distinct approval and execution capabilities. Its
approval binds the exact failed deployment attempt, environment, plan digest, revision, policy,
rollback reference, rationale, and expiry. The service persists intent and consumes that approval
before obtaining a new 600-second credential explicitly bound to `ROLLBACK`.

The local exercise intentionally corrupted the post-deployment revision, proved containment in
`ROLLBACK_REQUIRED`, restored only the recorded pre-deployment snapshot, independently verified
the complete marker, preserved a valid audit chain and recovery duration, and entered the
terminal `ROLLED_BACK` state. Ambiguous recovery remains non-replayable and contained. This closes
Phase 4 locally without enabling hosted or production deployment.

## Run locally

Create an isolated Python environment and install the project:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run the entirely in-memory deterministic demonstration:

```bash
.venv/bin/control-plane demo
```

For the API and worker with PostgreSQL:

```bash
docker compose up -d postgres
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/control-plane-api
```

Run `.venv/bin/control-plane-worker` in another terminal. The API listens only on `127.0.0.1:8000`; interactive OpenAPI documentation is at `/docs`. The default `dev-operator` header identity is a local-development convenience, not production authentication.

For a shared deployment, build the separate non-root API and worker image targets and follow [the Phase 3 deployment boundary](deployment/README.md). Production Compose requires immutable image digests, OIDC, TLS ingress, hosted database configuration, and separately injected credentials; it does not ship usable defaults.

To enable isolated work against an approved local repository, copy `repository-registry.example.json` to the ignored `repository-registry.json`, use a canonical repository root with an existing commit, set its writable paths, and configure `CONTROL_PLANE_REPOSITORY_REGISTRY_FILE=repository-registry.json`. Workflow clients submit only the matching opaque scope ID.

To activate the local Windsurf MCP boundary, first configure the repository registry, apply migrations, generate a random bearer token of at least 32 characters, and set the `CONTROL_PLANE_WINDSURF_MCP_*` variables shown in `.env.example`. Then run:

```bash
.venv/bin/control-plane-mcp
```

The Streamable HTTP endpoint is `http://127.0.0.1:8010/mcp`. It exposes only explicit implementation claim, heartbeat, and Git-bound evidence submission tools. This development token is not a substitute for OIDC on a shared or remote service. See [OIDC identity and merge confirmation](docs/identity-and-merge-confirmation.md) for production API identity and the human-merge evidence flow.

Run the verification suite:

```bash
.venv/bin/ruff check src tests migrations
.venv/bin/mypy src
.venv/bin/pytest -m 'not postgres'
CONTROL_PLANE_TEST_DATABASE_URL='postgresql+psycopg://control_plane:control_plane@localhost:55432/control_plane' .venv/bin/pytest -m postgres
.venv/bin/pytest -m docker
```

## Documentation map

- [System architecture](docs/architecture.md)
- [Workflow and state machine](docs/workflow.md)
- [Agent security and permissions](docs/agent-security-model.md)
- [Provider interface](docs/provider-interface.md)
- [Provider activation runbook](docs/provider-activation.md)
- [GitHub CI evidence integration](docs/github-ci-integration.md)
- [GitHub App activation record](docs/github-app-activation.md)
- [OIDC identity and authoritative merge confirmation](docs/identity-and-merge-confirmation.md)
- [Phase 3 deployment boundary](deployment/README.md)
- [Phase 3 acceptance record](docs/phase-3-acceptance.md)
- [Phase 4 controlled deployment design](docs/phase-4-deployment-design.md)
- [Phase 4.1 acceptance record](docs/phase-4-1-acceptance.md)
- [Phase 4.2 acceptance record](docs/phase-4-2-acceptance.md)
- [Phase 4.3 acceptance record](docs/phase-4-3-acceptance.md)
- [Phase 4.4 acceptance record](docs/phase-4-4-acceptance.md)
- [Proposed open-source deployment profile](docs/open-source-deployment-profile.md)
- [OpenTofu saved-plan validation](docs/opentofu-plan-validation.md)
- [Local OpenTofu and k3d activation evidence](docs/local-toolchain-activation.md)
- [Ephemeral k3d identity-boundary validation](docs/k3d-identity-validation.md)
- [Onboarding other projects](docs/project-onboarding.md)
- [Proposed data model](docs/data-model.md)
- [Threat model](docs/threat-model.md)
- [Failure modes and recovery](docs/failure-recovery.md)
- [Testing strategy](docs/testing-strategy.md)
- [Observability](docs/observability.md)
- [Implementation roadmap and approval decisions](docs/roadmap.md)
- [Phase 2 execution security](docs/phase-2-execution-security.md)
- [Claude, Devin, and Windsurf interoperability](docs/interoperability.md)
- [Capability routing and adversarial-input strategy](docs/capability-routing.md)
- [Controlled continuous improvement](docs/continuous-improvement.md)
- [Architecture Decision Records](docs/adr/)
- [Repository activation and public-launch gates](docs/activation-preflight.md)
- [Licensing and third-party boundary](docs/licensing.md)
- [Security policy](SECURITY.md)
- [Contribution guide](CONTRIBUTING.md)

## License status

Licensed under the [Apache License 2.0](LICENSE). This permissive license includes an explicit
patent grant and requires preservation of applicable copyright, patent, trademark, and
attribution notices when the work is redistributed. Provider services, models, dependencies,
operator projects, and model outputs are separate from the Eldridge license; see the
[licensing boundary](docs/licensing.md).

## Guiding invariants

1. No model output directly changes authoritative workflow state.
2. Every transition and privileged action passes deterministic policy checks.
3. Agents cannot approve their own work or manufacture human approval.
4. Command execution requires Phase 2 worktree and container boundaries; mock execution remains non-command-running.
5. Git is authoritative for project artifacts; PostgreSQL is authoritative for orchestration state and audit evidence.
6. Secrets are referenced by opaque handles and resolved only for an authorized execution boundary.
7. Model consensus is advisory. Tests, policy, verified evidence, and human judgment have higher authority.
