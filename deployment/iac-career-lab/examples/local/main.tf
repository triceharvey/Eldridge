module "eldridge_profile" {
  source = "../../modules/eldridge_profile"

  project_name       = var.project_name
  environment        = var.environment
  revision           = var.revision
  monthly_budget_usd = var.monthly_budget_usd
  labels             = var.labels
}
