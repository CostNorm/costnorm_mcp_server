provider "aws" {
  region = var.region
  profile = var.profile
}

# Storage 관련 리소스 배포 (Storage 모듈 호출)
module "storage" {
  source = "./modules/storage"
  
  # Storage 모듈에 필요한 공통 변수 전달 (현재는 없음)
  # 예: deploy_ebs_optimizer = true 
}

# Compute 관련 리소스 배포 (Compute 모듈 호출 - 추후 구현)
# module "compute" {
#   source = "./modules/compute"
# }

# Networking 관련 리소스 배포 (Networking 모듈 호출 - 추후 구현)
# module "networking" {
#   source = "./modules/networking"
# }

# 루트 레벨에서 필요한 Output 정의
# 예: 전체 애플리케이션 엔드포인트 등

# Storage 모듈의 Output 참조 예시 (필요한 경우)
# output "ebs_lambda_name_from_storage" {
#   description = "EBS Lambda function name exported from storage module"
#   value = module.storage.ebs_optimizer_lambda_function_name
# } 