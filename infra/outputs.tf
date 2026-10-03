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
