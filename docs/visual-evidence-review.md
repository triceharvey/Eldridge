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

For a rejected candidate, a reviewer can bind actionable critique to that same image with a
separate assessment JSON:

```sh
.venv/bin/control-plane audit visual-review \
  --manifest visual-evidence.json \
  --assessment visual-review-assessment.json \
  --repository-registry repository-registry.json
```

The assessment names the exact repository scope, commit, asset ID, and SHA-256, plus a shot ID,
claimed reviewer, and one or more findings. Each finding declares a category, severity, source
(`CREATOR_FEEDBACK` or `REVIEWER_VISUAL_OBSERVATION`), concrete observation, image location, and
proposed retry direction. At least one blocking finding is required, and the image must have a
declared `REJECTED_...` status. The command re-verifies the image bytes before returning a read-only
retry packet. It neither sends images to a model nor drives Runway.

Only image identity is independently verified by this command. Reviewer identity, whether a
reviewer truly viewed the frame, the visual correctness of a finding, and the manifest's declared
status remain supplied claims. The packet is not a creator approval, production plate, Runway
prompt execution, or release authorization. A separate reviewer and creator decision are still
needed before promotion. This is the first evidence-binding slice of a visible production loop,
not a completed autonomous visual-quality system.
