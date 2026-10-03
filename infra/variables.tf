variable "region" {
  description = "Region of the sector scout and its bucket (the CLI default is us-east-1, so this is explicit)."
  type        = string
  default     = "eu-west-1"
}

variable "data_bucket" {
  description = "The scout's existing S3 bucket. Read from successful/, written to snapshots/ and failed/."
  type        = string
  default     = "sector-scout-results-544611252144"
}

variable "ses_sender" {
  description = "Verified SES sender address."
  type        = string
  default     = "arijitgupta2000@gmail.com"
}

variable "ses_recipient" {
  description = "Address that receives the picks email."
  type        = string
  default     = "arijitgupta2000@gmail.com"
}

variable "package_path" {
  description = "Deployment zip built by stock_selection_lambda/build_zip.py."
  type        = string
  default     = "../dist/stock-selection.zip"
}

variable "function_name" {
  type    = string
  default = "stock-selection"
}

variable "backtest_function_name" {
  type    = string
  default = "backtest-report"
}

variable "scout_function_name" {
  type    = string
  default = "sector-scout"
}

variable "scout_package_path" {
  description = "Scout deployment zip built by financial_market_news_analyzer/build_zip.py."
  type        = string
  default     = "../dist/scout.zip"
}

variable "window_param" {
  description = "SSM parameter the scout uses to track the weekly attempt window."
  type        = string
  default     = "/sector-scout/attempt-window"
}

variable "api_key_param" {
  description = "SecureString parameter holding the OpenRouter API key (created by hand, deliberately not managed here)."
  type        = string
  default     = "/sector-scout/openrouter-api-key"
}

variable "state_bucket" {
  type    = string
  default = "robotrader-tfstate-544611252144"
}

variable "budget_usd" {
  description = "Monthly cost alert threshold. The pipeline is meant to cost nothing."
  type        = string
  default     = "2.0"
}

variable "budget_alert_email" {
  type    = string
  default = "arijitgupta2000@gmail.com"
}
