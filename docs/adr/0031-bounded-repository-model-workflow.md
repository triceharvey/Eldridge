# ADR-0031: Bind real-model repository changes to typed tools and workflow quotas

- Status: Accepted
- Date: 2026-09-12

## Context

Phase 5.4B proved the evaluation, deterministic-validation, independent-review, and human-promotion
chain, but it did not create a repository revision. Eldridge already had isolated worktrees, a
networkless non-root Docker executor, path-scoped typed tools, and protected pull-request controls.
Two gaps prevented a defensible real-model workflow: Claude's structured implementation contract did
not express typed tool proposals, and subscription invocation ceilings applied to evaluation batches
but not the multi-stage workflow.

Review tasks also received a candidate revision but not the exact producer output. That was
insufficient for a reviewer to evaluate what the producing model actually proposed.

## Decision

Add bounded `tool_requests` to the implementation output schema and continue validating each request
through the existing typed-tool registry. Add an explicit per-workflow subscription invocation
ceiling. Once the ceiling is exhausted, routing marks that subscription provider ineligible and may
continue only through another already authorized provider.

Architecture, security, and code-review requests receive the exact validated producer output plus
its controller-computed SHA-256 digest as untrusted context. The one-time Phase 5.4C exercise grants
Claude only planning and code-generation capabilities, caps it at two workflow invocations, grants
the local pinned Qwen model the review and validation roles, and permits one writable repository
path. The operator—not either model—pushes the resulting isolated branch and opens the pull request.

## Consequences

- A model can propose repository changes without receiving shell, Git, GitHub, credential, merge, or
  deployment authority.
- The executor, not the model, determines the resulting commit revision.
- Review evidence is bound to the producer output reviewers actually received.
- Subscription consumption cannot silently expand as a workflow schedules later stages.
- A protected pull request remains a separate operator action and GitHub independently enforces its
  required checks.

## Failure behavior

Invalid tool shapes, parent traversal, ungranted paths, extra changed files, Docker failure, provider
ambiguity, output-schema failure, reviewer failure, exhausted subscription allowance, or stale Git
evidence fail closed. A failed implementation worktree is removed; a successful candidate branch is
preserved for human inspection. No failure grants fallback authority or bypasses protected `main`.
