# Phase 5.4C Real Repository Workflow Acceptance

## Outcome

On 2026-09-12, Eldridge completed a real six-stage workflow against the public Eldridge repository.
Claude performed the bounded planning and implementation roles; the manifest-pinned local Qwen model
performed architecture challenge, testing evidence, security review, and final code review. The
networkless executor created one isolated commit changing one authorized Markdown path, and the human
operator approved that exact revision before opening protected pull request 21.

## Bound evidence

| Evidence | Value |
|---|---|
| Base revision | `0278a09760c10db54734a73008c514ae049c5995` |
| Candidate revision | `c93ca6492cb86e2a517928a6114a666eee60b2f6` |
| Changed path | `docs/generated/phase-5-4c-model-authored-note.md` |
| Claude workflow ceiling | Two invocations: planning and implementation |
| Local reviewer | `qwen3.5:9b-q4_K_M@sha256:6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7` |
| Repository input | Allowlisted public `docs/phase-5-4b-acceptance.md` content |
| Executor | Digest-pinned non-root Docker container, network `none` |
| Human gate | Exact candidate revision approved after all six tasks succeeded |
| Pull request | [PR 21](https://github.com/triceharvey/Eldridge/pull/21) |

## Controls exercised

- Claude received no repository mount, tools, credentials, shell, Git, GitHub, merge, or deployment
  authority. It returned a schema-constrained typed write proposal.
- The Docker executor allowed writes only to the exact registered path and computed the real Git
  revision after applying the proposal.
- Review requests contained the exact producer output and controller-computed digest as untrusted
  context.
- The subscription provider became ineligible after its two authorized workflow invocations; later
  tasks remained local.
- The repository registry verified that the candidate descended from the immutable base, matched the
  preserved branch head, and changed exactly one allowed file.
- The operator inspected the generated text, pushed the branch, and created the PR. GitHub—not the
  models—enforced the protected-branch checks.

## Reproduction boundary

The opt-in test is skipped during normal CI because it consumes Claude subscription allowance,
requires the pinned local model, writes an isolated Git branch, and uses Docker:

```sh
CONTROL_PLANE_RUN_LIVE_REPOSITORY_WORKFLOW=true \
CONTROL_PLANE_LIVE_REPOSITORY="$PWD" \
CONTROL_PLANE_LIVE_REPOSITORY_REPORT=.canary/phase-5-4c-live-repository-workflow.json \
  .venv/bin/pytest -m live_provider \
  tests/test_live_repository_workflow.py::test_live_repository_workflow_creates_exact_approved_revision
```

This proves a low-risk, documentation-only, repository-aware workflow and model-authored protected
PR. It does not yet prove arbitrary code generation, competing external producers, unattended merge,
hosted operation, or production deployment. Those remain separate authorization and qualification
decisions.
