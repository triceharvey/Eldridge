# ADR-0037: Enforce Local Provider Output Schemas at Generation

## Status

Accepted on 2026-09-13.

## Context

The 8,192-token local model resolved the earlier context truncation, but a later live architecture
review returned structurally invalid JSON objects twice. Both Ollama requests completed without
truncation, so retrying with more context or time would not address the failure.

The adapter used OpenAI-compatible JSON mode and embedded the task schema in its system prompt. JSON
mode guarantees an object-shaped response, not conformance to the required properties and literal
values. Ollama supports JSON Schema constrained generation through the OpenAI-compatible
`response_format` field.

## Decision

For every local-provider request, send the existing per-task JSON Schema as a strict named
`json_schema` response format. Continue embedding the same canonical schema in the system prompt and
continue parsing and validating the returned object inside Eldridge.

This changes response shaping only. It does not trust the model, weaken post-response validation,
grant tool execution, or change the provider's repository, egress, approval, or merge authority.

## Consequences

- Local generation is constrained to the same contract Eldridge validates afterward.
- Required fields, literal values, property types, and additional-property restrictions become less
  dependent on prompt compliance.
- Provider-side schema support is now part of the qualified local OpenAI-compatible runtime profile.
- An unsupported or malformed provider response still fails closed through the existing bounded
  retry policy.
- Schema conformance does not make model findings true; deterministic execution and independent
  review remain separate controls.

## Alternatives Rejected

- Continue JSON mode with stronger wording: does not mechanically constrain required properties.
- Increase retries: spends time without changing the malformed-generation condition.
- Remove local validation because the runtime enforces a schema: trusts an external component at the
  controller boundary.
- Switch providers immediately: discards the zero-cost qualified candidate before using its
  documented schema capability.
