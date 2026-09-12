# ADR-0029: Add a subscription-backed Claude Code provider

- Status: Accepted
- Date: 2026-09-11

## Context

Eldridge's Anthropic Messages adapter requires a separately billed API credential. The project owner
already has a Claude Pro subscription and an authenticated Claude Code installation, but treating
that login as an API key would misrepresent the billing and trust boundary. Anthropic documents
Claude Code as included with Pro while warning that `ANTHROPIC_API_KEY` overrides subscription
authentication and produces API charges. See Anthropic's
[Pro and Max Claude Code guidance](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)
and [CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage).

The first real-provider proof should use synthetic public input and must not expose a repository,
grant tools, persist a provider session, silently use API billing, or weaken Eldridge's external
egress policy.

## Decision

Add a disabled-by-default `ClaudeCodeProvider` and a separate `claude-code-subscription` routing
identity. Activation requires approved external egress, no mock fallback, an authenticated
`claude.ai` subscription session, and explicit data-classification and risk ceilings.

Every invocation runs in a new empty temporary directory with Claude tools disabled, plan
permissions, safe mode, a control-plane-owned system prompt, and session persistence disabled. The
adapter passes only a minimal allowlist of operating-system environment variables, excluding API
keys and alternate cloud-provider overrides, before verifying authentication. It sends only the
trusted task metadata, objective, and explicitly labeled untrusted context, then accepts only a
nonempty JSON object.

The provider shares the `anthropic` family with the Messages API adapter. It therefore cannot count
as an independent cross-family reviewer for another Anthropic-produced artifact.

## Alternatives considered

- Use the Messages API immediately. Rejected for this slice because Pro does not include API usage
  and no separate API budget or key has been approved.
- Drive the interactive Claude UI manually. Rejected as the qualification mechanism because it
  would not exercise a deterministic programmatic contract.
- Give Claude Code repository and shell tools. Rejected because provider qualification requires
  model evidence, not autonomous host authority.
- Treat subscription capacity as unlimited or free compute. Rejected because it consumes the
  owner's shared plan allowance and may stop at its usage limit.

## Consequences

- Eldridge can use the owner's existing Claude Code subscription through an explicit provider
  boundary without API-key billing.
- The first real external-model canary is reproducible and contains only synthetic public data.
- Repository-aware generation remains a later, separately scoped vertical slice.
- Activation may fail closed when Claude Code is logged out, its subscription is unavailable, or
  its plan allowance is exhausted.

## Failure behavior

Missing subscription authentication, an API/cloud authentication mode, CLI failure, timeout,
nonzero exit, invalid JSON, and empty structured output all fail without provider fallback. Error
responses do not persist authentication status, account identifiers, command output, or credentials.
