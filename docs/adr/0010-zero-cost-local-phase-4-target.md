# ADR 0010: Zero-Cost Local Phase 4 Target

- Status: Accepted
- Date: 2026-09-08
- Accepted by: project owner on 2026-09-08

## Context

Phase 4 needs realistic deployment and identity evidence, but a continuously billed managed
environment is not necessary to validate the next controls. Cost is a first-class operational
constraint. The existing machine, Docker runtime, PostgreSQL packaging, and CI can exercise the
majority of the design without creating external infrastructure.

## Decision

Use local Docker as the default Phase 4.2 target with optional ephemeral k3d only for
Kubernetes-specific evidence. Set the incremental infrastructure ceiling to USD 0. Use
OpenTofu for provider-neutral planning, a deny-by-default credential broker, and a
metadata-only fake credential broker before qualifying a local workload-identity mechanism.

A later temporary hosted validation exercise may have a maximum total budget of approximately
USD 5. That ceiling is not recurring authorization: the provider, exact resource plan, expected
duration, identity policy, and destruction procedure require a separate execution decision.
Azure managed services and persistent hosted K3s remain alternatives for future measured need.

## Consequences

- Current development and most Phase 4 validation add no infrastructure bill.
- Local evidence does not establish public availability, cloud IAM behavior, or managed-service
  recovery.
- k3d is optional; Docker Compose remains the smaller baseline when Kubernetes adds no evidence.
- A hosted exercise must destroy billable resources after evidence capture and verify that
  billing has stopped.
- Cost alternatives are reviewed at Phase 4 exits, but review never authorizes spending.

## Rejected alternatives

- **Begin with Azure managed services:** simpler operations do not justify a persistent bill at
  the current validation stage.
- **Begin with persistent hosted K3s:** transfers operations to the owner before continuous
  availability is required.
- **Treat free tiers as permanent architecture:** terms, quotas, and availability can change.
- **Install the full open-source platform immediately:** unused components increase resource
  consumption and maintenance without producing necessary evidence.
