# ADR-0032: Require executable, schema-bound model test evidence

## Status

Accepted on 2026-09-12.

## Context

The first real Python-change canary exposed a gap between a model saying tests passed and the
control plane possessing executable test evidence. The local provider also received only a prose
output contract, which allowed semantically correct but structurally invalid tool fields.

## Decision

Eldridge adds a typed `PYTHON_UNITTEST` tool restricted to explicit Python files below `tests/`.
It executes without a shell in the existing digest-pinned, networkless, non-root, read-only
container. A live TEST-stage result must contain at least one typed executable request. Local models
receive the same exact task-specific JSON Schema used to validate their response.

These controls do not authorize arbitrary commands, dependency installation, network access, host
execution, or writes during testing. Repository-wide Ruff, formatting, type, dependency, and
integration checks remain independent human and protected-CI gates.

## Consequences

- A model's assertion that tests passed is insufficient for a live repository workflow.
- Tool-name, field-name, path, and target mistakes fail before approval.
- A retryable local-model failure may consume a bounded task retry but cannot re-enable an exhausted
  external subscription provider.
- Broader test frameworks require a separately reviewed typed tool or a purpose-built pinned image.

## Alternatives rejected

- Arbitrary shell or model-proposed commands: too much authority.
- Trusting prose test claims: no deterministic evidence.
- Quietly normalizing invented field names: hides contract drift and weakens auditability.
