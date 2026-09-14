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

This is a single-node canary, not a highly available production topology. PostgreSQL, the API,
worker, and ingress will initially share the VM while encrypted database backups are copied to the
separate Object Storage failure domain. A paid or otherwise sustainable production profile should
separate database and worker failure domains once real usage justifies it.
