# Reusable Operator Workflows

Eldridge can run its governed model pipeline against any explicitly registered local Git project.
The operator command replaces phase-specific Python harnesses with one versioned JSON manifest and
two commands: a read-only preflight and an explicitly confirmed execution.

## Safety boundary

The manifest binds one workflow to:

- an operator-owned repository scope;
- an immutable 40-character Git commit;
- narrower writable paths within the repository registry grant;
- one named provider identity for every model stage;
- complexity, risk, classification, routing objective, and a task-lease ceiling.

Planning and implementation providers must differ from their corresponding reviewer identities.
High-risk work also requires a different provider family. Preflight rejects missing providers,
unsupported capabilities, provider risk/classification ceilings, insufficient subscription quota,
a symbolic base such as `HEAD`, a commit other than the registry's approved base, path expansion
beyond the registry, and incomplete role plans.

Execution uses the existing worktree and digest-pinned Docker boundary, requires executable test
evidence, and stops at `AWAITING_HUMAN_APPROVAL`. It cannot approve, push, open a pull request, merge,
deploy, or overwrite the registered base branch.

## Prepare the inputs

1. Copy `operator-workflow.example.json` to an ignored operator file.
2. Set `base_revision` to the exact commit approved in the repository registry. Do not use a branch
   name or a different commit that merely exists in the same repository.
3. Set exact files or the smallest justified subdirectories in `writable_paths`.
4. Give the objective explicit behavior, tests, rejection cases, and typed-tool expectations.
5. Configure the repository in the ignored registry described by `docs/project-onboarding.md`.
6. Configure and explicitly enable only the providers needed in the ignored provider policy.

The example assigns Claude Code Pro to planning and implementation and a pinned local model to the
four remaining stages. Other assignments are accepted only when the provider is enabled, supports
the stage, and satisfies the independence rules.

## Preflight without model calls

```sh
.venv/bin/control-plane workflow preflight \
  --manifest operator-workflow.json \
  --repository-registry repositories.json \
  --provider-policy provider-policy.json
```

Preflight reads Git and policy metadata but does not activate providers, contact Claude, load a local
model, create a database, or create a worktree. Its JSON output includes the resolved repository,
immutable base, writable paths, stage assignments, policy version, external providers, and final
stop state.

## Execute with explicit confirmation

For a disposable local evidence database:

```sh
.venv/bin/control-plane workflow run \
  --manifest operator-workflow.json \
  --repository-registry repositories.json \
  --provider-policy provider-policy.json \
  --database-url sqlite:///operator-workflow.db \
  --worktree-root .control-plane-worktrees \
  --create-schema \
  --confirm-execution \
  --confirm-external-egress
```

Omit `--confirm-external-egress` only when preflight reports no external provider. Omit
`--create-schema` for a migrated persistent database. The command emits JSON events for task
attempts and exits after printing the candidate revision at the human gate. A retryable failure may
consume another task lease, but provider workflow quotas still apply.

Claude Code subscription calls use `claude -p` with no session persistence, so they do not appear as
conversations in the Claude app. Their normalized results are recorded by Eldridge.

## Human disposition

After the command stops, inspect the exact branch and revision, then run the target repository's full
lint, format, type, unit, integration, secret, dependency, and packaging checks. Approval and PR
creation remain separate deliberate actions. A candidate that needs modification is a new revision
and requires new validation and approval; do not treat an earlier approval as transferable.
