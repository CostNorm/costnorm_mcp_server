# Outputs from the Storage module

output "ebs_optimizer_lambda_function_name" {
  description = "The name of the deployed EBS Optimizer Lambda function."
  # 하위 모듈(ebs_optimizer_lambda)의 출력값을 다시 내보냄
  value       = module.ebs_optimizer_lambda.function_name 
}

# 필요 시 다른 출력값 추가 (예: EFS ID 등)
