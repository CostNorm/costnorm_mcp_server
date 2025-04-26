variable "aws_region" {
  description = "AWS region to deploy the resources"
  type        = string
  default     = "ap-northeast-2" # Default is seoul, or your preferred region
}

variable "lambda_layer_name" {
  description = "Name for the Lambda layer"
  type        = string
  default     = "arm-analyzer-python-deps"
}

variable "lambda_function_name" {
  description = "Name for the Lambda function"
  type        = string
  default     = "arm-compatibility-analyzer"
}

variable "lambda_iam_role_name" {
  description = "Name for the Lambda IAM role"
  type        = string
  default     = "arm-compatibility-analyzer-role"
}

variable "lambda_runtime" {
  description = "Lambda function runtime"
  type        = string
  default     = "python3.13"
}

variable "lambda_handler" {
  description = "Lambda function handler"
  type        = string
  default     = "lambda_function.lambda_handler"
}

variable "lambda_timeout" {
  description = "Lambda function timeout in seconds"
  type        = number
  default     = 600 # Analysis might take time
}

variable "lambda_memory_size" {
  description = "Lambda function memory size in MB"
  type        = number
  default     = 3008 # Needs memory for potential large repos/dependencies
}

variable "lambda_architecture" {
  description = "Lambda function architecture (x86_64 or arm64)"
  type        = string
  default     = "arm64" # Run the analyzer itself on arm64
}

# --- Environment Variables ---

variable "log_level" {
  description = "Logging level for the Lambda function (e.g., INFO, DEBUG)"
  type        = string
  default     = "INFO"
}

variable "dockerhub_username" {
  description = "Docker Hub username"
  type        = string
  default     = ""
  sensitive   = true
}

variable "dockerhub_password" {
  description = "Docker Hub password or Personal Access Token"
  type        = string
  default     = ""
  sensitive   = true
}

variable "github_token" {
  description = "GitHub Personal Access Token (repo scope recommended)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "enable_terraform_analyzer" {
  description = "Enable Terraform Analyzer (True/False string)"
  type        = string
  default     = "False"
}

variable "enable_docker_analyzer" {
  description = "Enable Docker Analyzer (True/False string)"
  type        = string
  default     = "False"
}

variable "enable_dependency_analyzer" {
  description = "Enable Dependency Analyzer (True/False string)"
  type        = string
  default     = "True"
}

variable "tags" {
  description = "Tags to apply to the resources"
  type        = map(string)
  default = {
    Project = "ARM-Compatibility-Analyzer"
  }
} 