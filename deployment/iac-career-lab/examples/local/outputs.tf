output "profile" {
  description = "Validated learning profile returned by the reusable module."
  value       = module.eldridge_profile.profile
}

output "profile_digest" {
  description = "Deterministic digest returned by the reusable module."
  value       = module.eldridge_profile.profile_digest
}
