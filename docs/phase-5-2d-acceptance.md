# Phase 5.2D Trusted Artifact Validation Acceptance

## Outcome

Captured provider outputs can now become durable, snapshot-bound evaluation artifacts. Versioned
controller-owned validators produce output-digest-bound evidence, and the existing deterministic
campaign core selects or rejects candidates from those records. This completes the first real local
generate-to-validate-to-decide loop without granting promotion authority.

## Verified controls

| Control | Evidence |
|---|---|
| Human command boundary | Agents cannot start an assessment or cause validator execution |
| Current-source requirement | `UNKNOWN`, running, stale-version, stale-revision, stale-iteration, and terminal-campaign sources fail closed |
| Committed check intent | Assessment, artifact, and required-check rows exist before validator code runs |
| Exact artifact provenance | Each artifact binds its provider run, execution, campaign, workflow, task, workflow version, optional candidate revision, content, and SHA-256 digest |
| Trusted validator registry | Campaign check names must map to unique, controller-configured validator versions |
| Bounded evidence | Validator results must be typed JSON and no larger than 64 KiB; validator errors become failed generic evidence |
| Digest-bound checks | Every check stores the exact validated output digest and a separate evidence digest |
| Deterministic security boundary | Sensitive credential fields and fake approval, merge, deployment, or publication authority fields are rejected |
| Existing decision authority | Candidates pass through the durable campaign evaluator; callers and models cannot supply ranking scores |
| Bounded refinement | Failed checks produce `REFINEMENT_REQUIRED` or `EXHAUSTED` according to the existing iteration and budget limits |
| High-risk independence | Passing deterministic checks do not satisfy independent-review requirements |
| Replay safety | Repeating a completed assessment returns the original artifacts and decision without calling providers again |
| Controlled learning | Finalized provider runs create one append-only, version-specific observation; replay cannot duplicate it and self-scores are excluded |

## Evidence scope

Acceptance is verified in deterministic unit/API tests and the PostgreSQL migration/concurrency
suite. It is not evidence of a hosted deployment or a live Claude, Devin, Windsurf, or local-model
engineering job. The next safety slice must durably execute independent reviews and reconcile
ambiguous provider outcomes. Controlled repair and human promotion follow. Before broader platform
expansion, Eldridge must run one real end-to-end workflow and record its actual limitations.
