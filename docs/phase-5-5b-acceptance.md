# Phase 5.5B Live Operator Feedback and Negative-Review Acceptance

## Outcome

On 2026-09-12, Eldridge used the Phase 5.5A `control-plane workflow run` command—not a
phase-specific Python harness—to exercise Claude Code Pro and the pinned local Qwen reviewer against
the public Eldridge repository. The manifest bound commit
`27a83bc33774177c3898927794c945226a89b3ee`, exactly two new Python paths, public/low-risk data, two
Claude stages, four local review stages, and a twelve-lease ceiling.

The exercise did not produce an approved or merged candidate. Instead, it supplied useful
continuous-improvement evidence and proved that the controller and repository checks remained above
model consensus.

## Exercise Evidence

1. Workflow `4177db62-c75f-4143-8d2c-f58741747903` reached the human gate with candidate
   `43c4363320a7d380349a5bbd214828de232aa503`. Independent repository validation rejected it because
   Ruff found five deprecated-typing violations and manual contract review found that it required
   integer schema version `1` while the actual preflight emits string `"1"`. It was not approved.
2. Workflow `e52e95c7-12e6-4d73-be50-8b5c3f42c993` stopped before implementation. Ollama evidence
   showed the local architecture review generating roughly 1,700 tokens at about 9.6 tokens per
   second and reaching the 180-second HTTP timeout. The next policy reduced the local output ceiling
   to 2,048 tokens and raised its timeout to 300 seconds without changing repository authority.
3. Workflow `139a8a40-d155-462a-8871-4da8f4469408` passed planning, architecture, implementation, and
   executable tests, producing candidate `6696f62f86d84a32c4d4e49c1cc58f614515d62c`. The local security
   reviewer returned a substantive non-passing decision. Eldridge stopped before code review or the
   human gate, and the candidate was not approved.

The third run exposed that valid negative reviews were retried and their findings were replaced by a
generic validation error. ADR-0034 corrects that behavior.

## Implemented Control

- Test, security, and code-review schemas now accept explicit boolean decisions.
- A false decision is represented by `ProviderReviewRejectedError`, separate from malformed output.
- The exact structured rejection is retained in the task attempt.
- A substantive rejection is terminal after one attempt and transitions the workflow to `FAILED`.
- No candidate revision advances to human approval after negative review.
- `operator-workflow.json` is now ignored alongside other operator-owned policy files.

## Verification

- 318 deterministic tests passed; eight explicit external or destructive-local-state tests skipped.
- A new integration test proves one negative security review is retained and never retried.
- Parameterized contract tests cover negative TEST, SECURITY_REVIEW, and CODE_REVIEW decisions.
- Ruff lint and format verification, strict mypy, and `pip-audit` passed.
- PostgreSQL integration passed against the declared local Docker Compose service.

The next live operator run must start from the protected merge containing this control and use a new
manifest base, provider-policy version, and idempotency key. Prior candidates remain evidence only.

## Continued Live Feedback

The protected ADR-0034 revision was exercised with a scoped 8,192-token Ollama model profile. The
larger context resolved the prior architecture-review truncation without changing the global local
model service. Workflow `d3f073c6-051d-4859-8ca3-97f472c4d4ca` then passed planning, architecture
review, and isolated implementation before stopping at TEST.

The retained negative test output showed that the test provider had not received the implementation
artifact and inferred that the two candidate files were absent. The implementation had in fact
completed through typed writes on an isolated candidate branch. ADR-0035 binds the successful
implementation output and canonical digest into TEST requests, matching the existing security- and
code-review provenance boundary. A new workflow is required to qualify that correction; the stopped
candidate remains unapproved evidence.

The evidence-bound follow-up, workflow `171afa73-c50b-42de-aa9c-1ad24ac9b8cd`, exposed a separate
authority ambiguity. Its TEST provider received the full implementation output but claimed that the
module had a line-one syntax error and that a generated assertion failed before any typed tool had
run. Independent execution against candidate `dd3ebd7c827e938465b2cc02c6c3414e1f54b609` compiled both
files and passed all 36 generated tests. ADR-0036 therefore separates probabilistic test planning
from deterministic execution: models propose a ready plan and concerns, while only successful
sandbox evidence may create `tests_passed: true`. The candidate remains unapproved evidence and a
fresh workflow must qualify the corrected contract.
