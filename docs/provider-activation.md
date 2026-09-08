# Provider Activation Runbook

Claude and Devin are installed integration boundaries, not implicitly trusted providers. The normal runtime remains local and deterministic until an operator points `CONTROL_PLANE_PROVIDER_POLICY_FILE` at a reviewed policy. Credentials alone do not activate anything.

## Activation contract

Start from `provider-policy.example.json` and copy it to the ignored `provider-policy.json`. The policy is strict: unknown fields fail validation, external egress must be explicitly approved, enabled secrets must resolve through the reference-to-environment allowlist, and Phase 2 limits external data to `PUBLIC` or `INTERNAL`. Each integration also has a maximum risk and cost boundary.

Claude activation disables mock-provider fallback. This prevents an outage, routing failure, or policy mismatch from silently changing which model processes a task. The configured model identifier is copied into the routing profile and evidence partition. A changed model therefore starts without inherited qualification evidence.

Devin activation currently exposes only its bounded remote-session lifecycle: create, poll, and cancel with repository scope, tags, a maximum ACU limit, and optional structured-output schema. It is not placed in the normal task-provider routing pool yet. Phase 3 must first ingest Devin's commit or pull-request revision and independently validate its CI evidence; otherwise a remote result could bypass the same revision-bound controls applied to local worktrees.

Every routing record stores both the capability-router version and provider-activation policy version. This makes later evidence and incident review attributable to the exact configured policy.

## Safe canary sequence

1. Review provider data retention, regional processing, account roles, model access, and current pricing outside this repository.
2. Create least-privilege credentials and export them in the environment named by `secret_environment`. Never put credential values in either policy file.
3. Enable only Claude, set `allow_external_egress` to `true`, set `include_mock_providers` to `false`, and begin with `PUBLIC` plus `LOW` risk.
4. Run the single paid live probe explicitly:

   ```sh
   CONTROL_PLANE_RUN_LIVE_ANTHROPIC_TEST=true .venv/bin/pytest -m live_provider tests/test_live_providers.py::test_live_anthropic_minimal_structured_response
   ```

5. Review the request destination, normalized output, usage, audit event, routing record, and evidence partition before expanding to `INTERNAL` or `MEDIUM`.
6. Do not qualify a new model for `HIGH` or `CRITICAL` work until the configured evidence floor and independent-review rules are satisfied.

The Devin probe creates a real remote session and may consume paid capacity. It therefore requires a second, unmistakable opt-in and a repository already authorized in Devin:

```sh
CONTROL_PLANE_RUN_LIVE_DEVIN_TEST=true \
CONTROL_PLANE_LIVE_DEVIN_REPOSITORY=owner/repository \
.venv/bin/pytest -m live_provider tests/test_live_providers.py::test_live_devin_create_then_cancel
```

The probe cancels immediately after session creation. It validates only API identity, organization scope, budget submission, handle parsing, and cancellation—not safe ingestion of remote code.

## Rollback

Set the integration's `enabled` field to `false` or remove `CONTROL_PLANE_PROVIDER_POLICY_FILE`, then restart the API and worker. Revoke the provider credential if compromise is suspected. Because the policy is loaded at process startup, editing a file does not create a partially changed live configuration.

## Continuous improvement boundary

Promotion remains human-controlled. Validation outcomes can change evidence-backed ranking only among already eligible providers; they cannot broaden egress, classification, risk, cost, permissions, or approval authority. Model changes require a new policy version and begin a separate evidence history.
