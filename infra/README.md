# Infrastructure (Terraform)

Defines the stock-selection Lambda, its IAM role, log group, and the S3 trigger on the scout's bucket.
The scout's own resources (Lambda, schedules, bucket, SES, SSM, role) are not here yet; they were created by hand and are tracked in the import issue.

## One-time: create the state bucket

State lives in its own versioned bucket, separate from the data bucket. Run these once, with an account that can create buckets
(the account id is `544611252144`; the region is `eu-west-1`, because the CLI default is `us-east-1`):

```bash
aws s3api create-bucket --bucket robotrader-tfstate-544611252144 --region eu-west-1 \
  --create-bucket-configuration LocationConstraint=eu-west-1
aws s3api put-bucket-versioning --bucket robotrader-tfstate-544611252144 \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket robotrader-tfstate-544611252144 \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket robotrader-tfstate-544611252144 \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

Locking uses an S3 lock file (`use_lockfile`), so no DynamoDB table is needed.

## Deploy

```bash
# 1. build the Lambda zip (Linux wheels, no Docker)
python stock_selection_lambda/build_zip.py                   # writes dist/stock-selection.zip

# 2. init once
cd infra
cp backend.hcl.example backend.hcl                           # backend.hcl is git-ignored
terraform init -backend-config=backend.hcl

# 3. BEFORE applying: the notification resource replaces the bucket's whole notification config
aws s3api get-bucket-notification-configuration --bucket sector-scout-results-544611252144 --region eu-west-1
#    expect empty output; if anything is listed, add it to aws_s3_bucket_notification first

# 4. apply
terraform plan
terraform apply
```

Rebuilding the zip changes `source_code_hash`, so `terraform apply` redeploys the code.

## Test end to end

Run the scout manually; it writes `successful/<week>/response_*.json`, which triggers the Lambda:

```bash
aws lambda invoke --function-name sector-scout --region eu-west-1 --payload '{}' --cli-binary-format raw-in-base64-out out.json
aws logs tail /aws/lambda/stock-selection --region eu-west-1 --follow
aws s3 ls s3://sector-scout-results-544611252144/snapshots/ --recursive
```

Done when a picks email arrives and a new `snapshots/<week>/universe.csv` exists. A manual invocation (any event that isn't `WeeklyTrigger` or `HourlyRetry`) runs the scout's whole pipeline unconditionally, so it also sends the sector report email and uses OpenRouter credits.

## Quarterly methodology review (`backtest.tf`)

A second Lambda, `backtest-report`, runs from the same zip (handler `backtest_handler.handler`). EventBridge Scheduler starts it at 07:00 UTC on the 1st of January, April, July and October. It builds the backtest report from every `snapshots/` object, saves `reports/YYYY-Qn/backtest.html` and `backtest.json`, and emails the report. It changes nothing else; improving the prompts and thresholds is a reviewed pull request (`docs/methodology-review.md`).

Run it now to see the report:

```bash
aws lambda invoke --function-name backtest-report --region eu-west-1 --payload '{"period":"manual"}' --cli-binary-format raw-in-base64-out out.json
```

or without AWS: `python stock_selection_lambda/backtest_report.py --bucket sector-scout-results-544611252144 --html report.html`.

## Settings worth knowing

- `maximum_retry_attempts = 0`: the handler never re-raises (failures are written to `failed/` and emailed once); a retry would send the failure email three times.
- Memory 1024 MB, timeout 600 s; the package is about 130 MB unzipped (limit 250 MB).
