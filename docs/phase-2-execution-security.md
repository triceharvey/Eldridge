# Phase 2 Execution Security

## Status

The provider-neutral isolation path is implemented and composed into the runtime. Real model access remains disabled until the project owner selects the first provider and approves its data-egress, retention, credential, and cost policy.

## Repository registration

Workflow submissions contain only an opaque `repository_scope`. The API cannot supply a host path, mount, base revision, or writable directory. At process startup, the operator may load `CONTROL_PLANE_REPOSITORY_REGISTRY_FILE`, whose entries map approved scope IDs to canonical local Git repository roots, verified base revisions, and typed-tool write paths. Unknown scopes fail closed.

Copy `repository-registry.example.json` to an ignored local `repository-registry.json`, replace the placeholder path, and set the environment variable. Registration verifies that the path is the Git top-level directory and that the base revision resolves to a commit. The registry file is process configuration, not a workflow-controlled artifact.

## Worktree isolation

`GitWorktreeManager` resolves an immutable base commit and creates one `codex/task-<task-id>` branch in a dedicated worktree root. Task identifiers are restricted to safe characters, destinations must remain beneath the configured root, revisions cannot be option-like values, and Git is executed with argument arrays rather than a shell.

Host-side Git is invoked with repository hooks and filesystem monitors disabled, commit signing disabled, system/global configuration excluded, and a minimal environment that does not inherit control-plane credentials. Registration rejects local Git filters, external diff commands, and filesystem monitors because checkout, status, or commit could otherwise execute repository-controlled host commands before the container boundary applies.

Concurrent worktrees prevent agents from overwriting one another's files. Removing a worktree preserves its branch by default so completed work remains available for review. Branch deletion is an explicit option intended for verified cleanup and tests.

The composed executor creates one worktree per repository task. Implementation tools may write only beneath registered paths. Validation and review tasks receive a read-only workspace. Successful implementation changes are committed to the task branch with a fixed service identity; the control plane reads the resulting Git commit and treats it as authoritative instead of trusting a provider-supplied revision. Worktrees are removed after execution, successful implementation branches are preserved, and failed or read-only task branches are removed.

## Typed tools

The Phase 2 registry exposes three operations:

- `LIST_FILES`: enumerates regular workspace files while excluding `.git` internals;
- `PYTHON_COMPILE`: parses Python source without writing bytecode; and
- `WRITE_TEXT_FILE`: writes one UTF-8 file only beneath a task-granted path.

Requests reject unknown fields, absolute paths, parent traversal, backslash paths, null bytes, and `.git` components. A caller cannot supply an executable, shell expression, Docker option, environment variable, mount, user, network, or image.

## Container controls

The Docker executor uses a Python image pinned to an immutable multi-architecture SHA-256 digest. Every run enforces:

- a non-root UID/GID;
- `--network none`;
- a read-only container root filesystem;
- all Linux capabilities dropped;
- `no-new-privileges`;
- process, memory, CPU, timeout, and captured-output limits;
- a bounded `noexec,nosuid` temporary filesystem;
- only the assigned worktree mounted at `/workspace`;
- a read-only workspace mount unless the authorized tool requires a write; and
- no Docker socket, SSH agent, home directory, cloud credential path, or control-plane secret mount.

Timeout cleanup targets an internally generated container name and performs no wildcard or broad resource deletion. Container output is bounded and decoded defensively before it can become evidence.

## Remaining Phase 2 work

1. Approve activation policy for Claude and/or Devin; both adapters are implemented but disabled.
2. Run opt-in live-provider contract tests and prove that no integration bypasses the mock workflow's state and approval gates.

## Windsurf MCP boundary

The optional Windsurf server implements Streamable HTTP at `/mcp` and remains disabled by default. It binds a configured localhost bearer credential to the `windsurf-cascade` integration principal and exposes only three tools: explicit implementation-task claim, lease heartbeat, and implementation-evidence submission. It has no queue listing, prompt resource, arbitrary filesystem tool, generic state setter, approval, merge, or deployment operation.

The handoff digest binds the exact task, repository scope, branch, base commit, policy, assigned identity, writable paths, and evidence requirements. Submission checks the active scoped grant and lease before Git inspection and again under the final database lock. Git supplies the authoritative branch head, ancestry, and changed-file list. Cascade's test statement is retained as a claim and cannot satisfy the separately scheduled deterministic test gate.

The Phase 2 bearer mechanism is suitable only for a local single-user development boundary. Remote exposure remains prohibited until Phase 3 replaces it with OIDC/OAuth, TLS, audience/scope verification, and deployment-specific DNS-rebinding and Origin controls.

## Durable execution boundary

Task preparation, external execution, and finalization use separate transactions. The committed attempt is observable as `RUNNING` before provider or container work begins, so a worker crash cannot roll execution intent back into an apparently untouched task. A configurable background heartbeat renews the task lease through independent transactions while external work is active.

Finalization locks the task and checks its status, owner, unguessable token, and expiration. Evidence from a timed-out, reclaimed, reconciled, or otherwise superseded run is rejected. A lease that expires before execution begins can be reclaimed automatically; a lease that expires after `RUNNING` is treated as an unknown external outcome and blocks the workflow. A human must record a reasoned `RETRY` or `FAIL` reconciliation. Retrying creates a new task and grant while retaining the timed-out attempt and immutable reconciliation record.

The control plane must not enable a real provider merely because an API key is present. Provider activation is explicit configuration tied to a policy and repository data classification.
