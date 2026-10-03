# Quarterly methodology review: a second Lambda in the same package as stock-selection, started by
# EventBridge Scheduler on the 1st of January, April, July and October.  It builds the backtest report
# from the Universe Snapshots, saves it under reports/ and emails it.

# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "backtest" {
  name              = "/aws/lambda/${var.backtest_function_name}"
  retention_in_days = 90
}

data "aws_iam_policy_document" "backtest_permissions" {
  statement {
    sid       = "ListSnapshots"
    actions   = ["s3:ListBucket"]
    resources = [data.aws_s3_bucket.data.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["snapshots/*", "snapshots/"]
    }
  }

  statement {
    sid       = "ReadSnapshots"
    actions   = ["s3:GetObject"]
    resources = ["${data.aws_s3_bucket.data.arn}/snapshots/*"]
  }

  statement {
    sid       = "WriteReports"
    actions   = ["s3:PutObject"]
    resources = ["${data.aws_s3_bucket.data.arn}/reports/*"]
  }

  statement {
    sid     = "SendReportEmail"
    actions = ["ses:SendEmail"]
    resources = [
      "arn:aws:ses:${var.region}:${data.aws_caller_identity.current.account_id}:identity/${var.ses_sender}",
      "arn:aws:ses:${var.region}:${data.aws_caller_identity.current.account_id}:configuration-set/*",
    ]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.backtest.arn}:*"]
  }
}

resource "aws_iam_role" "backtest" {
  name               = "${var.backtest_function_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy" "backtest" {
  name   = "${var.backtest_function_name}-permissions"
  role   = aws_iam_role.backtest.id
  policy = data.aws_iam_policy_document.backtest_permissions.json
}

resource "aws_lambda_function" "backtest" {
  function_name = var.backtest_function_name
  role          = aws_iam_role.backtest.arn
  runtime       = "python3.12"
  architectures = ["x86_64"]
  handler       = "backtest_handler.handler"
  memory_size   = 1024
  timeout       = 300

  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)

  environment {
    variables = {
      BUCKET_NAME   = var.data_bucket
      SES_SENDER    = var.ses_sender
      SES_RECIPIENT = var.ses_recipient
    }
  }

  depends_on = [aws_cloudwatch_log_group.backtest]
}

# The handler catches its own failures, records them and emails once; a retry would only email again.
resource "aws_lambda_function_event_invoke_config" "backtest" {
  function_name          = aws_lambda_function.backtest.function_name
  maximum_retry_attempts = 0
}

# ---------------------------------------------------------------------------
# Schedule: 07:00 UTC on the 1st of Jan, Apr, Jul and Oct
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "backtest_scheduler" {
  name               = "${var.backtest_function_name}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "backtest_scheduler" {
  name = "invoke-${var.backtest_function_name}"
  role = aws_iam_role.backtest_scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.backtest.arn
    }]
  })
}

resource "aws_scheduler_schedule" "backtest_quarterly" {
  name                         = "${var.backtest_function_name}-quarterly"
  description                  = "Quarterly methodology review input: backtest report from the Universe Snapshots"
  schedule_expression          = "cron(0 7 1 1,4,7,10 ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.backtest.arn
    role_arn = aws_iam_role.backtest_scheduler.arn
    input    = jsonencode({})

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}
