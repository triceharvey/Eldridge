locals {
  profile = {
    project_name       = var.project_name
    environment        = var.environment
    revision           = var.revision
    monthly_budget_usd = var.monthly_budget_usd
    labels = merge(
      {
        managed_by = "eldridge-career-lab"
        workspace  = terraform.workspace
      },
      var.labels,
    )
  }
}

resource "terraform_data" "profile" {
  input = local.profile

  lifecycle {
    precondition {
      condition     = var.monthly_budget_usd == 0
      error_message = "The learning profile must remain inside the zero-dollar boundary."
    }

    postcondition {
      condition     = self.output.revision == var.revision
      error_message = "The stored learning profile must remain bound to the requested revision."
    }
  }
}
