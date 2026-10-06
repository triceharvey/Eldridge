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
credit **claims**, plus 3–24 sampled PNG frames with timestamps and hashes.
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
same video hash, artifact ID, duration claim, and credit claim. Prepare it during
the **next separately authorized** generation; this workflow does not itself
authorize a generation or credit spend:

1. Before generating, confirm the exact source-plate digest, permitted Runway
   model, maximum existing-credit spend, and creator's generation approval. Save
   the pre-generation credit balance as a local screenshot when available.
2. After the job completes, save local screenshots of the job details (including
   visible job ID), final generation settings, and credit ledger/debit. Download
   the original video and preserve its bytes. Do not include passwords, session
   tokens, cookies, or HAR files in the capture package.
3. Create the video candidate manifest with the downloaded video hash and
   observed job/duration/credit **claims**. Put screenshots inside its registered
   repository. Then run the receipt builder below, using repository-relative
   screenshot paths. It refuses missing/duplicate evidence, non-images, and
   overwriting an existing receipt.

```sh
.venv/bin/control-plane capture runway-receipt \
  --manifest video-candidate.json \
  --repository-registry repository-registry.json \
  --model-name 'Gen-4 Turbo' \
  --job-details audits/runway-job.png \
  --generation-settings audits/runway-settings.png \
  --credit-ledger audits/runway-credit-debit.png \
  --credit-balance-before audits/runway-credit-before.png \
  --output audits/runway-job-evidence.json
```

Finally, run `audit video-candidate` with `--runway-job-evidence` and
`--verify-frames` as shown above. A receipt has this format:

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
      "path": "audits/runway-job.png",
      "sha256": "sha256:<64 lowercase hex digits>"
    },
    {
      "kind": "GENERATION_SETTINGS",
      "path": "audits/runway-settings.png",
      "sha256": "sha256:<64 lowercase hex digits>"
    },
    {
      "kind": "CREDIT_LEDGER",
      "path": "audits/runway-credit-debit.png",
      "sha256": "sha256:<64 lowercase hex digits>"
    }
  ]
}
```

Capture paths must resolve inside the registered repository. `JOB_DETAILS`,
`GENERATION_SETTINGS`, and `CREDIT_LEDGER` are all required; `CREDIT_BALANCE_BEFORE`
and `DOWNLOAD_RECORD` images are optional. Preserve the capture files without
alteration. The receipt proves
only that those local capture bytes match the manifest; it cannot authenticate
Runway, independently verify the job ID or charged credits, or prove the capture
was not edited before hashing. The auditor does not OCR the screenshots. A
provider-authenticated readback would need a
separate integration and an explicit access/cost decision.

Nor does this command verify reviewer identity, model invocation, observation
accuracy, or creator approval. A human must still watch the complete video and
decide on that exact candidate digest. Keep rejected clips as evidence; do not
silently promote them to an approved motion master.
