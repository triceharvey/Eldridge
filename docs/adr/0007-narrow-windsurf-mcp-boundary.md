# ADR 0007: Narrow Windsurf MCP boundary

## Status

Accepted for local Phase 2 development on 2026-09-07. Remote or shared exposure is not approved.

## Context

Windsurf Cascade supports MCP tools but is an interactive IDE agent, not a documented generic headless execution API. Giving an MCP client the worker's queue, arbitrary filesystem operations, or generic workflow mutation would collapse the control plane's separation of duties. Returned prose also cannot establish that code, tests, or approvals are valid.

## Decision

Expose a separate localhost Streamable HTTP MCP server with exactly three tools: claim one explicit ready implementation task, heartbeat its lease, and submit Git-bound implementation evidence. Bind the server to a dedicated integration principal and a disabled-by-default development bearer credential. Replace the scheduled implementer grant with a narrow task grant while the handoff is active.

The handoff binds task, repository scope, branch, immutable base revision, policy, identity, writable paths, and required evidence by digest. Git verifies branch head, ancestry, changed files, and path scope. IDE-reported test results are non-authoritative and do not bypass independent test or review stages. Late results follow the existing lease-expiry reconciliation path.

## Consequences

Cascade can participate through a standards-based interface without being treated as a trusted orchestrator or approval authority. The local token is intentionally not presented as production identity. Phase 3 must add OIDC/OAuth, TLS, audience and scope checks, credential lifecycle, and explicit network-origin policy before remote use.
