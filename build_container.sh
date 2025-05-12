#!/bin/bash

AWS_PROFILE="costnorm"
AWS_REGION="ap-northeast-2"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --profile ${AWS_PROFILE} --query "Account" --output text)


# Build the container
docker build -t ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/costnorm-mcp-server:latest .

# Create ECR repository if it doesn't exist
aws ecr create-repository --repository-name costnorm-mcp-server --region ${AWS_REGION} --profile ${AWS_PROFILE}

# Login to ECR
aws ecr get-login-password --region ${AWS_REGION} --profile ${AWS_PROFILE} | docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

# Push the container to ECR
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/costnorm-mcp-server:latest

# Run the container
docker run -d -p 8080:8080 ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/costnorm-mcp-server:latest
