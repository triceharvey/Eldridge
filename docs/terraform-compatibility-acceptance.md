# Terraform Compatibility Acceptance

## Outcome

On 2026-09-13, HashiCorp Terraform 1.16.2 for macOS arm64 was installed from HashiCorp's official
release server after its archive matched the published SHA-256 checksum. The installed executable
digest is `2d5bc6e7ad80e2ec9a83f896e4150e58ba32afd47b5adbcd5e6a882a3b05f418`.

The official Homebrew tap could not be registered under Homebrew 7.0.1 because unrelated tap
formulae did not pass Homebrew's current validation. The temporary tap trust was removed, and the
verified official binary was installed directly instead. No HCP account, workspace, remote backend,
credential, provider package, managed resource, trial credit, or billing method was used.

## Compatibility Boundary

- OpenTofu 1.12.6 remains the authorized Eldridge infrastructure engine.
- Terraform 1.16.2 is a compatibility and professional-learning target only.
- Both engines use separate temporary working directories.
- The fixture has no external providers and only one built-in `terraform_data` resource.
- The test performs `init`, `validate`, `plan`, and `show -json`; it never performs `apply`.
- Successful validation produces no state file and creates no infrastructure.

Run the local proof with `make iac-compatibility` from an activated project environment.

## Cost Boundary

Local Terraform CLI use does not consume HCP Terraform's free-plan managed-resource allowance.
HCP Terraform is intentionally unactivated. Any later hosted exercise remains separately gated and
must stay within the owner's zero-dollar policy unless the owner approves an exact temporary budget.
