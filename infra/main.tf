data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# IAM: read successful/, write snapshots/ and failed/, keep the Paper Portfolio in portfolio/, publish the
# website's data to site/, send mail, write logs
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "stock_selection" {
  name               = "${var.function_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "permissions" {
  # Listing lets a missing object come back as NoSuchKey (not AccessDenied), and lets the portfolio step find
  # its weekly records, a stored week's scout result and the website's week index.
  statement {
    sid       = "ListOwnPrefixes"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "successful/*", "snapshots/*", "portfolio/*", "portfolio/", "site/*", "site/",
      ]
    }
  }

  statement {
    sid     = "ReadScoutResultsSnapshotsPortfolioAndSite"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.data.arn}/successful/*",
      "${aws_s3_bucket.data.arn}/snapshots/*",
      "${aws_s3_bucket.data.arn}/portfolio/*",
      "${aws_s3_bucket.data.arn}/site/*",
    ]
  }

  statement {
    sid     = "WriteSnapshotsFailuresPortfolioAndSite"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.data.arn}/snapshots/*",
      "${aws_s3_bucket.data.arn}/failed/*",
      "${aws_s3_bucket.data.arn}/portfolio/*",
      "${aws_s3_bucket.data.arn}/site/*",
    ]
  }

  statement {
    sid       = "SendPicksEmail"
    actions   = ["ses:SendEmail"]
    resources = [aws_sesv2_email_identity.sender.arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.stock_selection.arn}:*"]
  }
}

resource "aws_iam_role_policy" "stock_selection" {
  name   = "${var.function_name}-permissions"
  role   = aws_iam_role.stock_selection.id
  policy = data.aws_iam_policy_document.permissions.json
}

# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "stock_selection" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 90
}

resource "aws_lambda_function" "stock_selection" {
  function_name = var.function_name
  role          = aws_iam_role.stock_selection.arn
  runtime       = "python3.12"
  architectures = ["x86_64"]
  handler       = "handler.handler"
  memory_size   = 1024
  timeout       = 600

  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)

  environment {
    variables = {
      SES_SENDER    = var.ses_sender
      SES_RECIPIENT = var.ses_recipient
      BUCKET_NAME   = var.data_bucket
      SITE_URL      = var.site_url
    }
  }

  depends_on = [aws_cloudwatch_log_group.stock_selection]
}

# The handler catches every failure itself, records it in failed/ and emails
# once.  An S3-triggered invocation is retried twice by default, which would
# send the failure email (and re-download all prices) three times.
resource "aws_lambda_function_event_invoke_config" "stock_selection" {
  function_name          = aws_lambda_function.stock_selection.function_name
  maximum_retry_attempts = 0
}

# ---------------------------------------------------------------------------
# Trigger: a successful scout result lands in successful/
# ---------------------------------------------------------------------------

resource "aws_lambda_permission" "from_s3" {
  statement_id   = "AllowScoutBucketInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.stock_selection.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.data.arn
  source_account = data.aws_caller_identity.current.account_id
}

# WARNING: this resource owns the bucket's WHOLE notification configuration and
# replaces whatever is there.  The scout's bucket had none when this was written;
# re-check with
#   aws s3api get-bucket-notification-configuration --bucket <bucket>
# before every apply that touches it.
resource "aws_s3_bucket_notification" "successful_results" {
  bucket = aws_s3_bucket.data.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.stock_selection.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "successful/"
    filter_suffix       = ".json"
  }

  depends_on = [aws_lambda_permission.from_s3]
}
