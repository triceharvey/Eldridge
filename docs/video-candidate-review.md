# Local Video Candidate Review

The `audit video-candidate` command binds a local motion candidate to a previously
verified, creator-approved still and a limited, separate-model review. It does not
generate video, upload media, spend credits, approve motion, or authorize release.

```sh
.venv/bin/control-plane audit video-candidate \
  --source-manifest approved-still-evidence.json \
  --manifest video-candidate.json \
  --assessment reviewer-assessment.json \
  --repository-registry repository-registry.json
```

The source manifest and registry must pin the same Git revision and approved-still
digest. The candidate manifest names an MP4-family file inside that registered
repository, an expected SHA-256, a provider artifact ID **claim**, duration and
credit **claims**, plus 3–24 sampled PNG/JPEG frames with timestamps and hashes.
Samples must include the opening, middle, and ending. The reviewer assessment must
name a different model family than the producer, match the candidate and every
sample digest, and provide concrete findings. A rejection requires a blocking
finding. All files are read-only during the check.

The command verifies source Git bytes and local candidate/frame hashes. It checks
only an MP4-family header; it does **not** decode the video or independently prove
the sampled images came from it. Nor can it authenticate the provider job, charged
credits, reviewer identity, model invocation, observation accuracy, or creator's
decision. Its report marks each of these limits explicitly. A human must still
watch the complete video and decide on that exact candidate digest. Keep rejected
clips as evidence; do not silently promote them to an approved motion master.
