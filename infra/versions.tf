terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State lives in its own versioned bucket, separate from the data bucket.
  # The bucket name is account-specific, so it is passed at init time:
  #   terraform init -backend-config=backend.hcl      (see infra/README.md)
  backend "s3" {
    key          = "stock-selection/terraform.tfstate"
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project = "robotrader"
      managed = "terraform"
    }
  }
}
