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
- Recurring and temporary hosted cost ceilings must both equal USD 0.
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
- the then-committed USD 0 monthly and USD 5 temporary-exercise ceilings.

Cloud Shell supplied Terraform 1.5.7, below the repository's required 1.9+ runtime. The evidence
plan therefore used a version-only relaxation in a disposable clone. That deviation did not alter
the committed source, but it makes the saved plan ineligible for apply. OpenTofu remains the only
authorized execution engine.

A second planning exercise used the official OpenTofu 1.12.6 Linux Arm64 release after verifying
its published SHA-256 checksum. OpenTofu rejected OCI's preinstalled provider binary because it did
not match the committed lock file. It then installed the signed, checksum-pinned OCI provider
8.29.0 from the OpenTofu registry and initialized without changing the source constraint or lock
file.

The compatible plan was generated from merged `main` revision
`0ee1ee432a2886b80f42981f02beb11961fb6e2e`. Its sanitized JSON inspection reproduced the eight
create actions, zero destructive actions, network boundary, private backup boundary, and capacity
and cost assertions above. The saved 9,698-byte plan is identified by SHA-256
`bb120bfb1f91ec364b7a75e2390459186979461dae67ff8e1c0a1178c4946786`. No instance, VCN, subnet,
gateway, route table, security list, volume, or bucket was created.

## Superseded Plan Boundary

On 2026-09-27, the owner reduced the temporary hosted ceiling from USD 5 to USD 0 and explicitly
deferred DNS and production TLS until after infrastructure validation. That decision makes plan
`bb120bfb1f91ec364b7a75e2390459186979461dae67ff8e1c0a1178c4946786` obsolete and ineligible for
apply even though it created no resources.

## Zero-Dollar Replacement Plan

A fresh checkout of merged `main` revision
`ae38fe034d2e890231b364736b2b3f479b80ea2b` produced a replacement plan with OpenTofu 1.12.6 and
the signed OCI provider 8.29.0. OpenTofu validation and all three mocked plan tests passed before
the authenticated plan. Sanitized JSON inspection established:

- eight create actions and zero destructive actions;
- both monthly and temporary hosted ceilings fixed at USD 0;
- one `VM.Standard.A1.Flex` instance fixed at 2 OCPUs and 12 GB memory;
- one 50 GB boot volume with legacy IMDS endpoints disabled;
- public TCP ingress limited to ports 80 and 443; and
- a `NoPublicAccess`, version-enabled Object Storage backup bucket.

The saved 9,707-byte plan is identified by SHA-256
`b0b44b594630a9e754e6cb1fd3f5ad4cfcafd3529a7eb4395d6be8f0149417da`. It remains only in the
authenticated Cloud Shell workspace.

## First Controlled Apply Attempt

The owner approved that exact fingerprint under the USD 0 maximum on 2026-09-27. Immediately
before apply, the fingerprint, revision, action counts, zero-dollar inputs, A1 shape visibility,
and `AVAILABLE` image state were revalidated. OpenTofu created and recorded the safety marker,
VCN, internet gateway, route table, security list, public subnet, and private versioned bucket.
OCI then rejected the instance launch with `500-InternalError: Out of host capacity`.

Reconciliation confirmed those seven objects in OpenTofu state and no instance in the canary
compartment. No automatic retry, configuration change, DNS record, TLS setup, application
deployment, or teardown occurred. A refreshed recovery plan contains only the original
`oci_core_instance.canary` create action, no change or destructive action, and the same USD 0
budgets, revision, image, and 2 OCPU/12 GB/50 GB capacity boundary. Its 15,288-byte saved plan is
identified by SHA-256
`c14fdf76fbfa12062dc8e6adba6e56b4af95a99b81d2943a227305b521d86f65`.

## Controlled Capacity Retry

The owner approved that exact recovery-plan fingerprint under the USD 0 maximum on 2026-09-27.
Preflight revalidated the fingerprint, single create action, zero destructive actions, zero-dollar
inputs, source revision, A1 shape visibility, and `AVAILABLE` image state. The single apply again
failed at `oci_core_instance.canary` with `500-InternalError: Out of host capacity`.

Reconciliation confirmed seven objects still tracked in OpenTofu state, zero instances, and zero
boot volumes in the canary compartment. No further retry, configuration change, teardown, DNS,
TLS, or application deployment occurred.

## Controlled Teardown

The owner separately approved the destroy-only saved plan with SHA-256
`5157cf37a7adb1b32b7cfc1758586edcaca727d74f823b2bf7ed6a31a2f1721b` on 2026-09-27. Action-time
preflight confirmed seven deletes, zero creates, zero updates, seven tracked objects, an empty
backup bucket, and no instance. OpenTofu reported `0 added, 0 changed, 7 destroyed`.

Independent verification then reported zero OpenTofu state objects, VCNs, buckets, instances, and
boot volumes in the canary compartment. The dedicated IAM compartment was outside the plan and is
intentionally retained. No DNS, TLS, or application workload existed to remove.

## Remaining Activation Work

The approved one-time recovery attempt and controlled teardown are complete. Any future hosted
attempt requires a new plan and separate approval; it cannot reuse either consumed approval.
Always Free eligibility and capacity must be reconfirmed before another plan is proposed. DNS
selection, production TLS, multi-architecture image publication, application deployment, and all
eight production-readiness observations remain later activation work. This acceptance is not
hosted-production evidence.
