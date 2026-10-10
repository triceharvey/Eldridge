# Portable Audit and Quality-Evidence Bundles

Eldridge can export one workflow's project binding, task and attempt outcomes, artifact digests,
CI check evidence, and complete hash-chained audit history as a portable JSON bundle. Model output,
credential material, lease tokens, and repository contents are deliberately excluded.

```sh
.venv/bin/control-plane audit export \
  --database-url postgresql+psycopg://... \
  --workflow-id WORKFLOW_ID \
  --output workflow-audit.json

.venv/bin/control-plane audit verify --bundle workflow-audit.json
```

The export refuses a missing workflow, a broken database audit chain, an existing output path, or
a missing output directory. Verification checks both the bundle digest and every event in the
embedded per-workflow hash chain. Record the printed `sha256:` digest in an independently protected
system, such as a protected pull request, release record, or write-once archive.

The digest proves integrity only relative to a trusted copy of that fingerprint. Anyone able to
replace both the bundle and its recorded fingerprint can manufacture a new internally consistent
file. Production use therefore still requires access-controlled export, encryption appropriate to
the workflow's data classification, retention policy, and an external immutable or signed record. The detached signatures below cover
the signed-record part.

## Detached signatures

A signature moves the trust anchor from a protected copy of the digest to a public key. Generate an
Ed25519 key pair once, keep the private key outside the repository and the control plane, and
publish the public key where reviewers can find it:

```sh
openssl genpkey -algorithm ed25519 -out audit-signing.pem
chmod 600 audit-signing.pem
openssl pkey -in audit-signing.pem -pubout -out audit-signing.pub.pem

.venv/bin/control-plane audit sign \
  --bundle workflow-audit.json \
  --private-key audit-signing.pem \
  --output workflow-audit.sig.json

.venv/bin/control-plane audit verify \
  --bundle workflow-audit.json \
  --signature workflow-audit.sig.json \
  --public-key audit-signing.pub.pem
```

Signing refuses a bundle that does not verify and a key file readable by group or others. The
envelope binds the bundle digest, workflow ID, key ID, and signing time; changing any of them, or
rewriting the bundle and recomputing its digest, makes verification fail. A signature attests that
the key holder approved this export; it does not prove the database was untouched before export.
See ADR-0043.

For KAiJU Frenchies and other projects, use a distinct repository scope and workflow for each
change. The bundle then provides a portable record of which project, revision, policy, model stages,
artifact digests, CI results, and human-controlled state transitions produced the candidate.
