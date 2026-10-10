# ADR-0044: Ingest Devin sessions only through Git-verified, human-dispatched handoffs

- Status: Proposed (awaiting owner approval)
- Date: 2026-10-10

## Context

Eldridge has had a Devin v3 lifecycle adapter (create, poll, cancel) since Phase 2, deliberately
kept out of task routing "until Phase 3 can bind a remote commit or pull request to independently
ingested CI and validation evidence" (`docs/interoperability.md`). Phase 3 now provides
revision-bound evidence, and the Windsurf boundary (ADR-0007, ADR-0028) already proves an external
agent's revision with Git rather than with the agent's own report.

Devin differs from Windsurf in two ways that matter here. It runs in its own cloud machine and
pushes to GitHub rather than to the operator's local repository, and it never calls Eldridge. A v3
session that has finished usually remains `running` with `status_detail` `finished`, and a session
waiting on a person reports `waiting_for_user` or `waiting_for_approval`.

## Decision

Add a `DevinIntegrationService` with three human-only operations behind a new
`DISPATCH_REMOTE_AGENT` capability (granted to `HUMAN_APPROVER`, `require_human=True`):

1. **Dispatch** a `READY` implementation task in an `IMPLEMENTING` workflow. The repository scope
   must be registered with an explicit `remote_agent_repository` (GitHub `owner/name`) and
   writable paths. Eldridge builds a digest-bound handoff (base commit, requested branch, writable
   paths, ACU limit, evidence schema), commits a `RUNNING` attempt owned by the capability-less
   `devin-remote` integration identity, and only then creates the session with a structured-output
   schema, the attempt ID as idempotency key, and the handoff digest as a tag.
2. **Sync** polls the session. In-flight states renew a long remote lease. A finished session's
   structured output names a branch and head commit; Eldridge checks that `origin` is the
   registered GitHub repository, fetches that branch into a new local `devin/task-<id>-a<n>`
   branch, and runs the same `verify_revision_evidence` checks as Windsurf: immutable digests,
   branch head equals the claimed commit, descent from the handoff base, non-empty changes, and
   only registered writable paths. Accepted evidence becomes a `DEVIN_IMPLEMENTATION_EVIDENCE`
   artifact; Devin's test claim and pull-request URLs are recorded as non-authoritative, and the
   workflow advances to its independent `TEST`, security, and code-review stages.
3. **Cancel** terminates the session and closes the attempt.

Devin stays out of automatic routing. Dispatch is an explicit, paid, human decision.

## Alternatives

- Put Devin in the routing pool: lets the router spend ACUs without a person and would accept a
  remote result the control plane cannot reproduce. Rejected.
- Trust Devin's reported pull request: the PR and its CI live outside Eldridge's evidence chain
  and can change after ingestion. Rejected in favor of fetching and pinning the commit locally.
- Receive Devin webhooks: requires an inbound endpoint and a Devin-side credential to Eldridge.
  Polling by a human keeps Devin without any control-plane identity.

## Consequences

Devin becomes usable for real implementation work while every revision it produces passes the
same evidence gate as local and Windsurf work. Operators must register each repository for
remote agents, give the local clone a GitHub `origin` the fetch can reach with non-interactive
credentials, and sync sessions often enough to keep the remote lease alive
(`CONTROL_PLANE_REMOTE_AGENT_LEASE_SECONDS`, default four hours).

## Failure behavior

- Rejected before contact (cost, classification, or risk limit; integration disabled): the
  attempt fails and the task returns to `READY`.
- Any other dispatch error: the session may exist, so the lease is expired immediately and the
  existing reconciliation path blocks the workflow for a human; nothing retries a paid session.
- Session `error` or `terminated`, missing or malformed structured output, a revision that is not
  the branch head, ancestry or writable-path violations: the attempt fails through the normal
  retry budget, preserving the handoff and session ID for audit.
- Fetch failures (network, credentials, origin mismatch) change no state; sync again after fixing
  the cause.
- An unsynced session whose lease expires blocks the workflow for human reconciliation.
