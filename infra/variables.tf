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
