# Phase 3 Deployment Boundary

The production package separates the public API, trusted execution worker, one-shot database migration, and TLS ingress. It intentionally does not contain real identity, GitHub, provider, repository, database, or monitoring credentials.

## Build and publish

Build the targets, scan them with the organization's approved scanner, push them to an approved registry, and record the returned immutable digests:

```bash
docker build --target api -t registry.example.com/control-plane-api:0.3.0 .
docker build --target worker -t registry.example.com/control-plane-worker:0.3.0 .
docker push registry.example.com/control-plane-api:0.3.0
docker push registry.example.com/control-plane-worker:0.3.0
```

Production Compose requires digest-pinned API, worker, and Caddy image references. A mutable tag is not an acceptable release reference.

## Operator inputs

1. Copy `deployment/.env.production.example` to the ignored repository-root `.env.production` and inject secrets through the deployment platform rather than source control.
2. Place the four real policy files plus the repository registry under the ignored `deployment/policies/` directory with operator-only permissions.
3. Mount only approved repositories below `CONTROL_PLANE_PROJECTS_ROOT`; registry paths inside the worker must use `/projects/...`. Set `DOCKER_GID` to the group owning the dedicated host's container-engine socket so the non-root worker can connect without becoming UID 0.
4. Configure public DNS for `CONTROL_PLANE_DOMAIN`, permit inbound TCP 80/443 and UDP 443 as appropriate, and set the ACME account email.
5. Run `docker compose -f compose.production.yaml config` and inspect the fully rendered deployment before `up`.

Caddy terminates TLS and keeps certificate data in a dedicated volume. The API and worker live only on an internal network, and the API port must never be published directly in production. The Compose command trusts proxy headers from that isolated network because ingress is its only HTTP peer. `/metrics` remains protected by its separate monitoring bearer credential, while `/dashboard` and `/operations/summary` require the same OIDC and role authorization as audit queries.

## Worker trust boundary

The worker is a trusted control-plane component and requires access to the container engine to launch restricted task sandboxes. Run it on a dedicated worker host. Never mount the engine socket, control-plane policies, credentials, or project roots into a model task sandbox. The sandbox executor independently applies a non-root user, read-only root filesystem, dropped capabilities, no-new-privileges, bounded resources, scoped mounts, and network denial.

## Activation evidence

Before shared use, retain the rendered Compose configuration, image digests and scan reports, migration output, OIDC negative/positive tests, GitHub App permission screenshot, protected-branch rules, webhook delivery evidence, Prometheus scrape evidence, TLS scan, backup/restore result, and one full human-approved PR workflow. External activation is not complete until those environment-specific artifacts exist.
