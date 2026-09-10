# Phase 5.1 Local Model Provider Acceptance

## Outcome

The zero-cost local-provider control boundary is implemented and contract-tested. No local model
runtime was installed or model artifact downloaded during this slice, so live inference evidence
remains a separate operator decision.

## Verified controls

| Control | Evidence |
|---|---|
| Disabled by default | Submission and health fail closed until the versioned provider policy enables the adapter |
| Local means local | Only literal loopback IPs, HTTP, explicit non-privileged ports, and `/v1/chat/completions` are accepted |
| No hidden egress | Hostnames, LAN/remote IPs, TLS URLs, alternate paths, queries, fragments, and redirects are rejected |
| No credential path | Configuration and requests contain no API-key or bearer-token field |
| Exact model binding | Health requires the configured model in `/v1/models`; responses from a different model are rejected |
| Structured result | The adapter requests one JSON object and rejects invalid envelopes, content, usage, or model identity |
| No tool authority | The local model receives no executable tool definition and cannot approve, deploy, or merge |
| Normal governance | Results pass through existing validation, evidence, routing, review, and human-approval controls |
| Version isolation | Provider evidence is partitioned by the configured model identifier and provider-policy version |

## Verification result

The complete local suite passed with 225 tests and four deliberate environment-gated skips. Ruff,
formatting, mypy, dependency audit, and whitespace validation passed. GitHub CI supplies the final
PostgreSQL migration, container packaging, TLS, fail-closed deployment, and secret-history checks for
the pull request.

## Remaining live evidence

A live canary requires the owner to select and install a compatible runtime and model. That action may
consume disk, memory, and download bandwidth even when its monetary cost is zero. The first canary
must remain loopback-only and low risk, record the exact model identifier, and be followed by resource,
quality, latency, and output-validation review. Installing a runtime is not authorized by this
acceptance record.
