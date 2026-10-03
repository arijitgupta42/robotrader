# The sector scout: the weekly Lambda, its role, its log group, the attempt-window parameter and the two schedules
# that start it.  These were created by hand and are adopted here with import blocks (imports.tf).
#
# Deliberately NOT managed here: the OpenRouter API key, the SecureString parameter named by var.api_key_param.
# Managing it would put the key itself into the Terraform state file.  It is created once by hand:
#   aws ssm put-parameter --name /sector-scout/openrouter-api-key --type SecureString --value <key> --region eu-west-1
# and the scout's role is allowed to read it by name below.

locals {
  param_arn_prefix = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter"
}

resource "aws_cloudwatch_log_group" "scout" {
  name              = "/aws/lambda/${var.scout_function_name}"
  retention_in_days = 90
}

# The window parameter is written by the scout at run time ("open" when the weekly schedule fires, "closed" after a
# success), so Terraform owns its existence but not its value.
resource "aws_ssm_parameter" "attempt_window" {
  name        = var.window_param
  type        = "String"
  value       = "closed"
  description = "Tracks whether the weekly pipeline has succeeded (open/closed)"

  lifecycle {
    ignore_changes = [value]
  }
}

data "aws_iam_policy_document" "scout_permissions" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.scout.arn}:*"]
  }

  statement {
    sid       = "SaveResults"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/successful/*", "${aws_s3_bucket.data.arn}/failed/*"]
  }

  statement {
    sid       = "ReadParameters"
    actions   = ["ssm:GetParameter"]
    resources = ["${local.param_arn_prefix}${var.window_param}", "${local.param_arn_prefix}${var.api_key_param}"]
  }

  statement {
    sid       = "UpdateAttemptWindow"
    actions   = ["ssm:PutParameter"]
    resources = ["${local.param_arn_prefix}${var.window_param}"]
  }
}

resource "aws_iam_role" "scout" {
  description        = "Execution role for the Sector Scout Lambda function"
  name               = "${var.scout_function_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy" "scout" {
  name   = "${var.scout_function_name}-lambda-policy"
  role   = aws_iam_role.scout.id
  policy = data.aws_iam_policy_document.scout_permissions.json
}

resource "aws_lambda_function" "scout" {
  function_name = var.scout_function_name
  role          = aws_iam_role.scout.arn
  runtime       = "python3.11"
  architectures = ["x86_64"]
  handler       = "lambda_handler.handler"
  memory_size   = 512
  timeout       = 840

  filename         = var.scout_package_path
  source_code_hash = filebase64sha256(var.scout_package_path)

  environment {
    variables = {
      BUCKET_NAME      = var.data_bucket
      SSM_WINDOW_PARAM = var.window_param
    }
  }

  depends_on = [aws_cloudwatch_log_group.scout]
}

# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

resource "aws_iam_role" "scout_scheduler" {
  name               = "${var.scout_function_name}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scout_scheduler" {
  name = "invoke-${var.scout_function_name}"
  role = aws_iam_role.scout_scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.scout.arn
    }]
  })
}

resource "aws_scheduler_schedule" "scout_weekly" {
  name                         = "${var.scout_function_name}-weekly"
  description                  = "Weekly trigger for the Sector Scout (Saturday 06:00 UTC, within a 30 minute window)"
  schedule_expression          = "cron(0 6 ? * SAT *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode                      = "FLEXIBLE"
    maximum_window_in_minutes = 30
  }

  target {
    arn      = aws_lambda_function.scout.arn
    role_arn = aws_iam_role.scout_scheduler.arn
    input    = jsonencode({ "detail-type" = "WeeklyTrigger", "source" = "aws.scheduler" })

    retry_policy {
      maximum_event_age_in_seconds = 86400
      maximum_retry_attempts       = 0
    }
  }
}

# Runs the pipeline again only while the weekly window is still open (no success yet this week); otherwise the
# invocation returns immediately.  It fires at :05 and :35 of every hour (the name says "hourly" for historical reasons).
resource "aws_scheduler_schedule" "scout_retry" {
  name                         = "${var.scout_function_name}-hourly-retry"
  description                  = "Retry at :05 and :35 of every hour; only runs the pipeline if the weekly window is still open"
  schedule_expression          = "cron(5,35 * * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.scout.arn
    role_arn = aws_iam_role.scout_scheduler.arn
    input    = jsonencode({ "detail-type" = "HourlyRetry", "source" = "aws.scheduler" })

    retry_policy {
      maximum_event_age_in_seconds = 86400
      maximum_retry_attempts       = 0
    }
  }
}
