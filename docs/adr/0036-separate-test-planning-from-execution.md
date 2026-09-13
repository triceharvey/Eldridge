# ADR-0036: Separate Model Test Planning from Execution Verdicts

## Status

Accepted on 2026-09-13. This supersedes the TEST-decision portion of ADR-0034; security and code
review decisions remain unchanged.

## Context

After ADR-0035 supplied the implementation artifact to the test provider, the provider returned
specific compile and unit-test failures while also proposing those tools for later execution. The
model had not executed either tool and was explicitly forbidden from doing so. Independent operator
execution then proved that both files compiled and all 36 generated tests passed.

The `tests_passed` field incorrectly combined two different authorities: a model's pre-execution
assessment and the controller's sandbox result. That ambiguity allowed invented observations to
block deterministic execution and made simulated test output resemble trusted evidence.

## Decision

The TEST provider contract now describes test planning only. It returns `test_plan_ready`,
`concerns`, and typed `tool_requests`; it cannot return `tests_passed` or `failures`. A false readiness
decision remains a preserved, terminal model rejection because no executable plan is available.

When the plan is ready, Eldridge validates each typed request and executes it against the exact
candidate revision in the networkless, read-only-root sandbox. Only a successful executor result
causes the durable task output to receive `tests_passed: true` and an empty `failures` list, alongside
the sandbox evidence. Provider prose cannot create that verdict.

## Consequences

- Model reasoning selects bounded test operations but cannot claim their outcomes.
- Durable `tests_passed` evidence has one authority: successful controller execution.
- Fabricated pre-execution failures no longer prevent valid typed tools from running.
- Malformed or non-ready test plans still fail closed before execution.
- Sandbox tool failure still fails the task and cannot advance the candidate.

## Alternatives Rejected

- Prompt the model to be more accurate while retaining `tests_passed`: leaves the authority ambiguity
  intact and relies on probabilistic compliance.
- Override a false `tests_passed` claim after execution: stores contradictory meanings under one
  field and obscures provenance.
- Allow the model to execute tools directly: bypasses controller authorization, containment, and
  evidence capture.
- Accept model-only test claims: provides no deterministic evidence at all.
