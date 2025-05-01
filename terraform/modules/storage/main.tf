# This main.tf file within the storage module defines storage-related resources.

# EBS Optimizer Lambda 함수 배포
# 범용 Lambda 모듈 (../../modules/lambda)을 호출합니다.
module "ebs_optimizer_lambda" {
  source = "../../modules/lambda" # 범용 Lambda 모듈 경로 (storage 모듈 기준 상대 경로)

  # 모듈에 전달할 변수들
  function_name = var.ebs_lambda_function_name # storage 모듈 변수 사용
  source_dir    = "${path.module}/../../../lambda_ebs_optimizer" # EBS 코드 경로 수정 (storage 모듈 기준 상대 경로)
  handler       = var.ebs_lambda_handler      # storage 모듈 변수 사용
  runtime       = var.ebs_lambda_runtime      # storage 모듈 변수 사용
  role_arn      = aws_iam_role.lambda_role.arn # storage 모듈 iam.tf에서 정의된 역할 ARN 사용
  timeout       = var.ebs_lambda_timeout      # storage 모듈 변수 사용
  memory_size   = var.ebs_lambda_memory     # storage 모듈 변수 사용

  # 필요 시 환경 변수 등 추가 전달
  # environment_variables = {
  #   EBS_S3_BUCKET = var.ebs_s3_bucket
  # }
}

# 다른 Storage 관련 리소스(예: S3 버킷, EFS 등)가 필요하면 여기에 정의
