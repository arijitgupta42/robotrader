# One-time adoption of resources that were created by hand.  `terraform apply` imports them into the state and then
# applies the differences between what exists and what the configuration says.  Once applied, these blocks do nothing
# and can stay or be deleted.

import {
  to = aws_s3_bucket.data
  id = var.data_bucket
}
import {
  to = aws_s3_bucket_versioning.data
  id = var.data_bucket
}
import {
  to = aws_s3_bucket_server_side_encryption_configuration.data
  id = var.data_bucket
}
import {
  to = aws_s3_bucket_public_access_block.data
  id = var.data_bucket
}
import {
  to = aws_s3_bucket_ownership_controls.data
  id = var.data_bucket
}
import {
  to = aws_s3_bucket_lifecycle_configuration.data
  id = var.data_bucket
}

import {
  to = aws_s3_bucket.state
  id = var.state_bucket
}
import {
  to = aws_s3_bucket_versioning.state
  id = var.state_bucket
}
import {
  to = aws_s3_bucket_server_side_encryption_configuration.state
  id = var.state_bucket
}
import {
  to = aws_s3_bucket_public_access_block.state
  id = var.state_bucket
}

import {
  to = aws_cloudwatch_log_group.scout
  id = "/aws/lambda/${var.scout_function_name}"
}
import {
  to = aws_ssm_parameter.attempt_window
  id = var.window_param
}
import {
  to = aws_iam_role.scout
  id = "${var.scout_function_name}-lambda-role"
}
import {
  to = aws_iam_role_policy.scout
  id = "${var.scout_function_name}-lambda-role:${var.scout_function_name}-lambda-policy"
}
import {
  to = aws_lambda_function.scout
  id = var.scout_function_name
}
import {
  to = aws_scheduler_schedule.scout_weekly
  id = "default/${var.scout_function_name}-weekly"
}
import {
  to = aws_scheduler_schedule.scout_retry
  id = "default/${var.scout_function_name}-hourly-retry"
}

import {
  to = aws_sesv2_email_identity.sender
  id = var.ses_sender
}
import {
  to = aws_budgets_budget.monthly
  id = "${data.aws_caller_identity.current.account_id}:robotrader-monthly"
}
