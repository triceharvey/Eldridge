mock_provider "oci" {}

variables {
  region                            = "us-phoenix-1"
  compartment_id                    = "ocid1.compartment.oc1..test"
  availability_domain               = "TEST:PHX-AD-1"
  image_id                          = "ocid1.image.oc1.phx.test"
  object_storage_namespace          = "eldridgetest"
  backup_bucket_name                = "eldridge-canary-test-backups"
  release_revision                  = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  confirmed_home_region             = true
  confirmed_always_free_eligibility = true
  monthly_budget_usd                = 0
  temporary_total_budget_usd        = 5
}

run "always_free_plan" {
  command = plan

  assert {
    condition     = oci_core_instance.canary.shape == "VM.Standard.A1.Flex"
    error_message = "The canary must use the OCI Always Free Ampere A1 shape."
  }

  assert {
    condition     = oci_core_instance.canary.shape_config[0].ocpus == 2
    error_message = "The canary must not exceed two Always Free OCPUs."
  }

  assert {
    condition     = oci_core_instance.canary.shape_config[0].memory_in_gbs == 12
    error_message = "The canary must not exceed twelve Always Free GB of memory."
  }

  assert {
    condition     = oci_objectstorage_bucket.backups.access_type == "NoPublicAccess"
    error_message = "The backup bucket must never be public."
  }

  assert {
    condition     = oci_objectstorage_bucket.backups.versioning == "Enabled"
    error_message = "The backup bucket must retain version history."
  }

  assert {
    condition     = output.public_tcp_ports == [80, 443]
    error_message = "Only HTTP and HTTPS may be exposed publicly."
  }

  assert {
    condition     = terraform_data.safety_boundary.input.monthly_budget_usd == 0
    error_message = "The recurring budget must remain zero dollars."
  }
}

run "reject_unconfirmed_free_tier" {
  command = plan

  variables {
    confirmed_always_free_eligibility = false
  }

  expect_failures = [terraform_data.safety_boundary]
}
