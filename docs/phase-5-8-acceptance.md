# Phase 5.8 Production Readiness Gate Acceptance

## Outcome

Phase 5.8 converts Eldridge's remaining hosted-production conditions into one deterministic,
fail-closed command. The evaluator accepts only a complete production manifest, enforces bounded and
redacted validation, checks eight gate outcomes, verifies cross-gate release-revision bindings,
rejects stale or future evidence, and returns a canonical SHA-256 manifest digest.

The checked-in example is intentionally non-ready. Its evaluation returns exit code `2` and 23
specific blockers without contacting an external system or changing state. This is honest readiness
evidence: the gate exists and works, while the hosted observations remain outstanding.

## Safety Boundary

- Maximum manifest size is 64 KiB.
- Unknown fields and malformed types fail closed.
- Validation errors exclude submitted values so credentials are not echoed.
- URLs containing credentials and example.com placeholders are rejected.
- Image references must use full SHA-256 digests.
- The evaluator has no deployment, merge, credential, infrastructure, or network authority.
- A passing report does not replace final human activation approval.

## Remaining External Work

The eight hosted gates remain incomplete until a real environment exists. Phase 5.8 reduces the
remaining problem to evidence collection and remediation against an explicit contract; it does not
misrepresent local fixtures as hosted-production proof.

## Verification

- `ruff format --check`, repository-wide `ruff check`, and `mypy` passed.
- The local non-provider suite passed with 368 tests, one intentionally skipped destructive k3d
  exercise, and seven deselected environment-specific tests.
- The Docker-backed PostgreSQL integration lane passed independently with one test and 375
  deselected tests.
- `pip-audit` reported no known vulnerabilities in auditable dependencies.
- The six focused production-readiness tests cover the success path, failed outcomes, revision
  mismatch, placeholder and credential rejection with redacted errors, timestamp freshness, input
  bounds, invalid JSON, and the CLI exit contract.
