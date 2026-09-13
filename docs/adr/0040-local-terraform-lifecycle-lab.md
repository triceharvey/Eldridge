# ADR 0040: Permit a Provider-Free Local Terraform Lifecycle Lab

- Status: Accepted
- Date: 2026-09-13
- Accepted by: project owner on 2026-09-13

## Context

ADR 0039 permits Terraform planning as a compatibility target but deliberately forbids apply. A
useful professional Terraform exercise also needs module composition, state, workspaces, change
detection, apply, output inspection, and destroy. Those concepts can be exercised without external
infrastructure by using only Terraform's built-in `terraform_data` resource.

## Decision

Permit a separate career-lab test to apply and destroy the built-in `terraform_data` resource in a
pytest-owned temporary directory. The lab must use a zero-dollar validated variable, no provider
plugin, no backend configuration, no credential, no HCP integration, and no network API. It must
create independent default and staging workspace states, detect an exact revision change through
Terraform's detailed plan exit code, destroy both state objects, and verify both resource lists are
empty before pytest removes the temporary directory.

This decision does not expand Eldridge's deployment adapter authority. OpenTofu remains the only
approved infrastructure execution engine, and the existing no-apply compatibility fixture remains
unchanged.

## Consequences

- The portfolio demonstrates the core Terraform lifecycle using real commands and inspectable state.
- State is treated as sensitive, kept out of Git, and isolated by temporary working directory and
  workspace.
- The exercise proves CLI workspace mechanics but explicitly does not treat workspaces as sufficient
  production security isolation.
- Any provider, remote backend, cloud resource, HCP workspace, or payment mechanism still requires a
  separate decision.

## Rejected Alternatives

- **Use a free cloud resource:** free-tier labels do not eliminate billing, credential, or cleanup
  risk.
- **Use Docker or Kubernetes providers:** provider downloads and local target mutations are not
  needed to learn the lifecycle.
- **Change ADR 0039's compatibility fixture to apply:** mixing compatibility and lifecycle evidence
  would weaken the clearer no-apply boundary.
