# Contributing to Eldridge

Eldridge is currently owner-led and pre-release. Focused issues and pull requests are welcome
after the repository becomes public, but acceptance is not guaranteed.

## Development workflow

1. Start from the latest `main` revision and create a narrowly scoped branch.
2. Keep providers, outbound network access, privileged tools, and live integrations disabled
   unless the change explicitly tests an approved boundary.
3. Add or update tests for state transitions, authorization, failure handling, and security
   invariants affected by the change.
4. Run the local verification commands documented in `README.md`.
5. Open a pull request describing the risk, evidence, rollback path, and any unresolved limits.

Never commit credentials, private keys, real production policy files, `.env` files, provider
responses containing private data, local databases, or control-plane worktrees. Use the
sanitized example files already included in the repository.

## Pull-request expectations

- Model output is advisory and cannot substitute for tests or deterministic policy checks.
- Approvals must remain bound to the exact Git revision they authorize.
- Changes may not weaken separation of duties, auditability, or human approval to make a test
  pass.
- New dependencies require a concrete operational or security justification.
- Third-party or AI-assisted material must follow the provenance and compatibility checks in
  `docs/licensing.md`.
- Live-provider tests must remain separately opted in and must clearly disclose cost and data
  egress.

Security vulnerabilities must follow `SECURITY.md`, not the public issue tracker.
