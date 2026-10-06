# Local Video Candidate Review

The `audit video-candidate` command binds a local motion candidate to a previously
verified, creator-approved still and a limited, separate-model review. It does not
generate video, upload media, spend credits, approve motion, or authorize release.

```sh
.venv/bin/control-plane audit video-candidate \
  --source-manifest approved-still-evidence.json \
  --manifest video-candidate.json \
  --assessment reviewer-assessment.json \
  --repository-registry repository-registry.json \
  --verify-frames \
  --runway-job-evidence runway-job-evidence.json
```

The source manifest and registry must pin the same Git revision and approved-still
digest. The candidate manifest names an MP4-family file inside that registered
repository, an expected SHA-256, a provider artifact ID **claim**, duration and
credit **claims**, plus 3–24 sampled PNG/JPEG frames with timestamps and hashes.
Samples must include the opening, middle, and ending. The reviewer assessment must
name a different model family than the producer, match the candidate and every
sample digest, and provide concrete findings. A rejection requires a blocking
finding. All files are read-only during the check.

By default the command verifies source Git bytes and local candidate/frame hashes
and checks only an MP4-family header. `--verify-frames` explicitly re-decodes every
sample from the exact video bytes using the bundled macOS AVFoundation PNG v1
extractor in an isolated temporary directory. It verifies the generated PNG hashes
against the archived frame hashes, requires requested and decoded timestamps to
be within 50 ms, and reports the extractor's SHA-256. The command fails closed on
missing Swift/macOS support, a decoding error, or a mismatch. Temporary decoded
frames are removed afterward; repository files are not changed. PNG bytes may
depend on the operating-system decoder version, so a cross-platform mismatch is
not automatically proof of tampering. The check samples frames, not full playback.

An optional browser-observed Runway receipt binds contemporaneous captures to the
same video hash, artifact ID, duration claim, and credit claim. The receipt format is:

```json
{
  "schema_version": "1",
  "capture_method": "BROWSER_OBSERVED",
  "provider_artifact_id_claim": "the-visible-job-id",
  "video_sha256": "sha256:<64 lowercase hex digits>",
  "model_name_claim": "Gen-4 Turbo",
  "duration_ms_claim": 5000,
  "credits_charged_claim": 25,
  "captures": [
    {
      "kind": "JOB_DETAILS",
      "path": "audits/job-details.png",
      "sha256": "sha256:<64 lowercase hex digits>"
    }
  ]
}
```

Capture paths must resolve inside the registered repository. Supported kinds are
`JOB_DETAILS`, `GENERATION_SETTINGS`, `CREDIT_LEDGER`, and `DOWNLOAD_RECORD`.
Capture the relevant Runway view at generation/download time, record the exact
local file hashes, and preserve the files without alteration. The receipt proves
only that those local capture bytes match the manifest; it cannot authenticate
Runway, independently verify the job ID or charged credits, or prove the capture
was not edited before hashing. A provider-authenticated readback would need a
separate integration and an explicit access/cost decision.

Nor does this command verify reviewer identity, model invocation, observation
accuracy, or creator approval. A human must still watch the complete video and
decide on that exact candidate digest. Keep rejected clips as evidence; do not
silently promote them to an approved motion master.
