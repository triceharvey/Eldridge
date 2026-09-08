# GitHub CI Evidence Integration

Phase 3 begins with a narrow inbound trust boundary for GitHub `check_run` webhooks. This slice records what GitHub reported about an exact commit; it does not create pull requests, merge branches, change branch protection, or advance workflow state.

## Security properties

- The endpoint is absent operationally unless `CONTROL_PLANE_GITHUB_WEBHOOK_ENABLED=true` and a webhook secret is configured.
- `X-Hub-Signature-256` is verified over the unmodified request bytes with HMAC-SHA256 and constant-time comparison before JSON parsing or database lookup.
- Only `check_run` events are accepted. Non-completed check runs are acknowledged without becoming evidence.
- Completed runs require a terminal conclusion and a 40- or 64-character hexadecimal commit digest.
- Repository full name and `head_sha` must match exactly one workflow's registered repository scope and candidate revision. Missing or ambiguous matches fail closed.
- `X-GitHub-Delivery` is idempotent. Replaying the same signed body returns the existing record; reusing a delivery ID with different content is rejected.
- The raw webhook body, logs, annotations, and source code are not retained. The control plane stores bounded metadata and a SHA-256 payload digest.
- Success and failure conclusions are both retained. A received check is explicitly `gate_eligible: false` until required-check and branch-protection verification are implemented.
- The webhook identity has only `SUBMIT_CI_EVIDENCE`; it cannot approve, merge, deploy, lease tasks, or invoke model tools.

## GitHub configuration

Create a high-entropy webhook secret outside source control, configure that same value in the process environment, subscribe only to **Check runs**, and send deliveries to:

```text
POST /integrations/github/webhook
```

The repository name in GitHub must match the workflow's `repository_scope`, for example `owner/repository`. Configure TLS and a public ingress boundary before receiving real internet traffic; the development server remains localhost-only and is not an appropriate public webhook receiver.

Read normalized evidence with:

```text
GET /ci-checks?workflow_id=<workflow-id>
```

This read path uses the existing audit-reader authorization boundary.

## Next slice

The least-privilege GitHub App client is now implemented behind `github-app-policy.example.json`. It performs two bounded operations:

1. Verify the remote head branch equals the expected immutable candidate revision, then create a draft pull request with maintainer modification disabled.
2. Re-read a pull request, protected base branch, and exact-revision check runs to produce a structured merge-readiness assessment.

Installation tokens are restricted to one configured repository. Pull-request creation requests `pull_requests: write`, `checks: read`, `administration: read`, and `contents: read`. Readiness assessment further narrows pull-request access to read. The client rejects a returned token that has `contents: write` or any unexpected write permission, and it implements no merge method.

Required check names, base branch, minimum approving reviews, administrator enforcement, and linear-history requirements come from operator policy—not GitHub webhook content or an agent prompt. Readiness also requires stale-review dismissal, approval of the last push, disabled force pushes, and disabled branch deletion.

Pull-request proposals and readiness assessments are now bound to durable, human-authorized workflow commands and audit records. The proposal is committed as `RUNNING` before GitHub is called. An ambiguous failure becomes `UNKNOWN` and cannot be automatically retried; an authorized human invokes read-only reconciliation, which either binds the exact remote PR or records that none exists. Readiness snapshots are idempotent and rejected if the workflow candidate revision changes.

The next slice is OIDC-backed human identity and authoritative merge confirmation. A real GitHub App installation and repository remain separately approved external changes.
