# Devin Operator Runbook

This runbook takes one implementation task from an Eldridge workflow to a Devin session and back
as Git-verified evidence. The design and failure behavior are in ADR-0044; the integration
boundary is described in [interoperability](interoperability.md#devin-sessions).

Devin is a paid remote agent. Every step below is an explicit human action, and Eldridge never
routes work to Devin on its own.

## 1. One-time setup

1. **Devin side.** Create a Devin service user with only the `UseDevinSessions`,
   `ViewOrgSessions`, and `ManageOrgSessions` organization permissions, and give the Devin GitHub
   integration access to the target repository. Note the organization ID.
2. **Provider policy.** Copy `provider-policy.example.json` to the ignored `provider-policy.json`
   and set:

   ```json
   "allow_external_egress": true,
   "devin": {
     "enabled": true,
     "organization_id": "org-your-id",
     "service_token_ref": "devin-service-token",
     "maximum_cost_units": 5,
     "maximum_data_classification": "INTERNAL",
     "maximum_risk": "MEDIUM"
   }
   ```

   `maximum_cost_units` is the ACU ceiling for any single dispatch.
3. **Repository registry.** Add `remote_agent_repository` and the narrowest writable paths the
   task needs:

   ```json
   {
     "scope_id": "owner/repository",
     "path": "/absolute/path/to/clone",
     "writable_paths": ["src", "tests"],
     "remote_agent_repository": "owner/repository"
   }
   ```

   The clone's `origin` must be `https://github.com/owner/repository` (or the SSH form). Eldridge
   fetches with prompts disabled, so a private repository needs a non-interactive credential for
   that clone, such as a read-only deploy key.
4. **Environment.**

   ```sh
   export DEVIN_API_KEY=...                 # never commit it
   export CONTROL_PLANE_DATABASE_URL=postgresql+psycopg://...
   export CONTROL_PLANE_PROVIDER_POLICY_FILE=provider-policy.json
   export CONTROL_PLANE_REPOSITORY_REGISTRY_FILE=repository-registry.json
   ```

5. **Live probe (once).** Confirm the account, organization, and repository access with the
   existing create-then-cancel probe in [provider activation](provider-activation.md).

## 2. Per task

Do not run `control-plane-worker` for the workflow you plan to send to Devin: a running worker
leases ready tasks, including the implementation task you want to dispatch. `prepare` drives only
the named workflow.

```sh
# Create the workflow and stop at its implementation task.
.venv/bin/control-plane devin prepare \
  --title "Short task title" \
  --objective "$(cat objective.md)" \
  --repository-scope owner/repository \
  --idempotency-key "devin-$(date +%Y%m%d)-short-name"
# -> {"implementation_task_id": "...", "ready_for_devin": true, ...}

.venv/bin/control-plane devin dispatch --task-id TASK --max-cost-units 3
.venv/bin/control-plane devin sync --task-id TASK     # repeat; each sync renews the lease
.venv/bin/control-plane devin cancel --task-id TASK   # if the session goes off track
```

Read `remote.state` in each `sync` result:

| State | What to do |
|---|---|
| `QUEUED`, `RUNNING` | Sync again later. |
| `AWAITING_INPUT` | Answer Devin in its web app (the session URL is in the result), then sync. |
| `SUSPENDED` | Usually a credit or usage limit; resolve it in Devin, then sync. |
| `SUCCEEDED` with `task_status: SUCCEEDED` | Evidence accepted; the workflow moves to `TESTING`. |
| `attempt_status: FAILED` | Read `remote` and the attempt `error`; the task returns to `READY` while the retry budget lasts. |

Sync at least once per `CONTROL_PLANE_REMOTE_AGENT_LEASE_SECONDS` (default four hours). An
unsynced session expires into human reconciliation, as does any dispatch whose outcome is unknown.

## 3. After ingestion

The accepted commit is pinned locally on `devin/task-<task>-a<attempt>` and recorded as
`DEVIN_IMPLEMENTATION_EVIDENCE`. Devin's test claim and pull-request URL are context only. Resume
the normal worker (or drive the remaining stages) so Eldridge runs its own test, security, and
code-review stages, then use the usual pull-request, readiness, and human merge-approval path.
Close or supersede Devin's own pull request rather than merging it directly.

## Writing the objective

Devin receives the objective plus Eldridge's constraints (base commit, branch, writable paths,
no merges or CI changes, structured output). A good objective is a short engineering ticket:

```markdown
## Goal
One sentence describing the observable behavior that must change.

## Context
Where the relevant code lives and any known cause, with file paths.

## Requirements
- Concrete, testable requirements.
- Behavior that must not change.

## Tests
- The regression test to add first, and the command that must pass.

## Out of scope
- What not to touch, even if it looks related.

## Done when
- The new test fails before the change and passes after it.
- The full test command passes.
```
