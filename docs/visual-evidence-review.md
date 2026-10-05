# Local Visual Evidence Review

Eldridge can verify local images for a human review without sending image bytes to a model or
changing an approval state. The operator supplies a private manifest with one registered repository
scope, an immutable Git commit, and up to 16 image paths and expected SHA-256 digests. Run:

```sh
.venv/bin/control-plane audit visual-evidence \
  --manifest visual-evidence.json \
  --repository-registry repository-registry.json
```

The command checks that the registry and manifest name the same commit, each image is a regular file
inside the repository, its current bytes equal the committed Git blob, and its SHA-256 matches the
manifest. It accepts local JPEG, PNG, and WebP headers, with a 32 MiB limit per image. It returns
absolute local paths and verified digests for a Codex reviewer to display. No image is copied,
uploaded, transformed, sent to Claude, or approved by this command.

The `declared_status` field is an operator-supplied label, not a verified decision. The reviewer
must display the exact verified image to the human and keep the digest beside it. An explicit human
decision should identify the asset and digest; recording that decision and any later animation,
canon, publication, or deployment authority are separate steps. A successful image check does not
prove visual quality, rights, provenance of uncommitted references, or creator approval. If the
working image changes after inspection, rerun the command before relying on the displayed path.
