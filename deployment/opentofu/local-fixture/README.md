# Local OpenTofu Validation Fixture

This fixture uses only OpenTofu's built-in `terraform_data` resource. Initialization requires
no external provider package, and planning cannot create infrastructure. The pinned empty lock
file documents that the fixture has no external provider dependency.

The controlled validation sequence is:

```bash
tofu init -backend=false -lockfile=readonly
tofu validate
tofu plan -refresh=false -out=eldridge-local.tfplan
tofu show -json eldridge-local.tfplan
```

Do not apply this fixture through a generic shell path. The saved plan and JSON output are
temporary sensitive artifacts even though this fixture intentionally contains no secrets.
Future execution must occur through the typed Eldridge adapter after exact plan approval.
