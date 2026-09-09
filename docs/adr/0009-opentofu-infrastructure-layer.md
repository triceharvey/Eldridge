# ADR 0009: OpenTofu Infrastructure Layer

- Status: Accepted
- Date: 2026-09-08
- Accepted by: project owner on 2026-09-08

## Context

Phase 4 needs reproducible infrastructure plans without coupling Eldridge's authorization,
audit, and recovery controls to one hosting provider. Infrastructure changes must remain
reviewable, revision-bound, and portable across managed-cloud and self-hosted targets.

## Decision

Use OpenTofu as Eldridge's provider-neutral infrastructure-as-code layer. Eldridge will accept
only pinned OpenTofu and provider versions, generate a saved plan, bind approval to the plan
artifact and its digest, and apply only that exact approved plan through a typed adapter. The
adapter will not expose a generic shell or accept model-generated configuration at execution
time.

OpenTofu state, plan files, dependency locks, provider packages, and encryption configuration
are security-sensitive artifacts. Production design requires remote state locking, encryption,
least-privilege short-lived identity, separate state per environment, durable audit evidence,
and a tested recovery procedure.

This decision selects the infrastructure layer only. It does not select a hosting provider,
approve the proposed open-source deployment profile, create infrastructure, authorize cost, or
permit a real deployment.

## Consequences

- The same control-plane contract can target Azure, another cloud, K3s, or an approved
  self-hosted platform through constrained providers and modules.
- OpenTofu's saved-plan workflow aligns with Eldridge's immutable plan and exact-approval
  boundary.
- Eldridge must validate plan JSON and reject destructive, out-of-scope, untyped, or
  unbudgeted resource changes before approval.
- Provider and module versions must be constrained and dependency locks retained.
- State confidentiality, locking, backup, key recovery, and drift reconciliation become
  explicit operational responsibilities.

## Rejected alternatives

- **Provider-specific scripts:** difficult to review consistently and encourage platform
  coupling.
- **Model-generated shell commands:** cannot provide a stable authorization boundary.
- **Apply the current configuration after approval:** configuration or provider drift could
  apply something different from the reviewed plan.
- **Select one proprietary control plane as the abstraction:** would make portability depend on
  that vendor rather than Eldridge's provider-neutral contract.
