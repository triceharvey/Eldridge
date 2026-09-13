variable "project_name" {
  type        = string
  description = "Project identifier passed to the reusable profile module."
}

variable "environment" {
  type        = string
  description = "Non-production environment passed to the reusable profile module."
}

variable "revision" {
  type        = string
  description = "Exact Git commit passed to the reusable profile module."
}

variable "monthly_budget_usd" {
  type        = number
  description = "Zero-dollar ceiling passed to the reusable profile module."
  default     = 0
}

variable "labels" {
  type        = map(string)
  description = "Non-sensitive metadata passed to the reusable profile module."
  default     = {}
}
