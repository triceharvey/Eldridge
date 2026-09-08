# Onboarding Other Projects

The control plane is designed to orchestrate multiple independent projects. It is a service and policy layer above project repositories, not code that must be copied into every repository.

## Recommended operating model

Run one controlled API/worker deployment with PostgreSQL, then register each approved project by scope. Each workflow names a scope such as `owner/api-service`; it never supplies an arbitrary filesystem path. The operator-owned registry resolves that scope to the local Git repository root, base revision, and writable paths.

```json
[
  {
    "scope_id": "owner/api-service",
    "path": "/srv/projects/api-service",
    "base_revision": "main",
    "writable_paths": ["src", "tests", "docs"]
  },
  {
    "scope_id": "owner/infrastructure",
    "path": "/srv/projects/infrastructure",
    "base_revision": "main",
    "writable_paths": ["modules", "environments/dev", "tests", "docs"]
  }
]
```

Set `CONTROL_PLANE_REPOSITORY_REGISTRY_FILE` to this ignored operator file. On startup, every entry must resolve to a real Git root with a valid base revision, safe local Git configuration, and valid relative writable paths. Duplicate, missing, nested, or executable-filter configurations fail closed.

For GitHub-backed projects, add a matching entry to the ignored `github-app-policy.json`. That policy independently fixes the repository full name, protected base branch, required checks, review count, and branch-protection requirements. A project is not GitHub-enabled merely because it appears in the local repository registry.

Submit work through the common API:

```json
{
  "title": "Add bounded retry handling",
  "description": "Implement and validate the approved change.",
  "idempotency_key": "api-service-retry-2026-09-07",
  "complexity": "STANDARD",
  "risk": "MEDIUM",
  "data_classification": "INTERNAL",
  "repository_scope": "owner/api-service"
}
```

The same workflow engine then selects eligible AI providers by capability, creates a dedicated branch/worktree, executes typed tools in the sandbox, validates evidence, requires independent review, and stops at human gates. Repository permissions, paths, egress, risk, classification, provider credentials, CI policy, and approvals remain separate controls.

## Isolation choices

- **Central control plane:** recommended for a portfolio or engineering organization. One policy and audit system coordinates many repositories while each task keeps a separate worktree and container.
- **Dedicated instance:** appropriate for a project with stronger confidentiality, residency, customer, or regulatory boundaries. It uses its own database, provider policy, secrets, and repository registry.

Do not place unrelated trust zones in one instance merely for convenience. A restricted client repository should not inherit the egress or credentials used by a public portfolio project.

## Current availability

The package currently runs from this workspace using `.venv/bin/control-plane-api`, `.venv/bin/control-plane-worker`, and `.venv/bin/control-plane-mcp`. Separate non-root API/worker images, migrations, internal networking, OIDC, metrics, dashboard, and automatic-TLS ingress are packaged, but this local repository still has no initial Git commit or remote and no shared environment has been activated. Other local repositories can be onboarded now through the registry. Remote team use should wait for the operator-specific activation evidence listed in `deployment/README.md`.

Provider performance evidence is currently partitioned by provider, model, profile version, and task capability. Before cross-project automatic optimization, Phase 3/5 should add repository and evaluation-suite cohorts so success on one technology stack does not create unjustified trust on another.
