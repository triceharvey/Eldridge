# ADR-0043: Sign portable audit bundles with detached Ed25519 signatures

- Status: Proposed (awaiting owner approval)
- Date: 2026-10-10

## Context

`control-plane audit export` produces a bundle whose `sha256:` digest proves integrity only
relative to a trusted copy of that digest (see `docs/audit-exports.md`). Anyone able to replace the
bundle and its recorded fingerprint can manufacture an internally consistent file. The audit
documentation already names "an external immutable or signed record" as a production requirement.

## Decision

Add an explicit operator step, `control-plane audit sign`, that writes a detached Ed25519 signature
envelope for a bundle that already passes `audit verify`. The envelope signs the bundle digest,
workflow ID, signing-key ID (SHA-256 of the raw public key), schema version, algorithm, and signing
time. `audit verify --signature --public-key` checks the bundle, its binding to the envelope, the
key ID, and the signature together.

The private key is read from an operator-held PEM file that must not be group- or world-readable.
It is never stored in the database, configuration, or runtime, and no service process signs.
Signing uses `cryptography`, which was already installed through `pyjwt[crypto]`; it is now declared
directly because Eldridge imports it.

## Alternatives

- Sigstore keyless signing gives a public transparency log but requires network access and an
  identity provider, which conflicts with the USD 0 offline default.
- GPG signatures are familiar but add a system binary and a keyring to the trust path.
- An HMAC requires the verifier to hold the signing secret, so a verifier could forge.

## Consequences

Reviewers can verify a bundle against a published public key instead of a separately protected
digest. Key custody, rotation, and revocation remain operator responsibilities; the envelope
records a key ID so a revoked key can be recognized, but Eldridge does not maintain a revocation
list. A signature attests that the key holder approved the export, not that the underlying
database was never rewritten before export (ADR-0006 still applies).

## Failure behavior

Signing refuses a bundle that fails verification, a non-Ed25519 or encrypted key, a key file
readable by group or others, an existing output path, and a missing output directory. Verification
returns exit code 2 when any check fails and refuses to run with only one of `--signature` and
`--public-key`.
