terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# --- Source Code Packaging ---

# Data source to create the Lambda deployment package zip file from the src directory
data "archive_file" "lambda_source_code" {
  type        = "zip"
  # Source directory relative to the terraform module directory
  source_dir  = "../src"
  # Output path for the generated zip file (within the terraform module directory)
  output_path = "${path.module}/deployment_package.zip"

  # Exclude specified files and common development artifacts
  excludes = toset([
    ".env",          # Exclude the main .env file
    ".env.sample",   # Exclude the sample .env file
    "__pycache__/",  # Exclude Python bytecode cache
    "*.pyc",         # Exclude Python compiled files
    ".pytest_cache/",# Exclude pytest cache directory
    ".venv/",        # Exclude virtual environment directories
    "venv/",         # Common alternative venv name
  ])
}

# --- Layer Build ---

# Provides a trigger based on the requirements file content
resource "null_resource" "build_lambda_layer" {
  triggers = {
    requirements_hash = filebase64sha256("../requirements.txt")
    # build_timestamp   = timestamp() # Uncomment to force rebuild on every apply
  }

  provisioner "local-exec" {
    # Note: Assumes Docker is installed and running on the machine executing Terraform
    command = <<-EOT
      echo "Building Lambda layer dependencies..."
      export DOCKER_BUILDKIT=1 # Enable BuildKit for potential optimizations/features

      # Define build directory and output zip file path (relative to terraform dir)
      LAYER_BUILD_DIR="./build/layer"
      LAYER_ZIP_PATH="./python-deps-layer.zip"
      REQUIREMENTS_PATH="../requirements.txt"
      DOCKERFILE_PATH="./Dockerfile.layer"

      # Clean previous build artifacts
      rm -rf $LAYER_BUILD_DIR
      rm -f $LAYER_ZIP_PATH
      mkdir -p $LAYER_BUILD_DIR

      # Build the docker image - use --platform if your build host arch != lambda arch
      docker build --platform linux/arm64 -t lambda-layer-builder:latest -f $DOCKERFILE_PATH ..

      # Create a container from the image
      container_id=$(docker create --platform linux/arm64 lambda-layer-builder:latest)

      # Copy the installed packages from the container's /opt/* to the local build dir
      docker cp "$container_id:/opt/." "$LAYER_BUILD_DIR/"

      # Remove the container
      docker rm -f "$container_id"

      echo "Zipping layer contents..."
      # Zip the contents *inside* the 'python' directory, not the directory itself
      (cd "$LAYER_BUILD_DIR/python" && zip -r "../../${LAYER_ZIP_PATH}" .)

      echo "Lambda layer zip created at ${LAYER_ZIP_PATH}"
    EOT
  }
}

# --- Lambda Layer ---

resource "aws_lambda_layer_version" "python_deps_layer" {
  layer_name          = var.lambda_layer_name
  description         = "Python dependencies for ${var.lambda_function_name}"
  license_info        = "MIT"
  compatible_runtimes = [var.lambda_runtime]
  compatible_architectures = [var.lambda_architecture]

  # Reference the zip file created by the null_resource
  filename         = "./python-deps-layer.zip"
  source_code_hash = filebase64sha256("./python-deps-layer.zip")

  # Ensure the layer is created only after the build finishes
  depends_on = [
    null_resource.build_lambda_layer
  ]
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

  # Attach the dependency layer
  layers = [aws_lambda_layer_version.python_deps_layer.arn]

  # Use the zip file created by the archive_file data source
  filename         = data.archive_file.lambda_source_code.output_path
  # Use the hash of the generated zip file to trigger updates on code change
  source_code_hash = data.archive_file.lambda_source_code.output_base64sha256

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
    aws_lambda_layer_version.python_deps_layer, # Ensure layer exists before function creation
  ]
}

# CloudWatch Log Group for Lambda (explicit definition)
resource "aws_cloudwatch_log_group" "lambda_log_group" {
  name              = "/aws/lambda/${aws_lambda_function.arm_analyzer.function_name}"
  retention_in_days = 14 # Configure log retention
  tags              = var.tags
} 