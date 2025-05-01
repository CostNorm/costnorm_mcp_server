variable "function_name" {
  description = "The name of the Lambda function."
  type        = string
}

variable "source_dir" {
  description = "Path to the directory containing the Lambda function code."
  type        = string
}

variable "output_filename" {
  description = "Filename for the zipped deployment package."
  type        = string
  default     = "lambda_function.zip"
}

variable "handler" {
  description = "Lambda function handler (filename.handler_function)."
  type        = string
}

variable "runtime" {
  description = "Lambda function runtime environment."
  type        = string
}

variable "role_arn" {
  description = "ARN of the IAM role to be used by the Lambda function."
  type        = string
}

variable "timeout" {
  description = "Maximum execution time for the Lambda function (seconds)."
  type        = number
  default     = 60
}

variable "memory_size" {
  description = "Memory allocated to the Lambda function (MB)."
  type        = number
  default     = 256
}

variable "architectures" {
  description = "Instruction set architecture for the Lambda function."
  type        = list(string)
  default     = ["x86_64"]
}

variable "environment_variables" {
  description = "A map of environment variables for the Lambda function."
  type        = map(string)
  default     = {}
}
