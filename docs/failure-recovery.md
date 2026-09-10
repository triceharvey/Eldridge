# Failure Modes and Recovery

Recovery must preserve evidence and distinguish retryable work from ambiguous external side effects. A failure is not resolved by changing state manually in the database; operators use authorized recovery commands that create new attempts and audit events.

| Failure mode | Detection | Automatic response | Human action when needed |
|---|---|---|---|
| Invalid request or provider output | Schema validation | Reject; record sanitized validation evidence | Correct input or adapter; no automatic retry loop |
| Provider rate limit/transient outage | Normalized error | Bounded backoff with jitter and budget check | Reroute only after policy/egress review if outage persists |
| Provider accepted work but status is unknown | Missing/ambiguous callback or timeout | Poll/reconcile using remote handle; do not duplicate | Decide disposition when provider cannot prove outcome |
| Worker crashes before execution starts | Leased task expires | Return the same task to `READY`; preserve an audit event | None unless expiry repeats |
| Worker crashes after execution starts | Running lease heartbeat expires | Mark attempt `TIMED_OUT`, block workflow, revoke its grant, reject late results | Verify external reality, then record `RETRY` or `FAIL` reconciliation |
| Sandbox timeout/resource exhaustion | Runtime limit event | Terminate sandbox; preserve bounded diagnostics; mark attempt | Increase limit only through a new grant/policy decision |
| Sandbox escape indicator | Runtime/security alert | Kill and quarantine; revoke credentials; block related executor image | Incident response and integrity review before reuse |
| Test failure | Structured test report | Create remediation path or fail by retry policy | Accept risk only if policy permits an explicit exception |
| Security finding | Scanner/reviewer evidence | Block stage at configured severity | Mitigate, dispute with evidence, or record scoped exception |
| Merge conflict | Git base/candidate mismatch | Mark blocked; do not auto-force resolution | Rebase/replan and rerun evidence on new revision |
| Stale approval | Revision, target, policy, or expiry mismatch | Invalidate and return to approval gate | Review and approve the new exact target |
| Database unavailable | Readiness/transaction error | Stop leasing and fail authoritative commands closed | Restore service; reconcile leases and backups |
| Audit event write fails | Transaction failure | Roll back the associated state/action | Repair storage before retrying command |
| Git/CI callback duplicated or reordered | Delivery/idempotency ID and revision | Deduplicate; retain event; recompute only legal predicate | Reconcile if remote and local state disagree |
| Partial deployment | Deployment status/health verification | Enter `ROLLBACK_REQUIRED`; stop further rollout | Choose rollback, roll-forward, or containment using runbook |
| Ambiguous rollback | Timeout or transport loss after rollback request | Record non-replayable `UNKNOWN`; remain `ROLLBACK_REQUIRED` | Observe the target and authorize a new exact recovery decision |
| Budget exhaustion | Token/cost/time counters | Cancel if safe; mark blocked/failed; prevent fan-out | Increase budget with scoped approval or reduce task |
| Cancellation during execution | Cancellation token/state | Stop new work; terminate after grace period; reject late result | Reconcile external side effects if termination was uncertain |

## Retry policy

Retry policy belongs to the task definition and error taxonomy. It specifies maximum attempts, backoff range, total time/cost budget, and whether the operation is idempotent. Authorization denials, invalid configuration, policy violations, unsafe output, and deterministic test failures do not receive blind automatic retries.

Each retry creates a new attempt and retains prior evidence. The retry may use the same provider only when the failure is transient. Provider fallback is never automatic when it changes data-egress classification or security posture.

## Reconciliation

Reconcilers compare durable intent with external reality for provider handles, sandboxes, Git changes, CI checks, and deployments. They are idempotent and use immutable external IDs and revision digests. They may repair missing metadata or schedule safe follow-up work, but they cannot invent successful evidence or approvals.

The implemented worker commits task and attempt state as `RUNNING` before invoking a provider or executor. A background heartbeat renews the lease in short independent transactions. Finalization locks the task and accepts evidence only when the status, owner, token, and unexpired lease still match. This makes a result from an abandoned worker non-authoritative even if it arrives after recovery has begun.

An expired `LEASED` task has not started external execution and returns to `READY`. An expired `RUNNING` task has an unknown outcome: the attempt becomes `TIMED_OUT` with `LeaseExpired`, the task and workflow become blocked, and only an authenticated human with `RECONCILE_EXECUTION` may decide `RETRY` or `FAIL` with a rationale. Retry closes the abandoned task as `RECONCILED` and creates a new task and capability grant; it never reopens or overwrites the old attempt.

## Locks and orphan cleanup

Leases replace indefinite locks. A lease includes owner, token, expiry, and heartbeat. Orphan cleanup operates only on resources labeled with a validated task/run ID and confirms that no active lease exists. Cleanup must never target broad directories, repositories, or unscoped infrastructure.

## Rollback

Code changes normally recover by creating a new reviewed revision, not rewriting Git history. Control-plane database migrations require tested forward and rollback/restore procedures. Production rollback is not assumed safe: schema changes, data writes, and irreversible operations may require roll-forward. Therefore a failed deployment enters a decision state with captured evidence.

The Phase 4.4 local path implements one reversible exception: an eligible human approves
`ROLLBACK` for the exact failed attempt and recorded release-marker snapshot. The control plane
commits intent before a fresh operation-bound credential is requested, restores only that
snapshot, and enters `ROLLED_BACK` only after an independent exact read. A rejection or ambiguous
rollback leaves the workflow in `ROLLBACK_REQUIRED`.

## Recovery exercises

Before a capability is considered production-ready, test worker termination during each external step, duplicate callbacks, provider timeouts, database restart, expired leases, stale approvals, merge conflicts, cancellation, and failed post-deploy verification. Document recovery time and any operator-only step.
