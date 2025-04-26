# ARM 호환성 분석기 - 배포 가이드

이 가이드는 ARM 호환성 분석기를 Terraform을 사용하여 AWS Lambda 함수로 배포하는 방법을 설명합니다.

## 사전 요구 사항

1. **AWS 계정 및 자격 증명**: Lambda 함수, IAM 역할 및 CloudWatch 로그 그룹을 생성할 수 있는 권한이 있는 AWS 자격 증명이 필요합니다.
    * AWS CLI 구성: `aws configure`
    * 또는 AWS 환경 변수 설정: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`

2. **Terraform**: Terraform 설치 (버전 >= 1.0.0)
    * 다운로드: <https://www.terraform.io/downloads>
    * 설치 확인: `terraform -v`

3. **Docker**: `terraform apply`를 실행하는 시스템에 Docker가 설치되어 있고 실행 중이어야 합니다. Python 의존성 레이어를 빌드하는 데 필요합니다.
    * 다운로드: <https://www.docker.com/get-started>
    * 설치 확인: `docker --version`

4. **Python 및 종속성 (로컬 개발용)**: Python 3.8+ 및 pip 설치.
    * 로컬에서 실행하거나 테스트하려면 `requirements.txt`에 나열된 종속성을 설치하십시오: `pip install -r requirements.txt`

## 로컬 개발

로컬 개발 및 테스트를 위해 `.env` 파일을 사용하여 환경 변수를 구성할 수 있습니다:

1. `arm/src` 디렉토리에 필요한 변수가 포함된 `.env` 파일을 생성합니다 (`arm/src/.env.sample` 파일을 템플릿으로 사용):

    ```dotenv
    # 로깅
    LOG_LEVEL=INFO

    # GitHub API 액세스
    GITHUB_TOKEN=your_github_token_here

    # DockerHub 액세스 (Docker 이미지 검사용)
    DOCKERHUB_USERNAME=your_dockerhub_username
    DOCKERHUB_PASSWORD=your_dockerhub_password_or_token

    # 분석기 구성 (True/False 로 설정)
    ENABLE_TERRAFORM_ANALYZER=True
    ENABLE_DOCKER_ANALYZER=True
    ENABLE_DEPENDENCY_ANALYZER=True
    ```

2. 분석기를 로컬에서 실행 (`arm/src` 디렉토리에서):

    ```bash
    cd arm/src
    # 로컬 테스트에 필요한 경우 종속성을 설치합니다
    # pip install -r ../requirements.txt
    python lambda_function.py # 또는 로컬 실행 파일이 다른 경우 해당 파일
    ```

`src/config.py` 코드는 Lambda 환경에서 실행 중인지 로컬에서 실행 중인지 자동으로 감지하고, Lambda 환경이 아닌 경우 `.env` 파일을 로드합니다.

## 배포 단계

### 1. Terraform 변수 구성

1. Terraform 디렉토리로 이동:

    ```bash
    cd arm/terraform
    ```

2. 예제 파일을 복사하여 변수 정의 파일 생성:

    ```bash
    cp terraform.tfvars.example terraform.auto.tfvars
    ```

3. `terraform.auto.tfvars` 파일을 편집하여 값 입력:
    * **필수 자격 증명:** `github_token`, `dockerhub_username`, `dockerhub_password` 설정. **민감한 정보가 포함된 이 파일을 커밋하지 마십시오.**
    * **분석기 구성:** 분석기 활성화/비활성화 (`enable_terraform_analyzer`, `enable_docker_analyzer`, `enable_dependency_analyzer`).
    * **선택적 재정의:** 필요에 따라 AWS 리전, Lambda 함수 이름, 역할 이름, 메모리, 타임아웃 또는 태그 조정.

### 2. Terraform으로 배포

`arm/terraform` 디렉토리 내에서 다음 명령어를 실행합니다:

```bash
# Terraform 초기화 (aws, archive 등 프로바이더 다운로드)
terraform init

# Terraform이 적용할 변경 사항 미리보기
terraform plan

# AWS에 변경 사항 적용 및 배포
terraform apply
```

메시지가 표시되면 적용 작업을 확인합니다.

**`terraform apply`가 수행하는 작업:**

* `archive_file` 데이터 소스를 사용하여 `../src` 디렉토리의 내용을 `.env`, `.env.sample`, `__pycache__/` 등의 파일을 **제외**하고 `terraform/deployment_package.zip`으로 압축합니다.
* `../requirements.txt` 파일이 변경된 경우, Docker를 사용하여 Python 의존성을 빌드하는 `local-exec` 프로비저너를 실행하고 `terraform/python-deps-layer.zip` 파일을 생성합니다.
* AWS 계정에 IAM 역할, Lambda 레이어 버전(`python-deps-layer.zip` 사용), Lambda 함수(`deployment_package.zip` 사용 및 레이어 연결), CloudWatch 로그 그룹을 생성/업데이트합니다.
* `terraform.auto.tfvars`의 변수들을 Lambda 함수 환경 변수로 주입합니다.

성공적으로 완료되면 Terraform은 생성된 리소스에 대한 정보를 출력합니다.

### 3. analyzer.py의 Lambda 함수 이름 업데이트

배포가 성공적으로 완료되면 Terraform은 Lambda 함수 이름을 출력합니다. 이 이름을 `analyzer.py`에 업데이트해야 합니다:

1. `analyzer.py` 파일을 엽니다
2. Terraform 출력의 Lambda 함수 이름으로 `ARM_ANALYSIS_LAMBDA_FUNCTION_NAME` 변수를 업데이트합니다:

   ```python
   ARM_ANALYSIS_LAMBDA_FUNCTION_NAME = "arm-compatibility-analyzer"  # Terraform 출력의 Lambda 함수 이름으로 업데이트하세요
   ```

## Lambda 함수 호출

Lambda 함수는 `github_url` 매개변수가 포함된 JSON 페이로드를 예상합니다:

```bash
# AWS CLI 사용 (사용자 정의한 경우 함수 이름 변경)
aws lambda invoke \
  --function-name arm-compatibility-analyzer \
  --payload '{"github_url":"https://github.com/username/repo-to-analyze"}' \
  response.json

# 결과 확인
cat response.json
```

또는 프로그래밍 방식으로 사용하는 언어의 AWS SDK를 사용하여 함수를 호출할 수 있습니다.

## 정리

Terraform으로 생성된 모든 리소스를 제거하려면:

```bash
# arm/terraform 디렉토리 내에서
terraform destroy
```

메시지가 표시되면 삭제 작업을 확인합니다.

## 문제 해결

* **Terraform 오류**: 출력을 주의 깊게 읽어보십시오. 일반적인 문제로는 자격 증명 누락, Docker 미실행, AWS 권한 오류 등이 있습니다. 새 프로바이더를 추가한 경우 `terraform init`을 실행하십시오.
* **Lambda 실행 오류**: 함수의 AWS CloudWatch 로그를 확인하십시오 (예: `/aws/lambda/arm-compatibility-analyzer`). 로그 그룹 이름은 Terraform 출력에서 확인할 수 있습니다.
* **구성**: AWS 콘솔을 통해 Lambda 함수 설정에서 환경 변수를 확인하십시오 (Terraform이 `terraform.auto.tfvars`에서 설정해야 함).
* **의존성**: `requirements.txt`가 올바른지 확인하십시오. `terraform apply` 중 `local-exec` 레이어 빌드 단계의 로그에서 Docker 오류를 확인하십시오.
* **권한**: IAM 역할(기본값: `arm-compatibility-analyzer-role`)에 필요한 권한이 있는지 확인하십시오 (`AWSLambdaBasicExecutionRole`이 기본적으로 연결됨).
