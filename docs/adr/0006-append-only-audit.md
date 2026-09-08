# ADR-0006: Append-Only Audit Events

- Status: Accepted
- Date: 2026-09-06

## Context

The platform needs traceable actions without deploying an event platform prematurely. Normal logs are mutable, lossy, and may not commit with state.

## Decision

Write schema-versioned audit events in PostgreSQL in the same transaction as authoritative changes. Application roles can insert and read but not update/delete. Events include sequence numbers and a per-workflow hash chain. Export to immutable storage is deferred.

## Alternatives

- Logs alone do not provide transactional or retention guarantees.
- Kafka or a SIEM-first design adds infrastructure and still requires atomic publication handling.
- A database ledger extension may improve tamper evidence but increases platform coupling.

## Consequences

The MVP gains queryable and consistent history. Hash chaining detects many modifications but cannot defeat a database administrator who can rewrite the chain and application state; documentation must not claim otherwise.

## Failure behavior

If an audit event cannot be written, the associated authoritative action rolls back and fails closed. Export failures retry through the outbox without blocking already committed internal history.
