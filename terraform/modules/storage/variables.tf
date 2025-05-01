# Variables specific to the Storage module

variable "ebs_lambda_function_name" {
  description = "Name for the EBS Optimizer Lambda function."
  type        = string
  default     = "ebs-optimizer-lambda"
}

variable "ebs_lambda_handler" {
  description = "Handler for the EBS Optimizer Lambda function."
  type        = string
  default     = "lambda_function.lambda_handler"
}

variable "ebs_lambda_runtime" {
  description = "Runtime for the EBS Optimizer Lambda function."
  type        = string
  default     = "python3.9"
}

variable "ebs_lambda_timeout" {
  description = "Timeout for the EBS Optimizer Lambda function."
  type        = number
  default     = 60
}

variable "ebs_lambda_memory" {
  description = "Memory size for the EBS Optimizer Lambda function."
  type        = number
  default     = 256
}

# 필요 시 Storage 모듈 전체를 제어하는 변수 추가
# variable "deploy_storage_resources" {
#   description = "Flag to control deployment of all storage resources."
#   type        = bool
#   default     = true
# }
