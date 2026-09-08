# ADR-0001: Modular Monolith for the MVP

- Status: Accepted
- Date: 2026-09-06

## Context

The platform needs strong logical boundaries but has not yet demonstrated workload, team ownership, or independent scaling requirements that justify distributed services.

## Decision

Build one Python package with explicit modules and two runtime entry points: API and worker. Keep workflow, authorization, providers, execution, approvals, persistence, and audit behind internal interfaces.

## Alternatives

- Multiple microservices would improve independent deployment but add network policy, service identity, tracing, partial failure, and transaction complexity immediately.
- One synchronous API process would be simpler but couples request latency to agent work and handles restarts poorly.

## Consequences

The MVP remains understandable and transaction boundaries remain strong. API and worker can scale separately while sharing code. Extraction later requires disciplined module boundaries and contract tests.

## Failure behavior

API loss prevents new commands but not durable state. Worker loss leaves leases to expire and reconcile. A database outage makes authoritative operations fail closed.
