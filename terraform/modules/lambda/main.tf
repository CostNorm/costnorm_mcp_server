# Archive the Lambda function code
data "archive_file" "lambda_package" {
  type        = "zip"
  source_dir  = var.source_dir
  output_path = "${path.module}/${var.output_filename}"
}

# Define the Lambda function resource
resource "aws_lambda_function" "this" {
  function_name = var.function_name
  role          = var.role_arn
  handler       = var.handler
  runtime       = var.runtime
  timeout       = var.timeout
  memory_size   = var.memory_size
  architectures = var.architectures

  filename         = data.archive_file.lambda_package.output_path
  source_code_hash = data.archive_file.lambda_package.output_base64sha256

  environment {
    variables = var.environment_variables
  }

  # Ensure the archive file is created before the Lambda function
  depends_on = [data.archive_file.lambda_package]
}
