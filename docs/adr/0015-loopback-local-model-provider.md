# ADR 0015: Loopback-Only Local Model Provider

## Status

Accepted for implementation on 2026-09-09 as a zero-cost Phase 5 interoperability slice.

## Context

Eldridge needs an operator-controlled inference option that does not require a commercial account,
API credential, or external data transfer. Several local runtimes expose an OpenAI-compatible HTTP
surface, but compatibility alone does not make an arbitrary URL local or trustworthy. A configurable
endpoint could otherwise become an SSRF or silent-egress path around provider policy.

## Decision

Add a disabled-by-default `LocalOpenAIProvider` that accepts only an uncredentialed `http` URL using
a literal loopback IP address, an explicit non-privileged port, and the exact
`/v1/chat/completions` path. Hostnames, LAN addresses, TLS URLs, embedded credentials, alternate
paths, queries, fragments, and redirects fail closed. Health is checked through `/v1/models` on the
same loopback origin.

Activation is explicit in the versioned provider policy and requires no secret or external-egress
approval. The adapter requests deterministic JSON output, normalizes usage, exposes no tool
execution, and passes results through the same task validator, evidence store, capability router,
reviews, and human gates as every other provider. The exact configured model identifier partitions
qualification evidence.

## Consequences

- Ollama, LM Studio, llama.cpp, vLLM, or another compatible runtime can be evaluated without a paid
  provider when it is intentionally started on loopback.
- Local inference avoids provider egress but does not make repository input or model output trusted.
- A model upgrade begins a new evidence partition and cannot inherit the prior model's qualification.
- High-risk work remains blocked until the exact model has the configured evidence floor.
- The adapter has no shell, filesystem, deployment, secret, approval, or merge authority.
- LAN and remote endpoints require a future, separately approved provider boundary with transport
  identity and network policy.

## Alternatives

- **Allow arbitrary OpenAI-compatible URLs:** rejected because the local label could conceal remote
  egress or SSRF.
- **Allow `localhost`:** rejected because name resolution is mutable; literal loopback addresses are
  easier to validate.
- **Store an optional bearer token:** rejected for this slice because a credentialed endpoint needs a
  distinct secret and transport-security design.
- **Trust local output automatically:** rejected because locality changes data flow, not correctness.
