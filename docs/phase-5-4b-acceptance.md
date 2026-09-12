# Phase 5.4B Real Multi-Model Vertical Slice Acceptance

## Outcome

On 2026-09-12, Eldridge completed one explicitly opted-in live chain using a subscription-backed
Claude producer and an independent loopback-only Qwen reviewer. The controller admitted the
producer output only after deterministic schema, security, and revision-binding checks, bound the
review to the exact output digest, and required a human promotion command for that same digest.

## Verified controls

| Control | Evidence |
|---|---|
| Zero-dollar policy distinction | Metered external providers remain rejected; subscription use requires an explicit per-execution invocation ceiling |
| Bounded Claude use | The activated policy permitted one public, low-risk Claude invocation and no mock fallback |
| Enforced output shape | Claude Code received a task-specific JSON Schema; malformed output cannot enter validation |
| No repository disclosure | The provider received only the supplied public workflow description and repository-scope label, with no file content or tools |
| Deterministic admission | Schema, security, and revision-binding checks passed and retained evidence digests |
| Independent review | `local-openai-compatible` used the `operator-local` family and the pinned `qwen3.5:9b-q4_K_M` manifest digest |
| Exact artifact binding | Producer output, review, checks, and promotion all reference the same SHA-256 artifact digest |
| Human authority | Promotion required an explicit human command; neither model received merge, deployment, credential, or approval authority |
| Fail-closed iteration | The first live execution returned `UNKNOWN` on an invalid response; JSON Schema enforcement was added before a fresh run passed |

## Reproduction

Start the approved digest-pinned model on literal loopback with cloud access and history disabled,
confirm Claude Code subscription authentication, then run:

```sh
CONTROL_PLANE_RUN_LIVE_VERTICAL_SLICE=true \
CONTROL_PLANE_VERTICAL_SLICE_REPORT=.canary/phase-5-4b-live-vertical-slice.json \
  .venv/bin/pytest -m live_provider \
  tests/test_live_vertical_slice.py::test_live_claude_producer_local_reviewer_vertical_slice
```

The optional ignored report contains provider and model identifiers, object IDs, and evidence
digests, but not raw model output, credentials, account identifiers, or repository content.

## Evidence scope

This proves a real cross-family producer-review-promotion loop under a bounded subscription policy.
It does not prove repository-aware code generation, competing producer comparison, model-authored
file changes, protected pull-request creation, or hosted production operation. Those remain the
Phase 5.4C engineering-workflow slice rather than being inferred from this acceptance.
