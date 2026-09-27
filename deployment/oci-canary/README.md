# OCI Always Free Hosted Canary

This profile is Eldridge's selected lowest-cost hosted canary target. It declares one Arm64
`VM.Standard.A1.Flex` instance with 2 OCPUs and 12 GB memory, a 50 GB boot volume, a VCN and public
edge subnet, and a private versioned Object Storage backup bucket. Only TCP 80 and 443 are admitted;
SSH, PostgreSQL, and the control-plane API port are not publicly exposed.

The profile is plan-only until the owner separately approves an exact saved plan. It cannot prove
that a tenancy, region, image, or resource is eligible for OCI Always Free pricing. Before any real
plan, the operator must confirm both the tenancy home region and the **Always Free eligible** label
for every selected resource in the OCI console. Capacity is not guaranteed, and Oracle may reclaim
idle Always Free compute.

## Zero-cost local proof

The test replaces OCI with OpenTofu's mock provider, so it makes no API calls and creates no
resources:

```bash
tofu init -backend=false
tofu test
```

Terraform remains a compatibility and learning target; OpenTofu remains the authorized execution
engine. Neither `tofu apply` nor `terraform apply` is authorized by this profile or its tests.

## Real-plan prerequisites

1. Create an OCI account without upgrading it to paid service.
2. Choose the home region carefully and create a dedicated canary compartment.
3. Confirm A1 capacity, an Always Free eligible Arm64 Ubuntu image, the 50 GB boot volume, and the
   Object Storage allowance in the console.
4. Copy `terraform.tfvars.example` outside Git, replace all placeholders, and change both explicit
   confirmations to `true` only after checking the console.
5. Authenticate the OCI provider through the operator's local credential mechanism.
6. Run `tofu plan -out=eldridge-oci-canary.tfplan`, inspect its JSON, obtain a separate execution
   approval, and retain an exact cost estimate before considering an apply.

Use OCI Cloud Shell for an operator-driven plan when local browser callbacks or local-network
permissions are not appropriate. Cloud Shell inherits the signed-in OCI identity, stays in the
selected OCI region, and does not require a long-lived API key on the operator workstation.

A short-lived local security-token session is an optional alternative:

```bash
oci session authenticate \
  --region us-sanjose-1 \
  --tenancy-name tricenharvey \
  --profile-name eldridge-session \
  --session-expiration-in-minutes 60
export OCI_CLI_PROFILE=eldridge-session
export OCI_CLI_AUTH=security_token
```

The generated local session material stays under the operator's `~/.oci` directory and expires.
It must not be used when the callback listener would require an unapproved local-network permission.
Real `*.tfvars`, saved `*.tfplan` files, and Terraform state are ignored by Git and must never be
copied into an issue, pull request, model prompt, or other shared artifact.

This is a single-node canary, not a highly available production topology. PostgreSQL, the API,
worker, and ingress will initially share the VM while encrypted database backups are copied to the
separate Object Storage failure domain. A paid or otherwise sustainable production profile should
separate database and worker failure domains once real usage justifies it.

## Live eligibility checkpoint

On 2026-09-25, the owner tenancy was inspected without creating workload resources. The console
confirmed:

- the tenancy home region is `us-sanjose-1` (US West, San Jose);
- `VM.Standard.A1.Flex` is selectable and marked **Always Free-eligible**;
- the shape accepts the bounded 2 OCPU and 12 GB configuration;
- Canonical Ubuntu 24.04 Minimal aarch64 is compatible and listed as **Free**; and
- a 50 GB boot volume with in-transit encryption is accepted by the instance builder.

This checkpoint establishes console eligibility, not host capacity at apply time and not a final
price guarantee. No instance, VCN, subnet, volume, bucket, or other workload resource was created
during the inspection. On 2026-09-26, the zero-cost `eldridge-canary` IAM compartment was created as
the isolation boundary for the provider-backed plan.

The signed-in OCI Cloud Shell then produced an authenticated, provider-backed planning result in
`us-sanjose-1`. Its sanitized JSON review showed eight creates and zero destructive actions. The
planned instance remained `VM.Standard.A1.Flex` at 2 OCPUs and 12 GB memory with a 50 GB boot
volume; the only public ingress was TCP 80 and 443; the Object Storage bucket was private and
versioned; and the embedded boundary remained USD 0 recurring with at most USD 5 for the temporary
exercise. That historical plan was later superseded when the owner reduced the temporary ceiling
to USD 0. No plan was applied and no instance, VCN, subnet, gateway, route table, security list,
volume, or bucket was created.

Cloud Shell supplied Terraform 1.5.7 rather than the repository's required OpenTofu/Terraform 1.9+
runtime. The provider-backed plan therefore used a version-only relaxation in a disposable Cloud
Shell clone and is evidence-only: it is not an apply candidate. The committed source constraint and
OpenTofu execution policy were not changed.

## Compatible OpenTofu plan checkpoint

Later on 2026-09-26, the official OpenTofu 1.12.6 Linux Arm64 release was downloaded inside Cloud
Shell and verified against its published SHA-256 checksum. OpenTofu rejected OCI's preinstalled
provider binary because it did not match the committed lock file, then installed the signed,
checksum-pinned OCI provider 8.29.0 from the OpenTofu registry. This preserved the fail-closed
supply-chain boundary.

A fresh checkout of merged `main` at revision
`0ee1ee432a2886b80f42981f02beb11961fb6e2e` produced the exact saved OpenTofu plan. Sanitized JSON
inspection again showed eight creates and zero destructive actions, the fixed A1 capacity, ports
80/443 only, a private versioned bucket, and the USD 0 recurring boundary. The 9,698-byte plan has
SHA-256 `bb120bfb1f91ec364b7a75e2390459186979461dae67ff8e1c0a1178c4946786`.

The saved plan remains only in the authenticated Cloud Shell workspace and is ignored by Git. No
apply occurred. It is now ineligible for apply because its USD 5 temporary boundary is broader than
the owner's current USD 0 maximum. DNS and production TLS are deferred; a replacement plan must
encode both zero-dollar ceilings and receive separate exact-fingerprint approval.
