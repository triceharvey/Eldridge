# Local OpenTofu and k3d Toolchain Activation

## Activation evidence

Verified on 2026-09-08 on macOS arm64:

| Component | Version | Installed binary SHA-256 |
|---|---|---|
| OpenTofu | 1.12.6 | `7e12d433dc5f9d22b95fc9d0096b0b6e5e0e379486ad9724aa8e7ba08f9da7f5` |
| k3d | 5.9.0 | `01d4cc53d962698005abc98af9adb3f07bb48b44d534ea8e73968acc986af85e` |
| k3s default bundled by k3d | 1.35.5-k3s1 | Not started or downloaded |

Both tools were installed from Homebrew's stable bottles. Homebrew linkage tests passed. The
installation added no paid service, account, credential, provider plugin, container image, or
cluster.

## Genuine no-apply plan

The built-in-only fixture initialized with `-backend=false -lockfile=readonly`, validated, and
created a saved plan with OpenTofu 1.12.6. No external provider package was required. The plan
reported one local `terraform_data` create, zero changes, and zero destroys. It was not applied.

Observed evidence:

- JSON format: `1.2`;
- OpenTofu compatibility key `terraform_version`: `1.12.6`;
- provider: `terraform.io/builtin/terraform`;
- resource: `terraform_data.eldridge_local`;
- action: `create`;
- checks: passing;
- resource drift, outputs, and variables: absent;
- saved-plan SHA-256 for this run:
  `7133c7c14ea2af86ce0ce35475eb936a6c0810f3d349c3f4ea86be256f03dddf`; and
- empty external-provider lock-manifest SHA-256:
  `971d00d3b0364379c361798f0eab53c2adc2ccd0e6ad4442f3929090c508ef25`.

The saved-plan digest is run-specific and is evidence rather than a golden value. The integration
test regenerates the plan in a temporary directory, validates its current digest, runs the
no-change local adapter, confirms no state file was created, and never invokes `tofu apply`.

## Remaining boundary

k3d is installed but no cluster exists. Creating a cluster will require a separately bounded
test because it pulls a k3s image and changes local Docker state. A real provider remains
disabled until its exact source, version, lock checksums, resource scope, identity, and cleanup
policy are approved.
