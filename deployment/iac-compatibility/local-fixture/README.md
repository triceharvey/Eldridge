# Local IaC Compatibility Fixture

This fixture proves that Eldridge-authored HCL can be initialized, validated, planned, and rendered
as JSON by the pinned local OpenTofu and Terraform CLIs. It uses only the built-in
`terraform_data` resource, requires no provider download, creates no infrastructure, and is never
applied.

The compatibility check runs each engine in a separate temporary directory. That separation is
intentional: saved plans, working directories, and state must never be shared between engines.
OpenTofu remains Eldridge's authorized infrastructure execution layer. Terraform is a local
learning and compatibility target unless a later ADR and exact human approval expand its authority.
