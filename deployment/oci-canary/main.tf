locals {
  allowed_public_tcp_ports = [80, 443]
  common_tags = merge(
    {
      managed_by       = "eldridge-opentofu"
      environment      = "canary"
      release_revision = var.release_revision
      cost_profile     = "oci-always-free"
    },
    var.freeform_tags,
  )
}

resource "terraform_data" "safety_boundary" {
  input = {
    region                     = var.region
    release_revision           = var.release_revision
    monthly_budget_usd         = var.monthly_budget_usd
    temporary_total_budget_usd = var.temporary_total_budget_usd
    shape                      = "VM.Standard.A1.Flex"
    ocpus                      = 2
    memory_gb                  = 12
    boot_volume_gb             = 50
  }

  lifecycle {
    precondition {
      condition     = var.confirmed_home_region
      error_message = "The operator must confirm that region is the OCI tenancy home region."
    }

    precondition {
      condition     = var.confirmed_always_free_eligibility
      error_message = "The operator must confirm Always Free eligibility in the OCI console before planning."
    }

    precondition {
      condition     = var.monthly_budget_usd == 0 && var.temporary_total_budget_usd <= 5
      error_message = "The plan exceeds Eldridge's authorized hosted-cost boundary."
    }
  }
}

resource "oci_core_vcn" "canary" {
  compartment_id = var.compartment_id
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "eldridge-canary-vcn"
  dns_label      = "eldridge"
  freeform_tags  = local.common_tags

  lifecycle {
    precondition {
      condition     = terraform_data.safety_boundary.output.monthly_budget_usd == 0
      error_message = "VCN creation requires the accepted zero-dollar safety boundary."
    }
  }
}

resource "oci_core_internet_gateway" "canary" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.canary.id
  display_name   = "eldridge-canary-internet-gateway"
  enabled        = true
  freeform_tags  = local.common_tags
}

resource "oci_core_route_table" "edge" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.canary.id
  display_name   = "eldridge-canary-edge-routes"
  freeform_tags  = local.common_tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.canary.id
  }
}

resource "oci_core_security_list" "edge" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.canary.id
  display_name   = "eldridge-canary-edge-security"
  freeform_tags  = local.common_tags

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  dynamic "ingress_security_rules" {
    for_each = toset(local.allowed_public_tcp_ports)
    content {
      protocol = "6"
      source   = "0.0.0.0/0"

      tcp_options {
        max = ingress_security_rules.value
        min = ingress_security_rules.value
      }
    }
  }
}

resource "oci_core_subnet" "edge" {
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.canary.id
  cidr_block                 = var.subnet_cidr
  display_name               = "eldridge-canary-edge-subnet"
  dns_label                  = "edge"
  prohibit_public_ip_on_vnic = false
  route_table_id             = oci_core_route_table.edge.id
  security_list_ids          = [oci_core_security_list.edge.id]
  freeform_tags              = local.common_tags
}

resource "oci_core_instance" "canary" {
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_id
  display_name        = "eldridge-canary"
  shape               = "VM.Standard.A1.Flex"
  freeform_tags       = local.common_tags

  shape_config {
    ocpus         = 2
    memory_in_gbs = 12
  }

  create_vnic_details {
    assign_public_ip = true
    display_name     = "eldridge-canary-edge-vnic"
    hostname_label   = "control"
    subnet_id        = oci_core_subnet.edge.id
  }

  instance_options {
    are_legacy_imds_endpoints_disabled = true
  }

  source_details {
    source_id               = var.image_id
    source_type             = "image"
    boot_volume_size_in_gbs = 50
  }

  metadata = {
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
      release_revision = var.release_revision
    }))
  }

  depends_on = [terraform_data.safety_boundary]
}

resource "oci_objectstorage_bucket" "backups" {
  compartment_id = var.compartment_id
  namespace      = var.object_storage_namespace
  name           = var.backup_bucket_name
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  versioning     = "Enabled"
  freeform_tags  = local.common_tags

  depends_on = [terraform_data.safety_boundary]
}
