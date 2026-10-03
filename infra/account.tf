# Account-level pieces the pipeline relies on: the verified sender address, the cost alert, and the state bucket.
# Adopted from hand-made resources with import blocks (imports.tf).

# The verified SES sender/recipient address.  (The account's SES configuration set that used to be attached to it
# is not needed and is removed: no configuration_set_name here.)
resource "aws_sesv2_email_identity" "sender" {
  email_identity = var.ses_sender
}

# Cost alert: email at 85% and 100% of actual spend and at 100% of forecast.  The pipeline should cost nothing.
resource "aws_budgets_budget" "monthly" {
  name         = "robotrader-monthly"
  budget_type  = "COST"
  limit_amount = var.budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # as created in the console: gross cost, credits and refunds do not reduce it
  billing_view_arn = "arn:aws:billing::${data.aws_caller_identity.current.account_id}:billingview/primary"
  metrics          = ["UnblendedCost"]

  filter_expression {
    not {
      dimensions {
        key    = "RECORD_TYPE"
        values = ["Credit", "Refund"]
      }
    }
  }

  notification {
    notification_type          = "ACTUAL"
    comparison_operator        = "GREATER_THAN"
    threshold                  = 85
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    notification_type          = "ACTUAL"
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    notification_type          = "FORECASTED"
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}

# This configuration's own state bucket (created by the one-time commands in README.md).
resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}
