# Proposed Open-Source Deployment Profile

## Status and boundary

OpenTofu was approved as Eldridge's provider-neutral infrastructure layer on 2026-09-08. The
owner approved a USD 0 local Docker/k3d implementation target on 2026-09-08. This approval does
not authorize a hosting account, hardware purchase, public endpoint, credential, paid
infrastructure change, or real deployment.

Open source does not mean infrastructure is free. A VPS, physical server, domain, network,
off-site backup, and operator time can still create cost. Replacing managed services also
transfers patching, availability, backup, identity, capacity, and recovery duties to the
operator.

## Managed-to-open-source mapping

| Managed Azure capability | Proposed open-source equivalent | Operational responsibility transferred to Eldridge |
|---|---|---|
| Azure Container Apps | K3s with containerd | Cluster upgrades, node hardening, scheduling, capacity, and availability |
| Azure Container Registry | Harbor or CNCF Distribution registry | Storage, TLS, access control, scanning integration, and garbage collection |
| Azure Database for PostgreSQL | PostgreSQL managed by CloudNativePG | Upgrades, replication, backups, restore testing, and failover |
| Azure Managed Identity | SPIFFE/SPIRE plus projected Kubernetes service-account tokens | Trust-domain operation, attestation policy, rotation, and federation |
| Azure Key Vault | OpenBao | Unsealing, key custody, policy, backup, rotation, and recovery |
| Azure Monitor and Log Analytics | OpenTelemetry, Prometheus, Grafana, and Loki | Retention, alert delivery, storage capacity, and monitoring availability |
| Azure Application Gateway / ingress | Caddy or Traefik | TLS policy, certificate recovery, routing, and denial-of-service controls |
| Azure Policy | Kyverno or OPA Gatekeeper | Policy authorship, testing, distribution, exceptions, and upgrades |
| Azure Backup | pgBackRest plus Restic to a separate failure domain | Backup storage, encryption keys, schedules, integrity checks, and restore drills |
| Azure Cost Management | OpenCost plus host/provider budget alerts | Cost-model accuracy and enforcement outside the cluster |

The initial profile remains a local non-production Docker/k3d environment with PostgreSQL,
existing Prometheus metrics, and no public control-plane endpoint. Docker Compose remains the
lowest-cost baseline; k3d is used only for Kubernetes-specific evidence. Additional components
must be justified by a demonstrated need rather than installed as a platform bundle.

## Cost boundary

- Current Phase 4.2 infrastructure ceiling: USD 0 incremental spend.
- Local Docker, PostgreSQL, fake credentials, OpenTofu validation, and optional ephemeral k3d
  use the owner's existing machine.
- A later temporary hosted exercise may spend no more than approximately USD 5 total. It is a
  separately planned and approved validation event, not a recurring monthly authorization.
- The provider resource must be destroyed after evidence capture; stopping a billed VM is not
  sufficient unless the provider explicitly stops billing it.
- Azure managed services and persistent hosted K3s remain alternatives for future measured
  needs.

## OpenTofu execution boundary

1. Pin the OpenTofu CLI, providers, and modules; retain the dependency lock file.
2. Validate configuration and generate a saved plan without mutation.
3. Convert the plan to bounded JSON evidence and reject unknown, destructive, out-of-scope, or
   over-budget changes.
4. Bind the environment, repository revision, configuration digest, provider locks, saved-plan
   digest, policy version, and expiry into a human `DEPLOY` approval.
5. Obtain short-lived workload identity only after approval is consumed.
6. Apply only the saved plan artifact; never regenerate a plan during apply.
7. Observe the target independently, verify health and revision, and retain evidence.
8. Treat transport ambiguity as `UNKNOWN`; never automatically apply again.

Model output may propose a change for review, but cannot widen providers, modules, resource
scopes, identity permissions, cost limits, or execute `tofu apply`.

## Decision still required

Before implementing the first hosted adapter, the owner must choose between:

- a managed Azure non-production target with OpenTofu and Azure managed identity; or
- the proposed open-source K3s target with OpenTofu and SPIFFE/SPIRE.

That decision must also approve the host, total cost ceiling, trust policy, backup target,
verification probes, and rollback policy.
