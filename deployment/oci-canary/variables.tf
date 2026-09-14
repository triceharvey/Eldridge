variable "region" {
  type        = string
  description = "OCI home region confirmed by the operator."

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.region))
    error_message = "region must be an OCI region identifier such as us-phoenix-1."
  }
}

variable "compartment_id" {
  type        = string
  description = "OCID of the dedicated Eldridge canary compartment."

  validation {
    condition     = can(regex("^ocid1\\.compartment\\.", var.compartment_id))
    error_message = "compartment_id must be an OCI compartment OCID."
  }
}

variable "availability_domain" {
  type        = string
  description = "Availability domain in the confirmed home region with A1 capacity."

  validation {
    condition     = length(trimspace(var.availability_domain)) > 0
    error_message = "availability_domain must not be empty."
  }
}

variable "image_id" {
  type        = string
  description = "OCID of an Always Free eligible Arm64 Ubuntu image."

  validation {
    condition     = can(regex("^ocid1\\.image\\.", var.image_id))
    error_message = "image_id must be an OCI image OCID."
  }
}

variable "object_storage_namespace" {
  type        = string
  description = "OCI Object Storage namespace for the canary backup bucket."

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,100}$", var.object_storage_namespace))
    error_message = "object_storage_namespace contains unsupported characters."
  }
}

variable "backup_bucket_name" {
  type        = string
  description = "Unique, non-sensitive Object Storage bucket name."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,62}$", var.backup_bucket_name))
    error_message = "backup_bucket_name must be 3-63 lowercase letters, digits, or hyphens."
  }
}

variable "release_revision" {
  type        = string
  description = "Exact lowercase Git revision represented by this canary plan."

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.release_revision))
    error_message = "release_revision must be an exact 40-character lowercase Git commit."
  }
}

variable "confirmed_home_region" {
  type        = bool
  description = "Explicit acknowledgement that region is the tenancy home region."
  default     = false
}

variable "confirmed_always_free_eligibility" {
  type        = bool
  description = "Explicit acknowledgement that every selected item is marked Always Free eligible."
  default     = false
}

variable "monthly_budget_usd" {
  type        = number
  description = "Maximum recurring infrastructure cost allowed by this profile."
  default     = 0

  validation {
    condition     = var.monthly_budget_usd == 0
    error_message = "The OCI canary profile requires a monthly_budget_usd value of zero."
  }
}

variable "temporary_total_budget_usd" {
  type        = number
  description = "Separately approved maximum for a temporary hosted exercise."
  default     = 5

  validation {
    condition     = var.temporary_total_budget_usd >= 0 && var.temporary_total_budget_usd <= 5
    error_message = "temporary_total_budget_usd must remain between zero and five dollars."
  }
}

variable "vcn_cidr" {
  type        = string
  description = "Private network range for the canary."
  default     = "10.42.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vcn_cidr))
    error_message = "vcn_cidr must be a valid IPv4 CIDR."
  }
}

variable "subnet_cidr" {
  type        = string
  description = "Public edge subnet range; only ports 80 and 443 are admitted."
  default     = "10.42.10.0/24"

  validation {
    condition     = can(cidrnetmask(var.subnet_cidr))
    error_message = "subnet_cidr must be a valid IPv4 CIDR."
  }
}

variable "freeform_tags" {
  type        = map(string)
  description = "Additional non-sensitive ownership tags."
  default     = {}

  validation {
    condition = alltrue([
      for key, value in var.freeform_tags :
      length(trimspace(key)) > 0 && length(trimspace(value)) > 0
    ])
    error_message = "freeform tag keys and values must not be empty."
  }
}
