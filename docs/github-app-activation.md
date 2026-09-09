# GitHub App Activation Record

The private **Eldridge Control Plane** GitHub App was registered and installed on 2026-09-08.
This record contains only non-secret identifiers and control evidence. It does not
contain the private key, an installation token, a webhook secret, or GitHub account
credentials.

## Registration and installation

| Control | Verified value |
|---|---|
| Owner | `triceharvey` |
| App slug | `eldridge-control-plane` |
| App ID | `4878076` |
| Client ID | `Iv23liW43F2SDTPPFkvA` |
| Installation ID | `160167966` |
| App visibility | Private; installable only on the owning account |
| Repository selection | Only `triceharvey/Eldridge` |
| User OAuth during installation | Disabled |
| Device flow | Disabled |
| Webhook delivery | Disabled pending a real TLS endpoint and separately generated secret |

Repository permissions are limited to:

- Administration: read-only, for independent branch-protection assessment;
- Checks: read-only, for exact-revision CI evidence;
- Contents: read-only, for branch and commit verification;
- Pull requests: read and write, for creating draft proposals and reading their state; and
- Metadata: mandatory read-only access added by GitHub.

The app has no contents-write, merge-specific, workflow, secret, deployment, environment,
package, organization, or user permission. Eldridge implements no merge operation.

## Credential boundary

GitHub reports the retained private-key fingerprint as:

```text
SHA256:XLM7tDxj885TyLscAXiyG4lJJe5FDVOlEcAKWM+C/eI=
```

For private local validation, the corresponding PEM is outside the repository at
`~/.config/eldridge/credentials/github-app-private-key.pem`. The credential directory is mode
`700`, the key is mode `600`, and FileVault is enabled. The downloaded plaintext copy and all
superseded GitHub App keys were removed after validation. The real policy file is ignored by
Git and references the key through `GITHUB_APP_PRIVATE_KEY`; it does not embed the key.

This local arrangement is not the production secret-management design. Before shared or
hosted deployment, rotate the key into the selected platform's secret manager, expose it only
to the trusted worker process, and revoke the local-validation key.

## Live validation

Using Client ID `Iv23liW43F2SDTPPFkvA` and installation `160167966`, the implemented
`GitHubAppClient` successfully requested its narrowed installation token and completed a
read-only pull-request query for `triceharvey/Eldridge`. The client rejected broader
permissions by design. It then verified the exact remote revision and created draft pull
request #2 using pull-request write authority without contents-write or merge authority.
That pull request advanced to ready-for-review only after the owner approved the Phase 4 design
and the Phase 4.1 implementation passed local and hosted CI.

Webhook delivery, protected-branch evidence, human review, and merge reconciliation remain
separate gates. Webhooks will not be enabled against a localhost development server. GitHub's
branch-protection endpoint currently returns HTTP 403 for this private personal repository and
states that a qualifying paid plan or public visibility is required. The readiness client now
turns an unreadable protection response into `branch_protection_unverifiable` and remains
fail-closed. No plan upgrade, visibility change, bypass, or merge was attempted.
