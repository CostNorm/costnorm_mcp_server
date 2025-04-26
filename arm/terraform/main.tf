terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# IAM Role for Lambda
resource "aws_iam_role" "lambda_exec_role" {
  name = var.lambda_iam_role_name
  tags = var.tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      },
    ]
  })
}

# Attach basic execution policy (CloudWatch Logs)
resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Lambda Function
resource "aws_lambda_function" "arm_analyzer" {
  function_name    = var.lambda_function_name
  role             = aws_iam_role.lambda_exec_role.arn
  handler          = var.lambda_handler
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory_size
  architectures    = [var.lambda_architecture]
  tags             = var.tags

  # Assumes deployment_package.zip exists at the specified path
  filename         = var.lambda_zip_path
  # Calculate hash of the zip file to trigger updates on change
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  environment {
    variables = {
      # Pass variables from terraform config to lambda environment
      LOG_LEVEL                   = var.log_level
      DOCKERHUB_USERNAME          = var.dockerhub_username
      DOCKERHUB_PASSWORD          = var.dockerhub_password
      GITHUB_TOKEN                = var.github_token
      ENABLE_TERRAFORM_ANALYZER   = var.enable_terraform_analyzer
      ENABLE_DOCKER_ANALYZER      = var.enable_docker_analyzer
      ENABLE_DEPENDENCY_ANALYZER  = var.enable_dependency_analyzer
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic_execution,
  ]
}

# CloudWatch Log Group for Lambda (explicit definition)
resource "aws_cloudwatch_log_group" "lambda_log_group" {
  name              = "/aws/lambda/${aws_lambda_function.arm_analyzer.function_name}"
  retention_in_days = 14 # Configure log retention
  tags              = var.tags
} 