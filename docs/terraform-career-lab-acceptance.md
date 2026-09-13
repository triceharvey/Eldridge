# Local Terraform Career Lab Acceptance

## Scope

Phase 5.7 adds a real Terraform lifecycle exercise without adding infrastructure authority. The lab
uses a reusable child module, validated variables, locals, lifecycle conditions, outputs, local CLI
workspaces, saved plans, JSON inspection, state inspection, and explicit destroy.

## Security and Cost Boundary

- Terraform 1.16.2 and OpenTofu 1.12.6 validate the reusable module and its no-apply saved plan.
- Only Terraform applies, and only the built-in `terraform_data` resource is permitted.
- State exists only in a test-owned temporary directory and is never committed.
- Default and staging CLI workspaces retain separate state.
- No provider plugin, account, credential, remote backend, HCP workspace, infrastructure object, or
  bill is created.
- The module rejects any nonzero monthly budget before execution.
- Both workspace resources are destroyed and their state lists are verified empty.

## Professional Skills Demonstrated

- standard reusable module layout;
- typed variables and custom validation;
- locals, lifecycle preconditions and postconditions, and outputs;
- formatting, initialization, validation, planning, JSON plan review, apply, and destroy;
- CLI workspace and local-state separation; and
- automation-friendly `-detailed-exitcode` change detection.

## Verification

- Five career-lab tests passed against the installed Terraform and OpenTofu versions.
- The complete local suite passed with 362 tests, one destructive k3d test skipped, and seven
  external-provider tests deselected.
- PostgreSQL integration passed against the temporary Compose service.
- Terraform and OpenTofu recursive format checks passed.
- Ruff lint and format checks, strict mypy, and `pip-audit` passed.
