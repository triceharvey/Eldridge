# Architecture Decision Records

ADRs capture decisions that materially shape security, reliability, or evolution. ADRs 0001–0006 were accepted after the project owner approved the Phase 0 roadmap on 2026-09-06. ADR 0007 records the approved local Phase 2 interoperability boundary.

- [ADR-0001: Modular monolith for the MVP](0001-modular-monolith.md)
- [ADR-0002: PostgreSQL-backed durable workflow and leasing](0002-postgresql-workflow-and-leasing.md)
- [ADR-0003: Task-scoped capability authorization](0003-task-scoped-capabilities.md)
- [ADR-0004: Worktree plus ephemeral-container isolation](0004-isolated-execution.md)
- [ADR-0005: Scoped human approvals](0005-scoped-human-approvals.md)
- [ADR-0006: Append-only audit events](0006-append-only-audit.md)
- [ADR-0007: Narrow Windsurf MCP boundary](0007-narrow-windsurf-mcp-boundary.md)
- [ADR-0008: Controlled deployment boundary](0008-controlled-deployment-boundary.md)
- [ADR-0009: OpenTofu infrastructure layer](0009-opentofu-infrastructure-layer.md)
- [ADR-0010: Zero-cost local Phase 4 target](0010-zero-cost-local-phase-4-target.md)
- [ADR-0011: Local Kubernetes TokenRequest broker](0011-local-kubernetes-token-request-broker.md)
- [ADR-0012: Bounded local k3d deployment adapter](0012-bounded-local-k3d-deployment-adapter.md)
- [ADR-0013: Controlled local recovery](0013-controlled-local-recovery.md)

Each ADR records context, decision, alternatives, consequences, and failure behavior. Reversals are documented by a superseding ADR rather than editing history to hide the prior decision.
