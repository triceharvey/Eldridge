# Testing Strategy

Tests are enforcement evidence, not a final cleanup stage. The highest priority is proving that prohibited actions remain prohibited under invalid input, retries, races, and compromised-agent behavior.

## Test layers

### Unit tests

Cover transition predicates, grant matching, role baselines, approval scope/expiry, error classification, context classification/redaction, artifact validation, budget math, and provider normalization. Tests use fixed clocks and deterministic IDs where time/order matters.

### State-machine and property tests

Generate state/action sequences and assert that only registered transitions occur. Invariants include monotonic versions, no advancement without stage evidence, no terminal-state mutation, and no direct path from implementation to deployment. Every forbidden transition returns a stable error and emits the appropriate denial audit record without changing state.

### Permission and adversarial tests

Verify deny-by-default behavior for missing, expired, overbroad, or wrong-scope grants. Required cases include:

- an implementer attempts to approve its own work;
- an orchestrator attempts to create a human approval;
- a security agent attempts to modify its governing policy;
- one task attempts to read/write another task's path or artifact;
- a provider output embeds fake approval/test evidence;
- repository prompt injection requests a secret or network capability; and
- path traversal, symlink escape, shell metacharacters, and environment-variable leakage attempts.

### Provider contract tests

Run a shared suite against every adapter: capability declaration, valid structured result, malformed result, timeout, cancellation, idempotency, unknown handle, rate limit, authentication failure, usage mapping, redaction, and unsupported features. Default CI uses sanitized fixtures and `MockProvider`; live tests are explicit and budget-controlled.

### Persistence and integration tests

Use PostgreSQL—not only mocks—for transactions, constraints, leases, optimistic concurrency, audit/event atomicity, idempotency, and outbox behavior. Test two workers racing for one task, lease expiry, late results, duplicate commands, and process restart.

### Executor security tests

Beginning in Phase 2, verify filesystem mounts, user ID, Linux capabilities, resource limits, network denial, process cleanup, artifact boundaries, and absence of control-plane/cloud/Git-admin credentials. Include malicious test repositories and dependency hooks in an isolated test environment.

### End-to-end tests

Drive the public API through one deterministic workflow: request, plan, independent review, implementation evidence, tests, security review, code review, and human approval. Assert audit continuity and revision binding. Separate negative flows cover rejection, cancellation, retry exhaustion, stale approval, and provider outage.

Phase 3 also builds separate API and trusted-worker container targets, inspects their non-root runtime identities, validates the rendered production Compose model, starts the read-only API image against PostgreSQL, and probes liveness plus authenticated Prometheus exposition. Hosted acceptance adds real OIDC, GitHub protected-branch, webhook, TLS, scrape, and backup/restore evidence.

Phase 4.1 drives the deployment command API through immutable environment registration, exact
plan creation, separate approval, intent commit, simulated execution, observation, and
verification. Negative cases reject production environments, credential-bearing adapters,
unknown operation types and fields, unbound artifacts, wrong plan digests, expired approvals,
agent authority, and idempotency-key drift.

Phase 5 acceptance distinguishes deterministic fixtures, local runtime canaries, and live-provider
evidence. A generated output is not validated evidence until a named, versioned controller-owned
check stores its own digest and the exact output digest it assessed. A passing deterministic check is
not an independent model review. Before hosted-production claims or broader scaling work, run one
explicitly authorized real workflow end to end and retain the provider, prompt contract, artifact,
validation, review, Git revision, protected-PR, and human-disposition evidence.

## Critical acceptance cases

| Case | Expected result |
|---|---|
| `IMPLEMENTING -> DEPLOYED` | Denied; state unchanged; denial audited |
| Agent principal submits approval | Denied regardless of claimed role text |
| Windsurf requests a planning/review task or omits its scoped lease | Denied before repository evidence is inspected |
| Windsurf reports a revision, ancestry, or changed-file set that Git does not confirm | Rejected; workflow remains at implementation |
| Implementer reviews its own artifact | Does not satisfy independent-review predicate |
| Candidate revision changes after tests | Test/security/review evidence becomes inapplicable |
| Candidate revision changes after approval | Approval cannot be consumed |
| Worker loses lease then returns success | Late result recorded for investigation but cannot advance workflow |
| Audit write fails during transition | Entire transition rolls back |
| Same idempotency key and body repeats | Original response returned; no duplicate workflow/action |
| Same idempotency key with different body | Conflict and security-relevant audit event |
| Unknown provider capability | Task remains unscheduled with a clear blocked reason |

## Quality gates

Phase 1 requires formatting/linting, type checks, unit tests, PostgreSQL integration tests, dependency vulnerability review, and migration checks. Coverage percentages are supporting indicators; invariant and negative-path coverage are the release gate. Any skipped security-critical test must fail CI unless a time-bound, human-approved exception is recorded outside the agent's control.

## Test evidence

Evidence includes tool/version, command or structured invocation, start/end time, exit status, report digest, candidate revision, environment/image digest, and relevant policy version. A prose statement such as "tests passed" is not evidence. Re-running against a different revision creates new evidence rather than updating the prior record.
