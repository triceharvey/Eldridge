# Activation Preflight

This checklist is the boundary between the locally verified implementation and external activation. Complete it in order so hosted evidence remains attributable and reproducible.

## Repository activation

- [x] Configure the repository-local Git author name and GitHub `noreply` email.
- [x] Review the staged initial source tree.
- [x] Create the initial commit on `main`.
- [x] Create `triceharvey/Eldridge` as a private GitHub repository.
- [x] Authenticate GitHub CLI and add the selected repository as `origin`.
- [x] Push `main` and preserve the first successful hosted CI run as evidence.
- [x] Rewrite the private branch history and current refs to remove the original commit email.
- [x] Verify the rewritten tree and commit count match the pre-rewrite repository.
- [ ] Resolve GitHub's retained pre-rewrite commit object before public visibility.

## GitHub security activation

- [ ] Install the least-authority GitHub App for the selected repository.
- [ ] Configure the webhook secret and deliver a signed test event.
- [ ] Protect `main` from direct pushes and require exact-revision checks.
- [ ] Configure the real OIDC issuer, audience, subject mapping, and operator roles.
- [ ] Exercise one human-approved draft pull request from creation through merge reconciliation.

## Runtime activation

- [ ] Select the non-production hosting target and managed PostgreSQL service.
- [ ] Publish API and worker images by immutable digest and retain scan evidence.
- [ ] Configure DNS and verify automatic TLS at the public endpoint.
- [ ] Configure authenticated Prometheus scraping and verify the operations dashboard.
- [ ] Perform and document a PostgreSQL backup and restore exercise.

No external repository, paid provider, cloud resource, DNS record, or production credential should be created from this checklist without the project owner's explicit target and approval.
