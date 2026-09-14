# ADR 0042: Select OCI Always Free for the First Hosted Canary Plan

- Status: Accepted
- Date: 2026-09-13
- Accepted by: project owner through the approved cost-controlled hosted-canary roadmap

## Context

Eldridge needs real hosted evidence without converting its temporary approximately USD 5 exercise
into an open-ended bill. Azure and AWS provide valuable professional experience, but their broad
free-account benefits are time-limited or credit-backed. OCI documents a persistent Always Free
allocation that includes Ampere A1 compute, block and object storage, load balancing, secrets,
monitoring, and networking suitable for a small proof of concept.

## Decision

Select OCI Always Free as the first plan-only hosted canary target. The initial topology uses one
Arm64 `VM.Standard.A1.Flex` instance limited to 2 OCPUs, 12 GB memory, and a 50 GB boot volume, plus
a private versioned Object Storage bucket. OpenTofu is the authorized engine; Terraform must remain
compatible for professional practice but receives no apply authority.

The configuration fails planning unless the operator explicitly confirms the OCI home region and
the console's Always Free eligibility labels. It fixes recurring budget at USD 0 and bounds the
separately approved temporary exercise at USD 5. Tests use a mocked provider and cannot create OCI
resources.

## Consequences

- The smallest credible hosted canary can be planned without purchasing a persistent cluster.
- Arm64 images must be published and scanned before activation.
- API, worker, ingress, and PostgreSQL initially share one VM, so the canary is not highly available.
- Encrypted database backups must be copied to Object Storage and restored as part of acceptance.
- Capacity is not guaranteed, and idle Always Free instances may be reclaimed by Oracle.
- Account creation, credentials, DNS, a real plan, and any apply remain separate owner actions.

## Rejected Alternatives

- **Azure as the first target:** retained for career practice and a later managed-service exercise,
  but its broad free allowances are time-limited and do not establish a persistent zero-dollar
  baseline.
- **AWS free plan as the first target:** retained for career practice, but the account plan is a
  bounded proof-of-concept period rather than a permanent hosting commitment.
- **Persistent hosted K3s:** rejected for the first canary because it adds cost and cluster operations
  before Eldridge has measured workload demand.
- **Treat OCI Always Free labels as guaranteed:** rejected; eligibility and home-region capacity must
  be confirmed immediately before planning and again before any separately approved apply.

## Source Basis

- [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [OCI Free Tier lifecycle and home-region boundary](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [OpenTofu mocked-provider tests](https://opentofu.org/docs/cli/commands/test/)
- [Azure free-service categories](https://azure.microsoft.com/en-us/pricing/free-services)
- [AWS Free Tier account boundaries](https://aws.amazon.com/free/free-tier-faqs/)
