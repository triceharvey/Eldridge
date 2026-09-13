output "profile" {
  description = "Validated, non-sensitive learning profile stored by the built-in resource."
  value       = terraform_data.profile.output
}

output "profile_digest" {
  description = "Deterministic SHA-256 digest of the validated profile metadata."
  value       = sha256(jsonencode(terraform_data.profile.output))
}
