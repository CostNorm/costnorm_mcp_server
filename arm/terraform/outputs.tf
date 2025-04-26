output "lambda_function_name" {
  description = "The name of the deployed Lambda function"
  value       = aws_lambda_function.arm_analyzer.function_name
}

output "lambda_function_arn" {
  description = "The ARN of the deployed Lambda function"
  value       = aws_lambda_function.arm_analyzer.arn
}

output "lambda_iam_role_arn" {
  description = "The ARN of the IAM role created for the Lambda function"
  value       = aws_iam_role.lambda_exec_role.arn
}

output "lambda_log_group_name" {
  description = "The name of the CloudWatch Log Group for the Lambda function"
  value       = aws_cloudwatch_log_group.lambda_log_group.name
} 