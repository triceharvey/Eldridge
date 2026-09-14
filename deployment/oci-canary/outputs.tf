output "canary_instance" {
  description = "Non-secret instance identity and network coordinates for DNS and evidence collection."
  value = {
    id         = oci_core_instance.canary.id
    public_ip  = oci_core_instance.canary.public_ip
    private_ip = oci_core_instance.canary.private_ip
  }
}

output "backup_bucket" {
  description = "Private, versioned bucket selected for encrypted backup artifacts."
  value = {
    name      = oci_objectstorage_bucket.backups.name
    namespace = oci_objectstorage_bucket.backups.namespace
  }
}

output "safety_boundary" {
  description = "Cost, capacity, and revision boundary committed into the plan."
  value       = terraform_data.safety_boundary.output
}

output "public_tcp_ports" {
  description = "Complete public ingress allowlist. SSH and PostgreSQL are deliberately absent."
  value       = local.allowed_public_tcp_ports
}
