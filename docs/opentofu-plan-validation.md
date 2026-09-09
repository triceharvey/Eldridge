# OpenTofu Saved-Plan Validation

## Implemented boundary

The zero-cost Phase 4.2 slice validates machine-readable evidence produced from an immutable
OpenTofu saved plan. It does not run OpenTofu, start a subprocess, create a Docker or k3d
resource, contact a registry or provider, issue a credential, or apply infrastructure.

The validator enforces:

- a supported OpenTofu JSON format major version and an explicitly pinned CLI version;
- allowlisted provider names, full provider sources, and exact version constraints;
- allowlisted managed-resource types and exact resource addresses;
- a bounded change count and JSON input size;
- only explicitly allowed actions, with delete, replacement, forget, import, generated
  configuration, moved/deposed resources, provisioners, child modules, and drift rejected;
- passing checks and no output changes;
- no variable values or sensitive-value markers in the validation artifact;
- lowercase SHA-256 bindings for the binary saved plan and dependency lock file; and
- the approved USD 0 monthly infrastructure ceiling.

Successful validation returns sanitized evidence containing only policy/version bindings,
digests, estimated cost, and resource/action summaries. It does not return resource values,
variables, outputs, or credential material. The local adapter can simulate acceptance of that
evidence while proving that no subprocess started, no external target was contacted, and no
change occurred.

## Sensitive plan handling

`tofu show -json` can expose sensitive values in plaintext. A future real extractor must run
inside the isolated execution boundary, keep the binary and JSON plan out of logs and API
responses, pass only sanitized evidence to the control plane, and destroy its temporary files
after hashing and validation. Rejecting a sensitive marker prevents approval; it does not make
an already exposed JSON artifact safe.

## Remaining activation work

OpenTofu 1.12.0 and provider packages must be obtained through a separately reviewed,
checksum-verifying installation path before a real fixture is generated. The current machine
has Docker and kubectl but does not have OpenTofu or k3d installed. Installation, provider
download, cluster creation, and real apply are intentionally deferred to a later explicit
local-tooling step.

The exact dependency lock file must be committed and reviewed. A real execution adapter must
apply the approved binary saved plan rather than generating a new plan, obtain workload identity
only after approval consumption, and persist issuance and verification metadata without secrets.

## Authoritative references

- [OpenTofu JSON output format](https://opentofu.org/docs/internals/json-format/)
- [OpenTofu `show` command and sensitive-output warning](https://opentofu.org/docs/cli/commands/show/)
- [OpenTofu dependency lock file](https://opentofu.org/docs/language/files/dependency-lock/)
