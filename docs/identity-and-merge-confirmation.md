# OIDC Identity and Authoritative Merge Confirmation

Shared and production deployments require OIDC. The API validates the bearer token signature against one operator-fixed HTTPS JWKS endpoint, permits only RS256, and requires exact issuer, audience, expiration, issued-at, and subject claims. An explicit allowlist maps each stable OIDC subject to an active control-plane principal. Caller-supplied `X-Principal-ID` values are ignored whenever OIDC is active.

Copy `oidc-policy.example.json` to the ignored `oidc-policy.json`, replace the issuer, audience, JWKS URL, and subject mapping with values from the selected identity provider, set `enabled` to `true`, and configure:

```bash
CONTROL_PLANE_ENVIRONMENT=production
CONTROL_PLANE_OIDC_POLICY_FILE=oidc-policy.json
```

Startup fails closed outside `development` and `test` when an active OIDC policy is absent. Keep TLS termination and the OIDC login/token acquisition flow at a trusted ingress or client; the control plane is the relying validation boundary, not an identity provider.

## Merge invariant

The GitHub App has no merge method and never requests `contents:write`. A human merges through GitHub after protected-branch controls pass. The control plane advances `APPROVED` to `MERGED` only when all of the following bind to the same immutable head revision:

1. a durable, confirmed pull-request proposal;
2. a successful readiness assessment covering configured checks and branch protection;
3. a consumed human `MERGE` approval under the current workflow policy; and
4. two read-only GitHub observations showing the expected PR is closed and merged and GitHub's merge-status endpoint confirms it.

The human then calls:

```http
POST /workflows/{workflow_id}/pull-requests/{proposal_id}/confirm-merged
Authorization: Bearer <OIDC token for this API>
Content-Type: application/json

{
  "assessment_id": "<successful assessment id>",
  "idempotency_key": "unique-merge-confirmation-key"
}
```

The request and outcome are durable and auditable. A failed GitHub read leaves the workflow in `APPROVED`; it never guesses that a merge occurred. `GET /workflows/{workflow_id}/merge-confirmations` exposes the evidence trail to authorized auditors.
