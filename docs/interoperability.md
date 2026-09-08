# Claude, Devin, and Windsurf Interoperability

## Integration model

These products expose different authority and lifecycle boundaries, so the control plane uses three integration patterns rather than pretending they are interchangeable chat-completion APIs.

| Product | Control-plane role | Integration boundary | Phase 2 posture |
|---|---|---|---|
| Claude | Model provider | Anthropic Messages API with structured client tool proposals | Adapter implemented, disabled by default |
| Devin | Remote agent runtime | Devin v3 organization sessions using a least-privilege service user | Lifecycle adapter implemented, disabled by default |
| Windsurf Cascade | Human/IDE agent and MCP client | Git handoff plus authenticated control-plane MCP tools | Local Streamable HTTP boundary implemented; headless execution not claimed |

## Claude

Anthropic documents client-side tool use through `tool_use` content blocks and requires the caller to execute the tool separately and return a corresponding result. That matches the control plane: Claude can propose a typed operation, but policy and the isolated executor decide whether it runs. The adapter uses the official Messages endpoint, requires an explicitly configured model and secret reference, requests JSON output, and never interprets Claude text as approval.

The adapter is disabled by default. Enabling it will require a repository data classification, an outbound-data policy, token/cost limits, retention review, and an allowlisted development secret reference. See the [Anthropic tool-use documentation](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview).

## Devin

Devin is represented as a remote runtime because a session has its own lifecycle, repository access, cost units, status, messages, and potential pull requests. The current v3 API supports organization-scoped sessions and service-user RBAC. The adapter creates, polls, and cancels sessions; applies workflow/task/idempotency tags; sets a maximum ACU budget; and never supplies `bypass_approval`, inline session secrets, or organization secret IDs.

Activation requires a Devin organization ID and a service user restricted to the minimum session permissions. A personal token or enterprise-wide administrator token is not the default. See Devin's [v3 migration guide](https://docs.devin.ai/api-reference/getting-started/migration-guide), [common flows](https://docs.devin.ai/api-reference/common-flows), and [RBAC reference](https://docs.devin.ai/api-reference/v3/overview).

## Windsurf

Windsurf documents Cascade as an interactive coding agent and MCP client. Its documented Enterprise API focuses on usage analytics and configuration rather than a general headless Cascade-session lifecycle. Therefore the control plane does not claim direct remote execution that the public interface does not establish.

The implemented integration is a digest-bound handoff containing workflow, task, opaque repository scope, branch, immutable base revision, assigned principal, policy version, writable paths, objective, and required return evidence. Its Streamable HTTP server exposes exactly three tools: claim one explicitly named ready implementation task, renew that task's lease, and submit revision-bound implementation evidence. It cannot list the queue, claim planning or review work, invoke general workflow transitions, approve, merge, or deploy.

The server binds one configured MCP identity to `windsurf-cascade`, requires an exact bearer token using constant-time comparison, rejects duplicate authorization headers, limits request bodies, and listens only on localhost. Claiming replaces the scheduled implementer's active grant with a narrow integration grant for heartbeat and evidence return. Git independently verifies that the submitted revision is the named branch head, descends from the handoff base, has changes, and touches only operator-registered writable paths. Reported changed files must exactly match Git. A Cascade test claim is stored as non-authoritative; the workflow still schedules its independent `TEST`, security-review, and code-review stages.

Windsurf currently documents stdio, Streamable HTTP, and SSE support, including headers and environment interpolation for remote HTTP MCP configuration. A local configuration can therefore use:

```json
{
  "mcpServers": {
    "eldridge-control-plane": {
      "serverUrl": "http://127.0.0.1:8010/mcp",
      "headers": {
        "Authorization": "Bearer ${env:CONTROL_PLANE_WINDSURF_MCP_BEARER_TOKEN}"
      }
    }
  }
}
```

The static bearer token and fixed local identity are Phase 2 development controls, not production authentication. Any shared or remote deployment must replace them with OIDC/OAuth identity, TLS, audience and scope validation, credential rotation, and an explicit Host/Origin allowlist. See Windsurf's [Cascade MCP documentation](https://docs.windsurf.com/windsurf/cascade/mcp), the official MCP Python SDK's [ASGI/Streamable HTTP guidance](https://py.sdk.modelcontextprotocol.io/run/asgi/), and its [deployment security guidance](https://py.sdk.modelcontextprotocol.io/run/deploy/).

## Common enforcement

No integration may:

- create or impersonate human approval;
- enable provider-side approval bypass;
- receive a privileged secret merely because it requests one;
- write outside its assigned branch/worktree or repository scope;
- send repository data to an unapproved provider destination;
- silently fall back to a different provider; or
- advance workflow state using an unvalidated prose claim.

Provider and runtime credentials are referenced by opaque names and resolved only after policy authorization. The runtime can load the strict, versioned activation document described in the [provider activation runbook](provider-activation.md); without that explicit file it still uses only mock providers. The current adapters accept injected HTTP clients for deterministic contract testing.

Devin remains lifecycle-only in Phase 2. It is intentionally excluded from ordinary task routing until Phase 3 can bind a remote commit or pull request to independently ingested CI and validation evidence.

## Strength-aware routing

The control plane now has a provider-neutral capability router and task-strategy contract. It represents Claude as a model, Devin as a remote agent, and Windsurf as an IDE handoff, then filters candidates by capability, health, activation, data classification, approved egress, execution mode, cost, and review independence before considering performance. Quality is learned per task capability from validation outcomes; it is not hard-coded from vendor claims.

Complex work may request two independent candidate producers, while high-risk work requires proven evidence and two reviewers outside the producing provider family. Obfuscated or suspicious input is contained before any provider receives it. See [Capability routing and adversarial-input strategy](capability-routing.md).
