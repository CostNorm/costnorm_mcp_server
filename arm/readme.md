# ARM Compatibility Analyzer - Deployment Guide

This guide explains how to deploy the ARM Compatibility Analyzer as an AWS Lambda function using Terraform.

## Prerequisites

1. **AWS Account & Credentials**: You need AWS credentials with permissions to create Lambda functions, IAM roles, and CloudWatch Log Groups.
    * Configure your AWS CLI: `aws configure`
    * Or set AWS environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`

2. **Terraform**: Download and install Terraform (version >= 1.0.0)
    * Download from: <https://www.terraform.io/downloads>
    * Verify installation: `terraform -v`

3. **Docker**: Docker must be installed and running on the machine where you execute `terraform apply`. This is required for building the Python dependency layer.
    * Download from: <https://www.docker.com/get-started>
    * Verify installation: `docker --version`

4. **Python & Dependencies (for Local Development)**: Python 3.8+ and pip installed.
    * Install dependencies listed in `requirements.txt` if you plan to run or test locally: `pip install -r requirements.txt`

## Local Development

For local development and testing, you can use a `.env` file to configure environment variables:

1. Create a `.env` file in the `arm/src` directory with the necessary variables (see `arm/src/.env.sample` for a template):

    ```dotenv
    # Logging
    LOG_LEVEL=INFO

    # GitHub API Access
    GITHUB_TOKEN=your_github_token_here

    # DockerHub Access (for Docker image inspection)
    DOCKERHUB_USERNAME=your_dockerhub_username
    DOCKERHUB_PASSWORD=your_dockerhub_password_or_token

    # Analyzer Configuration (set to True/False)
    ENABLE_TERRAFORM_ANALYZER=True
    ENABLE_DOCKER_ANALYZER=True
    ENABLE_DEPENDENCY_ANALYZER=True
    ```

2. Run the analyzer locally (from the `arm/src` directory):

    ```bash
    cd arm/src
    # Make sure dependencies are installed locally if needed for testing
    # pip install -r ../requirements.txt
    python lambda_function.py # Or your local entry point if different
    ```

The code in `src/config.py` automatically detects whether it's running in Lambda or locally and will load the `.env` file if it's not in a Lambda environment.

## Deployment Steps

### 1. Configure Terraform Variables

1. Navigate to the Terraform directory:

    ```bash
    cd arm/terraform
    ```

2. Create your variable definitions file by copying the example:

    ```bash
    cp terraform.auto.tfvars.example terraform.auto.tfvars
    ```

3. Edit `terraform.auto.tfvars` and fill in your values:
    * **Required Credentials:** Set `github_token`, `dockerhub_username`, and `dockerhub_password`. **Do not commit this file with sensitive credentials.**
    * **Analyzer Configuration:** Enable/disable analyzers (`enable_terraform_analyzer`, `enable_docker_analyzer`, `enable_dependency_analyzer`).
    * **Optional Overrides:** Adjust AWS region, Lambda function name, role name, memory, timeout, or tags if needed.

### 2. Deploy with Terraform

From within the `arm/terraform` directory, run the following commands:

```bash
# Initialize Terraform (downloads providers like aws and archive)
terraform init

# Preview the changes Terraform will make
terraform plan

# Apply the changes to deploy to AWS
terraform apply
```

Confirm the apply operation when prompted.

**What `terraform apply` does:**

* Uses the `archive_file` data source to zip the contents of the `../src` directory, **excluding** files like `.env`, `.env.sample`, `__pycache__/`, etc., into `terraform/deployment_package.zip`.
* If `../requirements.txt` has changed, it runs the `local-exec` provisioner which uses Docker to build the Python dependencies and creates `terraform/python-deps-layer.zip`.
* Creates/updates the IAM role, Lambda layer version (from `python-deps-layer.zip`), Lambda function (using `deployment_package.zip`), and CloudWatch Log Group in your AWS account.
* Injects variables from `terraform.auto.tfvars` into the Lambda function's environment.

Terraform will output information about the created resources upon successful completion.

## Invoking the Lambda Function

The Lambda function expects a JSON payload with a `github_url` parameter:

```bash
# Using AWS CLI (replace function-name if you customized it)
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
# From within the arm/terraform directory
terraform destroy
```

Confirm the destroy operation when prompted.

## Troubleshooting

* **Terraform Errors**: Read the output carefully. Common issues include missing credentials, Docker not running, or AWS permissions errors. Run `terraform init` if you add new providers.
* **Lambda Execution Errors**: Check AWS CloudWatch logs for the function (e.g., `/aws/lambda/arm-compatibility-analyzer`). Log group name is available in Terraform outputs.
* **Configuration**: Verify environment variables in the Lambda function settings via the AWS Console (Terraform should set these from `terraform.auto.tfvars`).
* **Dependencies**: Ensure `requirements.txt` is correct. Check the logs from the `local-exec` layer build step during `terraform apply` for Docker errors.
* **Permissions**: Verify the IAM role (`arm-compatibility-analyzer-role` by default) has the necessary permissions (`AWSLambdaBasicExecutionRole` is attached by default).
