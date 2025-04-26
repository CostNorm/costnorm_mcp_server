# ARM Compatibility Analyzer - Deployment Guide

This guide explains how to deploy the ARM Compatibility Analyzer as an AWS Lambda function using Terraform.

## Prerequisites

1. **AWS Account & Credentials**: You need AWS credentials with permissions to create Lambda functions, IAM roles, and CloudWatch Log Groups.
   - Configure your AWS CLI: `aws configure`
   - Or set AWS environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`

2. **Terraform**: Download and install Terraform (version >= 1.0.0)
   - Download from: <https://www.terraform.io/downloads>
   - Verify installation: `terraform -v`

3. **Python & Dependencies**: Python 3.8+ and pip installed
   - For local testing, install dependencies: `pip install -r requirements.txt`

## Local Development

For local development and testing, you can use a `.env` file to configure environment variables:

1. Create a `.env` file in the `arm/src` directory with the following variables:

   ```python
   # Logging
   LOG_LEVEL=INFO
   
   # GitHub API Access
   GITHUB_TOKEN=your_github_token_here
   
   # DockerHub Access (for Docker image inspection)
   DOCKERHUB_USERNAME=your_dockerhub_username
   DOCKERHUB_PASSWORD=your_dockerhub_password_or_token
   
   # Analyzer Configuration
   ENABLE_TERRAFORM_ANALYZER=True
   ENABLE_DOCKER_ANALYZER=True
   ENABLE_DEPENDENCY_ANALYZER=True
   ```

2. Run the analyzer locally:

   ```bash
   cd arm/src
   python -m lambda_function
   ```

The code automatically detects whether it's running in Lambda or locally and will load the `.env` file if it's not in a Lambda environment.

## Deployment Steps

### 1. Prepare the Deployment Package

Create a deployment package containing your source code and dependencies:

```bash
# Create a temporary build directory
mkdir -p build

# Install dependencies into the build directory
pip install -r requirements.txt --target ./build

# Copy your source code into the build directory
cp -r ./arm/src/* ./build/

# Create the zip file
cd build
zip -r ../arm/deployment_package.zip .
cd ..

# Clean up
rm -rf build
```

### 2. Configure Terraform Variables

1. Navigate to the Terraform directory:

   ```bash
   cd arm/terraform
   ```

2. Create your variable definitions:

   ```bash
   cp terraform.tfvars.example terraform.auto.tfvars
   ```

3. Edit `terraform.auto.tfvars` and fill in your values:
   - Set required credentials: `github_token`, `dockerhub_username`, etc.
   - Configure which analyzers to enable
   - Adjust other Lambda settings as needed

### 3. Deploy with Terraform

```bash
# Initialize Terraform
terraform init

# Preview the changes
terraform plan

# Apply the changes
terraform apply
```

Confirm the apply operation when prompted. Terraform will output information about the created resources.

## Invoking the Lambda Function

The Lambda function expects a JSON payload with a `github_url` parameter:

```bash
# Using AWS CLI
aws lambda invoke \
  --function-name arm-compatibility-analyzer \
  --payload '{"github_url":"https://github.com/username/repo-to-analyze"}' \
  response.json

# View the result
cat response.json
```

Or invoke it programmatically from your code using the AWS SDK for your language.

## Cleanup

To remove all resources created by Terraform:

```bash
terraform destroy
```

Confirm the destroy operation when prompted.

## Troubleshooting

- **CloudWatch Logs**: Check AWS CloudWatch logs at `/aws/lambda/arm-compatibility-analyzer`
- **Lambda Configuration**: Verify environment variables in the AWS Console
- **Deployment Package**: Make sure the deployment package contains all necessary dependencies
- **Permissions**: Verify IAM role permissions
