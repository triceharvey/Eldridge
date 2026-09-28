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
the workflow's data classification, retention policy, and an external immutable or signed record.

For KAiJU Frenchies and other projects, use a distinct repository scope and workflow for each
change. The bundle then provides a portable record of which project, revision, policy, model stages,
artifact digests, CI results, and human-controlled state transitions produced the candidate.
