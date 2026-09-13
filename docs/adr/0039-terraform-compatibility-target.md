# ADR 0039: Add Terraform as a Compatibility and Learning Target

- Status: Accepted
- Date: 2026-09-13
- Accepted by: project owner on 2026-09-13

## Context

OpenTofu is Eldridge's approved provider-neutral infrastructure execution layer. The owner also
needs current HashiCorp Terraform experience for future projects and employment. Terraform CLI and
the hosted HCP Terraform service are distinct: installing and running the CLI locally does not
consume HCP managed-resource allowance or trial credit.

## Decision

Retain OpenTofu as the only authorized Eldridge infrastructure execution engine. Add a pinned local
Terraform CLI as a compatibility and learning target. A built-in-only fixture must initialize,
validate, plan, and produce equivalent bounded change evidence under both engines in isolated
temporary directories. The compatibility path may not apply a plan, contact HCP Terraform, use a
remote backend, download a provider, request credentials, or create infrastructure.

HCP Terraform remains unactivated. Any later free-plan exercise requires a separate decision that
names the organization, workspace, repository revision, credential scope, resource ceiling,
cleanup procedure, and confirmation that the organization is still on the free plan. Trial credit
is not treated as a budget and no payment method is authorized.

## Consequences

- Eldridge demonstrates both open-source OpenTofu practice and employer-relevant Terraform
  compatibility without weakening the zero-dollar boundary.
- Plans, working directories, caches, locks, and state are never shared across engines.
- Compatibility is proven by deterministic structure, not by assuming the tools are interchangeable.
- Terraform drift from the shared HCL subset fails the compatibility test before any execution
  authority can be considered.
- A future paid HCP plan or cloud bill cannot be inferred from this decision.

## Rejected Alternatives

- **Replace OpenTofu with Terraform:** unnecessary vendor dependency and contrary to ADR 0009.
- **Treat the HCP free plan as expiring credits:** the free plan is a managed-resource ceiling, not a
  consumable balance; trial credits are a different billing mechanism.
- **Share saved plans or state between engines:** plan files are engine- and version-specific security
  artifacts.
- **Prove compatibility by applying infrastructure:** adds cost and destructive-state risk without
  improving this local contract test.
