terraform {
  required_version = "= 1.12.6"
}

resource "terraform_data" "eldridge_local" {
  input = "eldridge-local-zero-cost-validation"

  lifecycle {
    postcondition {
      condition     = startswith(self.input, "eldridge-local")
      error_message = "The fixture must remain bound to the Eldridge local profile."
    }
  }
}
