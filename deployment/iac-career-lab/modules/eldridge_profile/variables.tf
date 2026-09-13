variable "project_name" {
  type        = string
  description = "Lowercase project identifier used in deterministic profile metadata."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.project_name))
    error_message = "project_name must be 3-31 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "environment" {
  type        = string
  description = "Bounded non-production environment represented by this learning profile."

  validation {
    condition     = contains(["development", "test", "staging"], var.environment)
    error_message = "environment must be development, test, or staging."
  }
}

variable "revision" {
  type        = string
  description = "Exact lowercase Git commit bound to this profile."

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.revision))
    error_message = "revision must be an exact 40-character lowercase hexadecimal Git commit."
  }
}

variable "monthly_budget_usd" {
  type        = number
  description = "Maximum incremental monthly infrastructure cost permitted by this lab."
  default     = 0

  validation {
    condition     = var.monthly_budget_usd == 0
    error_message = "The local career lab requires a monthly_budget_usd value of zero."
  }
}

variable "labels" {
  type        = map(string)
  description = "Non-sensitive ownership and purpose metadata attached to the profile."
  default     = {}

  validation {
    condition = alltrue([
      for key, value in var.labels :
      can(regex("^[a-z][a-z0-9_-]{1,30}$", key)) && length(trimspace(value)) > 0
    ])
    error_message = "Label keys must be 2-31 lowercase characters and values must not be empty."
  }
}
