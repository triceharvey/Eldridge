# Phase 5.9 OCI Always Free Canary Plan Acceptance

## Outcome

Phase 5.9 selects OCI Always Free as Eldridge's first hosted canary planning target and adds a real
OCI provider configuration without creating an account, credential, plan against OCI, or resource.
The profile declares a bounded Arm64 VM, network edge, and private versioned backup bucket. Its
OpenTofu mock-provider test proves the resource graph and safety assertions without contacting OCI.

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
- No state, saved plan, OCI credential, API request, or cloud resource was produced.

## Remaining Activation Work

The owner must still create or select an OCI tenancy, choose its home region, confirm A1 capacity,
select an eligible Arm64 image, establish the canary compartment and credential boundary, choose a
DNS name, and approve a saved plan after inspecting its exact cost. A later activation slice must
publish multi-architecture images, deploy the application, and collect all eight production
readiness observations. This acceptance is not hosted-production evidence.
