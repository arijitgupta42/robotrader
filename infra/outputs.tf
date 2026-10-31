output "function_name" {
  value = aws_lambda_function.stock_selection.function_name
}

output "log_group" {
  value = aws_cloudwatch_log_group.stock_selection.name
}

output "backtest_function_name" {
  value = aws_lambda_function.backtest.function_name
}

output "backtest_schedule" {
  value = aws_scheduler_schedule.backtest_quarterly.schedule_expression
}

# Used as a plain value in .github/workflows/pages.yml (an ARN is not a secret).
output "github_pages_role_arn" {
  value = aws_iam_role.github_pages.arn
}
