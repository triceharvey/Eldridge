# ADR-0002: PostgreSQL-Backed Durable Workflow and Leasing

- Status: Accepted
- Date: 2026-09-06

## Context

Workflow state, queued tasks, retries, approvals, and audit events require durability and atomic invariants. Adding a broker would create a second consistency domain before throughput requires it.

## Decision

Use PostgreSQL for relational state, transactional task leasing, append-only events, idempotency, and an outbox. Workers claim ready tasks with row-level locking and time-limited leases.

## Alternatives

- Redis provides convenient queue primitives but creates state synchronization and additional operations.
- Kafka provides durable event streams but is disproportionate for the MVP.
- SQLite is useful for narrow tests but does not represent the intended concurrency behavior.

## Consequences

State changes and emitted events can commit atomically. Throughput is bounded by database polling/locking, which is acceptable until measurement says otherwise. Concurrency tests must run against PostgreSQL.

## Failure behavior

Workers stop leasing during database loss. Expired leases are reconciled; non-idempotent ambiguous actions require operator review. The outbox permits later reliable integration delivery.
