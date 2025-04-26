# ARM 호환성 분석기 - 배포 가이드

이 가이드는 ARM 호환성 분석기를 Terraform을 사용하여 AWS Lambda 함수로 배포하는 방법을 설명합니다.

## 사전 요구 사항

1. **AWS 계정 및 자격 증명**: Lambda 함수, IAM 역할 및 CloudWatch 로그 그룹을 생성할 수 있는 권한이 있는 AWS 자격 증명이 필요합니다.
   - AWS CLI 구성: `aws configure`
   - 또는 AWS 환경 변수 설정: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`

2. **Terraform**: Terraform 설치 (버전 >= 1.0.0)
   - 다운로드: <https://www.terraform.io/downloads>
   - 설치 확인: `terraform -v`

3. **Python 및 종속성**: Python 3.8+ 및 pip 설치
   - 로컬 테스트를 위한 종속성 설치: `pip install -r requirements.txt`

## 로컬 개발

로컬 개발 및 테스트를 위해 `.env` 파일을 사용하여 환경 변수를 구성할 수 있습니다:

1. `arm/src` 디렉토리에 다음 변수가 포함된 `.env` 파일을 생성합니다:

   ```python
   # 로깅
   LOG_LEVEL=INFO
   
   # GitHub API 액세스
   GITHUB_TOKEN=your_github_token_here
   
   # DockerHub 액세스 (Docker 이미지 검사용)
   DOCKERHUB_USERNAME=your_dockerhub_username
   DOCKERHUB_PASSWORD=your_dockerhub_password_or_token
   
   # 분석기 구성
   ENABLE_TERRAFORM_ANALYZER=True
   ENABLE_DOCKER_ANALYZER=True
   ENABLE_DEPENDENCY_ANALYZER=True
   ```

2. 분석기를 로컬에서 실행:

   ```bash
   cd arm/src
   python -m lambda_function
   ```

코드는 Lambda 환경에서 실행 중인지 로컬에서 실행 중인지 자동으로 감지하고, Lambda 환경이 아닌 경우 `.env` 파일을 로드합니다.

## 배포 단계

### 1. 배포 패키지 준비

소스 코드와 종속성이 포함된 배포 패키지를 생성합니다:

```bash
# 임시 빌드 디렉토리 생성
mkdir -p build

# 빌드 디렉토리에 종속성 설치
pip install -r requirements.txt --target ./build

# 빌드 디렉토리에 소스 코드 복사
cp -r ./arm/src/* ./build/

# ZIP 파일 생성
cd build
zip -r ../arm/deployment_package.zip .
cd ..

# 정리
rm -rf build
```

### 2. Terraform 변수 구성

1. Terraform 디렉토리로 이동:

   ```bash
   cd arm/terraform
   ```

2. 변수 정의 파일 생성:

   ```bash
   cp terraform.tfvars.example terraform.auto.tfvars
   ```

3. `terraform.auto.tfvars` 파일을 편집하여 값 입력:
   - 필수 자격 증명 설정: `github_token`, `dockerhub_username` 등
   - 활성화할 분석기 구성
   - 필요에 따라 다른 Lambda 설정 조정

### 3. Terraform으로 배포

```bash
# Terraform 초기화
terraform init

# 변경 사항 미리보기
terraform plan

# 변경 사항 적용
terraform apply
```

메시지가 표시되면 적용 작업을 확인합니다. Terraform은 생성된 리소스에 대한 정보를 출력합니다.

## Lambda 함수 호출

Lambda 함수는 `github_url` 매개변수가 포함된 JSON 페이로드를 예상합니다:

```bash
# AWS CLI 사용
aws lambda invoke \
  --function-name arm-compatibility-analyzer \
  --payload '{"github_url":"https://github.com/username/repo-to-analyze"}' \
  response.json

# 결과 확인
cat response.json
```

또는 프로그래밍 방식으로 AWS SDK를 사용하여 함수를 호출할 수 있습니다.

## 정리

Terraform으로 생성된 모든 리소스를 제거하려면:

```bash
terraform destroy
```

메시지가 표시되면 삭제 작업을 확인합니다.

## 문제 해결

- **CloudWatch 로그**: `/aws/lambda/arm-compatibility-analyzer`에서 AWS CloudWatch 로그 확인
- **Lambda 구성**: AWS 콘솔에서 환경 변수 확인
- **배포 패키지**: 배포 패키지에 필요한 모든 종속성이 포함되어 있는지 확인
- **권한**: IAM 역할 권한 확인
