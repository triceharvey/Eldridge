# Phase 5.9 OCI Always Free Canary Plan Acceptance

## Outcome

Phase 5.9 selects OCI Always Free as Eldridge's first hosted canary planning target and adds a real
OCI provider configuration. The profile declares a bounded Arm64 VM, network edge, and private
versioned backup bucket. Its OpenTofu mock-provider test proves the resource graph and safety
assertions without contacting OCI.

On 2026-09-26, the owner activated the tenancy in `us-sanjose-1`, created the dedicated
`eldridge-canary` IAM compartment, and authorized an authenticated planning exercise through OCI
Cloud Shell. The exercise produced a saved, provider-backed plan for inspection but did not apply
it or create workload resources.

## Enforced Boundary

- OpenTofu remains the only authorized infrastructure execution engine.
- Oracle's OCI provider is pinned to version 8.29.0 with a committed OpenTofu lock file.
- Recurring cost must equal USD 0; the temporary exercise may not exceed USD 5.
- Planning fails until home-region and Always Free eligibility are explicitly confirmed.
- Compute is fixed at 2 OCPUs, 12 GB memory, one 50 GB boot volume, and the A1 Flex shape.
- Only public TCP ports 80 and 443 are admitted.
- The backup bucket is private and versioned.
- The example contains placeholders and both operator confirmations default to false.
- Cloud-init installs Docker and records the exact revision, but deploys no application or secret.

## Verification

- OpenTofu 1.12.6 initialized the pinned OCI provider and validated the configuration.
- OpenTofu's mocked provider executed two plan tests: the accepted Always Free envelope and the
  rejection of unconfirmed eligibility.
- Terraform 1.16.2 independently initialized, validated, and passed both mocked plan tests from a
  separate temporary directory.
- The repository suite passed with 372 tests, one intentional destructive-k3d skip, and seven
  environment-specific deselections; the Docker-backed PostgreSQL integration passed separately.
- Ruff format/check, mypy, and the dependency vulnerability audit passed.
- The original local and mock-provider verification produced no state, saved plan, OCI credential,
  API request, or cloud resource.

## Provider-backed Planning Evidence

OCI Cloud Shell inherited the signed-in operator identity and confirmed that San Jose is the
tenancy home region. The plan used OCI provider 8.29.0 and the selected San Jose availability
domain and Arm64 Ubuntu image. A sanitized JSON inspection established:

- eight create actions and zero destructive actions;
- one `VM.Standard.A1.Flex` instance fixed at 2 OCPUs and 12 GB memory;
- one 50 GB boot volume with legacy IMDS endpoints disabled;
- public TCP ingress limited to ports 80 and 443;
- a `NoPublicAccess`, version-enabled Object Storage backup bucket; and
- the committed USD 0 monthly and USD 5 temporary-exercise ceilings.

Cloud Shell supplied Terraform 1.5.7, below the repository's required 1.9+ runtime. The evidence
plan therefore used a version-only relaxation in a disposable clone. That deviation did not alter
the committed source, but it makes the saved plan ineligible for apply. OpenTofu remains the only
authorized execution engine, and any later apply must use a fresh plan from a compatible runtime.
No instance, VCN, subnet, gateway, route table, security list, volume, or bucket was created.

## Remaining Activation Work

The owner must still produce and inspect a fresh compatible OpenTofu plan, confirm its exact cost,
choose a DNS name, and separately approve the exact saved plan before any apply. Capacity must be
reconfirmed at apply time. A later activation slice must publish multi-architecture images, deploy
the application, and collect all eight production-readiness observations. This acceptance is not
hosted-production evidence.
