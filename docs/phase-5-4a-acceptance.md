# Phase 5.4A Subscription-Backed Claude Qualification Acceptance

## Outcome

Eldridge now has a disabled-by-default Claude Code provider that uses an authenticated Claude
subscription rather than an Anthropic API key. A live synthetic probe returned the required
structured result through this boundary on 2026-09-11.

## Verified controls

| Control | Evidence |
|---|---|
| Separate provider identity | `claude-code-subscription` is distinct from the Messages API adapter but shares the Anthropic review family |
| Explicit activation | Provider policy must enable Claude Code, approve external egress, disable mock fallback, and set classification and risk ceilings |
| Subscription-only authentication | The adapter requires a logged-in `claude.ai` subscription and removes API-key and alternate-cloud overrides |
| No repository exposure | Each request runs from a new empty temporary directory and receives only control-plane supplied input |
| No autonomous tools | Claude Code starts with an empty tool set, plan permission mode, and safe mode |
| No retained session | Noninteractive calls use disabled session persistence |
| Structured evidence | Output must normalize to a nonempty JSON object; invalid and empty responses fail closed |
| Bounded initial scope | The live canary used only a synthetic public objective and no project content |
| Honest iteration record | Two pre-acceptance calls exposed trusted-envelope and unconstrained-schema defects; both failed their assertions before the corrected canary passed |

## Evidence scope

This proves subscription authentication, bounded invocation, normalization, and a real Claude model
response. It does not yet prove repository-aware generation, multi-model comparison, independent
cross-family review, protected pull-request creation, or end-to-end human disposition. It consumes
Claude plan allowance and does not authorize usage credits or API billing.

```bash
CONTROL_PLANE_RUN_LIVE_CLAUDE_CODE_TEST=true \
  .venv/bin/pytest -m live_provider \
  tests/test_live_providers.py::test_live_claude_code_subscription_minimal_structured_response
```
