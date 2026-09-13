# Phase 5.5A Reusable Operator Workflow Acceptance

## Outcome

On 2026-09-12, Eldridge packaged its proven model-to-validation pipeline as a reusable operator
command for explicitly registered local Git projects. The command accepts one strict, versioned JSON
manifest, provides a read-only preflight without activating providers, requires deliberate execution
and external-egress confirmations, and drives only the created workflow to
`AWAITING_HUMAN_APPROVAL`.

This slice productizes the previously qualified workflow boundary. It does not claim another paid
provider run or broaden Eldridge into unattended development: the command cannot approve, push,
open a pull request, merge, deploy, or overwrite a registered base branch.

## Bound controls

| Control | Evidence |
|---|---|
| Repository authority | Scope resolves only through the operator-owned repository registry |
| Base authority | Manifest digest must be immutable and equal the registry's independently resolved base |
| Write authority | One to 64 unique relative paths, each within the registry grant |
| Provider authority | Every model task has one enabled, capable provider within its risk, classification, and workflow quota ceilings |
| Review independence | Review identity differs from the producer; high-risk work also differs by provider family |
| Egress | External providers are disclosed by preflight and require a separate confirmation |
| Execution | Existing isolated worktree, non-root container, typed-tool, and executable-test controls remain active |
| Queue isolation | Leasing is restricted to the workflow created by this operator invocation |
| Retry bound | Manifest sets a six-to-48 task-lease ceiling |
| Human authority | Execution stops at the revision-bound human approval gate |

## Verification

- 314 deterministic tests passed; eight explicitly opt-in tests were skipped.
- New tests reject an unregistered commit even when it exists in the registered repository.
- New tests prove a run leaves an older unrelated ready task untouched in a shared database.
- New tests reject stage plans beyond configured provider risk and subscription workflow ceilings.
- CLI tests prove preflight emits the bound JSON plan and execution fails without confirmation.
- The PostgreSQL lease integration passed against the repository's declared Docker Compose service.
- Ruff lint, Ruff format verification, strict mypy, and `pip-audit` passed.

The skipped live-provider, live-repository, k3d, and default PostgreSQL tests remain explicit because
they require external activity or destructive local-state opt-in. PostgreSQL was then invoked
separately with its test URL and passed.

## Operator interface

```sh
.venv/bin/control-plane workflow preflight \
  --manifest operator-workflow.json \
  --repository-registry repositories.json \
  --provider-policy provider-policy.json
```

After inspecting that result, the operator may invoke `workflow run` with `--confirm-execution` and,
when disclosed by preflight, `--confirm-external-egress`. Full use and safety boundaries are recorded
in [`operator-workflows.md`](operator-workflows.md) and
[ADR-0033](adr/0033-manifest-bound-operator-workflows.md).
