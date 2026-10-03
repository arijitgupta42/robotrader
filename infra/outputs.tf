output "function_name" {
  value = aws_lambda_function.stock_selection.function_name
}

output "log_group" {
  value = aws_cloudwatch_log_group.stock_selection.name
}
