# Terraform and OpenTofu Career Lab

This zero-cost lab teaches a production-style infrastructure-as-code workflow without contacting a
cloud or downloading a provider. The reusable `eldridge_profile` module and its local root cover:

- the standard `main.tf`, `variables.tf`, `outputs.tf`, and `versions.tf` structure;
- strong input types, validation blocks, locals, lifecycle conditions, and module outputs;
- `fmt`, `init`, `validate`, saved plans, JSON plan inspection, and detailed exit codes;
- separate local CLI workspaces and state files;
- controlled change detection after a revision update; and
- explicit destroy and empty-state verification.

Automated tests copy the lab into a temporary directory. Only the built-in `terraform_data` resource
is applied, so no infrastructure, account, credential, provider plugin, or bill is created. The
temporary state is never committed. HCP Terraform is outside this lab and remains unactivated.

Run the complete proof from an activated project environment:

```bash
make terraform-career-lab
```

Treat state as sensitive even in a learning exercise. Never commit a real `terraform.tfstate`, and
never assume Terraform CLI workspaces provide the credential or access-control isolation required
for production environments.
