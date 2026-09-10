# ADR 0013: Controlled Local Recovery

## Status

Accepted on 2026-09-09.

## Context

A deployment control plane is incomplete if it can identify a bad outcome but cannot recover
without bypassing its own authority, identity, evidence, and idempotency boundaries. Phase 4.4
must prove that recovery is a separate privileged operation, not an implied extension of the
original deployment approval. The exercise must remain inside the approved USD 0 local k3d target
and must not imply that arbitrary production rollback is safe.

## Decision

Add a distinct `ROLLBACK` approval action and separate `APPROVE_ROLLBACK` and
`EXECUTE_ROLLBACK` capabilities. A rollback approval is valid only while the workflow is
`ROLLBACK_REQUIRED` and binds the exact failed deployment attempt, environment, immutable plan
digest, merged revision, policy version, and recorded rollback reference. Rejection is recorded
and consumed but leaves the workflow contained.

The service commits a durable rollback record and consumes the exact approval before constructing
an exact-request broker or contacting the target. A new 600-second Kubernetes TokenRequest bound
to the `ROLLBACK` operation is delivered only within the trusted adapter callback. The adapter
may restore only the pre-deployment release-marker snapshot already stored in the failed
deployment evidence. It performs a separate target read and compares the complete restored
snapshot. Verified recovery transitions the workflow to the terminal `ROLLED_BACK` state.

Rollback records retain approval and attempt references, immutable request digest, idempotency
key, rollback reference, status, operation reference, sanitized credential metadata, execution,
observation and verification evidence, error code, timestamps, and measured recovery duration.
An ambiguous rollback becomes non-replayable `UNKNOWN` and remains `ROLLBACK_REQUIRED`.

## Consequences

- Deployment approval never grants rollback authority.
- Credential policy distinguishes `APPLY_PLAN` from `ROLLBACK` and creates a broker bound to the
  complete authorized request.
- Stored target snapshots accept only the four release-marker fields and validated digest,
  revision, and idempotency formats; arbitrary ConfigMap data cannot be introduced through
  recovery evidence.
- Successful idempotent replay returns the durable rollback record without a second credential or
  target mutation.
- Failed or ambiguous recovery never transitions the workflow to success.
- Migration 0010 replaces the previously unused placeholder rollback table only when it contains
  zero rows; it fails closed rather than deleting legacy recovery evidence.
- This proves recovery for one reversible local marker. It does not establish that database,
  schema, production, or other provider changes are reversible.

## Alternatives

- **Reuse the deployment approval:** rejected because deployment and recovery have different risk
  and timing.
- **Let the adapter choose an earlier version:** rejected because that would permit target state
  selection outside the approved durable evidence.
- **Automatically retry ambiguous rollback:** rejected because the target may already have
  changed.
- **Mark recovery complete after the update response:** rejected because success requires an
  independent target observation.
