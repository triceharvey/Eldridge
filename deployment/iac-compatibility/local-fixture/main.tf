terraform {
  required_version = ">= 1.12.0, < 2.0.0"
}

resource "terraform_data" "eldridge_compatibility" {
  input = "eldridge-local-zero-cost-compatibility"

  lifecycle {
    postcondition {
      condition     = startswith(self.input, "eldridge-local")
      error_message = "The fixture must remain bound to the Eldridge local profile."
    }
  }
}
