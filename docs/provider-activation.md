# Provider Activation Runbook

Claude, Devin, and the local OpenAI-compatible adapter are installed integration boundaries, not
implicitly trusted providers. The normal runtime remains deterministic until an operator points
`CONTROL_PLANE_PROVIDER_POLICY_FILE` at a reviewed policy. Credentials or a running local server do
not activate anything by themselves.

## Activation contract

Start from `provider-policy.example.json` and copy it to the ignored `provider-policy.json`. The policy is strict: unknown fields fail validation, external egress must be explicitly approved, enabled secrets must resolve through the reference-to-environment allowlist, and Phase 2 limits external data to `PUBLIC` or `INTERNAL`. Each integration also has a maximum risk and cost boundary.

Claude activation disables mock-provider fallback. This prevents an outage, routing failure, or policy mismatch from silently changing which model processes a task. The configured model identifier is copied into the routing profile and evidence partition. A changed model therefore starts without inherited qualification evidence.

A Claude Pro subscription and the Anthropic Messages API are separate access paths. Pro can support
interactive Claude and Claude Code work, but it must not be represented as an API credential or
silently routed through the Messages adapter. Subscription-backed CLI or Agent SDK use requires its
own reviewed adapter and authentication flow; shared production automation should retain predictable,
separately budgeted API identity. Eldridge currently implements only the disabled-by-default
Messages API adapter.

Devin activation currently exposes only its bounded remote-session lifecycle: create, poll, and cancel with repository scope, tags, a maximum ACU limit, and optional structured-output schema. It is not placed in the normal task-provider routing pool yet. Phase 3 must first ingest Devin's commit or pull-request revision and independently validate its CI evidence; otherwise a remote result could bypass the same revision-bound controls applied to local worktrees.

Every routing record stores both the capability-router version and provider-activation policy version. This makes later evidence and incident review attributable to the exact configured policy.

## Zero-cost local model canary

Set `local_model.enabled` to `true`, choose the exact installed model, and set
`include_mock_providers` to `false` when measuring the local model without mock competition. External
egress stays disabled and no secret mapping is required. The endpoint must use literal loopback, for
example `http://127.0.0.1:11434/v1/chat/completions`; LAN and remote endpoints are rejected.

Start the operator-owned runtime separately, verify `/v1/models`, then begin with deterministic
low-risk fixtures. Review normalized output, validation, routing records, latency, resource pressure,
and exact model-version evidence before widening its risk ceiling. Local placement does not authorize
tools or bypass any human gate.

The repository includes a fail-closed live qualification command. The following example keeps
Ollama transient and loopback-only; it does not install an always-on service:

```sh
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  OLLAMA_NO_CLOUD=1 OLLAMA_NOHISTORY=1 ollama serve
ollama pull qwen3.5:9b-q4_K_M
curl --fail --silent http://127.0.0.1:11434/api/tags | jq \
  '.models[] | select(.name == "qwen3.5:9b-q4_K_M") | {name,digest}'
control-plane-local-canary \
  --runtime ollama \
  --model qwen3.5:9b-q4_K_M \
  --artifact-digest sha256:<full-manifest-digest-from-api-tags> \
  --output .canary/qwen3.5-9b-q4_K_M.json
```

Replace the digest placeholder with `sha256:` followed by the full digest returned by the runtime.
Do not enable a policy that still contains the all-zero example digest.

The report contains only synthetic fixture inputs and outputs. It records endpoint health, latency,
token usage, canonical output hashes, the full model-manifest digest, exact runtime/model identifiers,
and an explicit all-or-nothing result. The fixtures test structured planning, hostile
repository-instruction containment, and source context fidelity. A passing report supports only
provisional low-risk evaluation; it does not grant tool, merge, deployment, publication, or
human-approval authority. Preserve reviewed reports outside
Git or in an approved immutable evidence store when they are used for a promotion decision.

For a canon-governed project such as KAiJU Frenchies, use the same pattern with project-specific
holdout fixtures built from the approved canon bible and provenance ledger. A local model may propose
metadata, checks, drafts, or review findings, but canon changes, commercial asset release, storefront
publication, and production changes remain human decisions.

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
