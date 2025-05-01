variable "region" {
    description = "AWS region for deploying resources"
    type        = string
    default     = "ap-northeast-2"
}
variable "profile" {
    description = "AWS profile name to use for authentication"
    type        = string
    default     = "costnorm"
}
variable "function_name" {
  description = "Name of the EBS Optimizer Lambda function"
  type        = string
  default     = "ebs-optimizer-lambda"
}
variable "lambda_timeout" {
  description = "Maximum execution time for the Lambda function (seconds)"
  type        = number
  default     = 60
}
variable "lambda_memory" {
  description = "Memory allocated to the Lambda function (MB)"
  type        = number
  default     = 256
}
variable "lambda_runtime" {
  description = "Lambda function runtime environment"
  type        = string
  default     = "python3.9"
}
variable "lambda_handler" {
  description = "Lambda function handler (filename.handler_function)"
  type        = string
  default     = "lambda_function.lambda_handler"
} 