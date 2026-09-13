# ADR-0038: Bound Provider Prose Arrays

## Status

Accepted on 2026-09-13.

## Context

Strict JSON Schema enforcement removed malformed property shapes, but the common schema for plans,
assumptions, findings, concerns, and failures placed no limit on array length or string length. In a
live architecture review, the local model remained schema-compliant while generating until the
2,048-token response ceiling on both bounded attempts. Neither response contained a complete JSON
object, so Eldridge closed the workflow before implementation.

Increasing the response budget would consume more latency and memory without defining how much
review evidence the control plane actually accepts. Bounded structured output needs bounds on prose,
not only on tool counts and file content.

## Decision

Every provider-output array whose elements are prose strings is limited to eight items of at most
500 characters each. The same limits apply across planning, architecture review, test planning,
security review, and code review through the shared schema definition.

Eldridge continues to validate the completed response after provider-side generation. Hitting a
provider token limit, violating a schema bound, or returning incomplete JSON fails closed under the
existing retry policy.

## Consequences

- Review output has a predictable maximum size before it enters durable state.
- A local model cannot consume an unbounded generation window with repetitive findings.
- Operators receive concise, individually bounded findings suitable for audit and refinement.
- Large or numerous concerns must be prioritized by the provider instead of silently expanding cost.
- These prose limits do not reduce typed file-content limits or executable test evidence.

## Alternatives Rejected

- Raise `max_tokens`: increases latency and resource use without an acceptance bound.
- Add prompt wording only: relies on probabilistic brevity rather than enforceable structure.
- Truncate completed provider text: can corrupt JSON and arbitrarily remove critical findings.
- Accept partial JSON: destroys schema validation and evidence integrity.
