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

The first hosted validation exercise also has a USD 0 maximum. If any selected resource cannot be
confirmed as free before apply, the exercise must stop rather than consume trial credits or incur
a charge. Any future nonzero ceiling requires a new architecture decision plus approval of the
provider, exact resource plan, expected duration, identity policy, and destruction procedure.
Azure managed services and persistent hosted K3s remain alternatives for future measured need.

## Consequences

- Current development and most Phase 4 validation add no infrastructure bill.
- Local evidence does not establish public availability, cloud IAM behavior, or managed-service
  recovery.
- k3d is optional; Docker Compose remains the smaller baseline when Kubernetes adds no evidence.
- A hosted exercise must reject a plan containing any expected charge and remove temporary
  resources after evidence capture.
- Cost alternatives are reviewed at Phase 4 exits, but review never authorizes spending.

## Rejected alternatives

- **Begin with Azure managed services:** simpler operations do not justify a persistent bill at
  the current validation stage.
- **Begin with persistent hosted K3s:** transfers operations to the owner before continuous
  availability is required.
- **Treat free tiers as permanent architecture:** terms, quotas, and availability can change.
- **Install the full open-source platform immediately:** unused components increase resource
  consumption and maintenance without producing necessary evidence.
