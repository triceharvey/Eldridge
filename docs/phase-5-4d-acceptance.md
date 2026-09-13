# Phase 5.4D Governed Python Change Acceptance

## Outcome

On 2026-09-12, Eldridge completed and merged its first real model-authored Python change. Claude Code
Pro performed planning and implementation through the non-interactive CLI adapter. The pinned local
Qwen model performed architecture, test, security, and code review. The final candidate added a
change-scope classifier and 13 deterministic tests, changed exactly two authorized paths, and passed
the isolated test tools, the complete local quality suite, PostgreSQL integration, and protected
GitHub CI before merge.

Claude was invoked with `claude -p` in a temporary directory, with tools disabled, plan permission
mode, safe mode, and session persistence disabled. Consequently, these invocations do not appear as
conversations in the Claude desktop or web application. Authentication was verified as `claude.ai`
Pro rather than an API key.

## Bound evidence

| Evidence | Value |
|---|---|
| Base revision | `b91059bb1d54a82b1b423cded2a6b9ef5a2c7b43` |
| Model candidate | `c00b504a77121b8e06419afe4c08c834e2c1f912` |
| Protected merge | `133ccc7cbf180c3a44d7c597ef29f0d9556f0410` |
| Changed paths | `src/control_plane/change_scope.py`; `tests/test_change_scope_generated.py` |
| Claude ceiling | Two workflow invocations: planning and implementation |
| Local reviewer | `qwen3.5:9b-q4_K_M@sha256:6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7` |
| Isolated tests | `PYTHON_COMPILE`; `PYTHON_UNITTEST` |
| Local regression | 303 passed; Ruff, format, mypy, and dependency audit passed |
| Database integration | PostgreSQL lease integration passed on the declared compose port |
| Pull request | [PR 26](https://github.com/triceharvey/Eldridge/pull/26) |

## Continuous-improvement evidence

Earlier candidates did not merge. They exposed and drove correction of four concrete gaps:

1. TEST results could omit executable evidence. Eldridge now requires a typed test request for the
   live schema.
2. The local adapter supplied prose rather than the exact response schema. Qwen produced
   `type`/`target` instead of `name`/`targets`; schema parity corrected the live response.
3. A generated test used the wrong package import boundary. The isolated runner rejected it.
4. A semantically correct candidate missed the repository's formatter output. Human inspection
   rejected it before PR creation and the requirement was added to the next refinement.

The final unmodified model candidate passed the complete pre-PR checks. GitHub then passed secret
scanning, packaging, and the full hosted test job before protected merge. This is evidence of the
intended model-to-validation-to-refinement loop, not evidence that arbitrary code generation or
unattended merging is safe.

## Reproduction boundary

The evidence harness is opt-in because it consumes Claude subscription allowance, starts a local
model, creates isolated Git branches, and uses Docker:

```sh
CONTROL_PLANE_RUN_LIVE_CODE_WORKFLOW=true \
CONTROL_PLANE_LIVE_REPOSITORY="$PWD" \
CONTROL_PLANE_LIVE_CODE_REPORT=.canary/phase-5-4d-live-code-workflow.json \
  .venv/bin/pytest -m live_provider \
  tests/test_live_code_workflow.py::test_live_code_workflow_creates_tested_exact_approved_revision
```

The original fixed target paths now exist on `main`, so a repeat qualification should use new,
explicitly approved paths and a new operation key rather than overwrite this accepted evidence.
