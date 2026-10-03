# Infrastructure (Terraform)

Everything the pipeline needs in AWS (account `544611252144`, region `eu-west-1`) is defined here, and nothing else should exist in the account. It is meant to cost nothing: every service used stays inside the free tier.

| File | What it defines |
|---|---|
| `scout.tf` | The sector scout: Lambda, role, log group, the attempt-window SSM parameter, and its two schedules (weekly trigger, retry) |
| `main.tf` | The stock-selection Lambda, its role and log group, and the S3 trigger on `successful/` |
| `backtest.tf` | The quarterly `backtest-report` Lambda, its role, and its schedule |
| `data_bucket.tf` | The results bucket (`successful/`, `failed/`, `snapshots/`, `reports/`) with its lifecycle rules |
| `account.tf` | The verified SES sender, the cost alert (budget), and the Terraform state bucket |
| `imports.tf` | One-time `import` blocks that adopted the resources created by hand |

Not managed here, on purpose:

- **The OpenRouter API key** (SSM SecureString `/sector-scout/openrouter-api-key`). Managing it would put the key in the state file. It was created once by hand (`aws ssm put-parameter --name /sector-scout/openrouter-api-key --type SecureString --value <key> --region eu-west-1`); the scout's role may read it by name.
- **The `robotrader-admin` IAM user** that runs Terraform and the AWS CLI. Terraform must not be able to delete the credentials it runs with.

## Deploy

```bash
# 1. build both Lambda zips (Linux wheels, no Docker; the builds are reproducible, so unchanged code gives no diff)
python stock_selection_lambda/build_zip.py                   # dist/stock-selection.zip (stock selection + quarterly report)
python financial_market_news_analyzer/build_zip.py           # dist/scout.zip (the sector scout)

# 2. init once
cd infra
cp backend.hcl.example backend.hcl                           # backend.hcl is git-ignored
terraform init -backend-config=backend.hcl

# 3. review, then apply
terraform plan
terraform apply
```

`terraform apply` redeploys any Lambda whose zip changed. The plan is the review step: read it for `destroy` and for changes you did not expect.

## State bucket

State lives in its own versioned bucket, separate from the data bucket (`robotrader-tfstate-544611252144`, managed in `account.tf` and protected with `prevent_destroy`). Locking uses an S3 lock file (`use_lockfile`), so no DynamoDB table is needed. To recreate the account from nothing, create that bucket first by hand, then apply:

```bash
aws s3api create-bucket --bucket robotrader-tfstate-544611252144 --region eu-west-1 \
  --create-bucket-configuration LocationConstraint=eu-west-1
aws s3api put-bucket-versioning --bucket robotrader-tfstate-544611252144 --versioning-configuration Status=Enabled
```

## Schedules

| Schedule | When (UTC) | What |
|---|---|---|
| `sector-scout-weekly` | Saturday 06:00, within a 30 minute window | opens the attempt window and runs the scout |
| `sector-scout-hourly-retry` | :05 and :35 of every hour | runs the scout again only if the window is still open, otherwise returns at once |
| `backtest-report-quarterly` | 07:00 on the 1st of Jan, Apr, Jul, Oct | builds the backtest report and emails it |

A successful scout run writes `successful/<week>/response_*.json`, and that write starts the stock-selection Lambda, which sends the week's one email.

## Test end to end

A manual scout run (any event that isn't `WeeklyTrigger` or `HourlyRetry`) runs the whole pipeline unconditionally, which uses a little OpenRouter credit and sends the weekly email:

```bash
aws lambda invoke --function-name sector-scout --region eu-west-1 --payload '{}' --cli-binary-format raw-in-base64-out out.json
aws logs tail /aws/lambda/stock-selection --region eu-west-1 --follow
aws s3 ls s3://sector-scout-results-544611252144/snapshots/ --recursive
```

To replay an existing result without running the scout, invoke `stock-selection` with a synthetic S3 event for its `successful/` key.

## Quarterly methodology review

`backtest-report` (same zip as stock selection, handler `backtest_handler.handler`) builds the backtest report from every `snapshots/` object, saves `reports/YYYY-Qn/backtest.html` and `backtest.json`, and emails it. It changes nothing else; improving the prompts and thresholds is a reviewed pull request (`docs/methodology-review.md`). Run it on demand:

```bash
aws lambda invoke --function-name backtest-report --region eu-west-1 --payload '{}' --cli-binary-format raw-in-base64-out out.json
```

or without AWS: `python stock_selection_lambda/backtest_report.py --bucket sector-scout-results-544611252144 --html report.html`.

## One-time cleanup of leftovers

Resources that were created by hand and are no longer used (found by the audit in issue #13). Delete them once, after the first `terraform apply` of this configuration, because the old scheduler role is still in use until the schedules are moved to the new one:

```bash
# console-made Lambda role that no function uses, and its policy
aws iam detach-role-policy --role-name sector-scout-role-9fkpedsl --policy-arn arn:aws:iam::544611252144:policy/service-role/AWSLambdaBasicExecutionRole-2e978121-19af-4f33-9bee-13f2d6a9072e
aws iam delete-role --role-name sector-scout-role-9fkpedsl
aws iam delete-policy --policy-arn arn:aws:iam::544611252144:policy/service-role/AWSLambdaBasicExecutionRole-2e978121-19af-4f33-9bee-13f2d6a9072e

# console-made scheduler role and policy, replaced by sector-scout-scheduler-role (run only after the apply moved the schedules)
aws iam detach-role-policy --role-name Amazon_EventBridge_Scheduler_LAMBDA_c153158938 --policy-arn arn:aws:iam::544611252144:policy/service-role/Amazon-EventBridge-Scheduler-Execution-Policy-09ee2ec7-aa84-4979-9d72-d72c89ab8494
aws iam delete-role --role-name Amazon_EventBridge_Scheduler_LAMBDA_c153158938
aws iam delete-policy --policy-arn arn:aws:iam::544611252144:policy/service-role/Amazon-EventBridge-Scheduler-Execution-Policy-09ee2ec7-aa84-4979-9d72-d72c89ab8494

# orphaned Systems Manager Quick Setup stack (two roles; no Quick Setup configuration exists)
aws cloudformation delete-stack --stack-name AWS-QuickSetup-SSM-LocalDeploymentRolesStack --region eu-west-1

# SES configuration set that was only the sender's console default (the apply detaches it first)
aws sesv2 delete-configuration-set --configuration-set-name my-first-configuration-set --region eu-west-1

# the older, duplicate $10 budget (robotrader-monthly at $2 is the one Terraform manages)
aws budgets delete-budget --account-id 544611252144 --budget-name "My Monthly Cost Budget"
```
