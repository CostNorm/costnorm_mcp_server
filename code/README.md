# AWS 잠재적 미사용 리소스 식별 도구

이 프로젝트는 AWS 비용 데이터를 분석하여 잠재적으로 사용되지 않는 리소스를 식별하는 데 도움을 줍니다. 비용 추세를 기반으로 사용량이 크게 줄어든 서비스-오퍼레이션 조합을 찾고, 해당 **서비스**에 속하면서 최근(어제) 비용이 발생한 특정 리소스 ID 목록을 제공합니다.

## 작동 방식

프로젝트는 두 단계로 작동합니다:

1.  **비용 추세 분석 (`cost_analyzer.py`)**:
    *   지정된 S3 버킷에서 매일 생성된 비용 요약 CSV 파일 (`_sorted_costs.csv` 형식 가정)을 읽습니다.
    *   최근 기간(예: 지난 7일)과 비교 기간(예: 그 이전 30일)의 비용을 `Service::Operation` 단위로 집계합니다.
    *   다음 기준을 만족하는 `Service::Operation`을 "잠재적 미사용" 후보로 식별합니다:
        *   **비용 감소**: 비교 기간 비용이 특정 절대값(`MIN_COMPARISON_COST`) 이상이고, 최근 기간 비용이 비교 기간 비용의 특정 비율(`COST_THRESHOLD_PERCENTAGE`) 미만인 경우.
2.  **최근 비용 발생 리소스 ID 조회 (`lambda_function.py` 호출 -> `athena_query_handler.py` 실행)**:
    *   AWS Lambda 함수 (`lambda_function.py`) 내에서 실행됩니다.
    *   먼저 `cost_analyzer.py` 모듈을 호출하여 잠재적 미사용 `Service::Operation` 목록을 얻습니다.
    *   `athena_query_handler.py` 모듈을 호출하여 다음 작업을 수행합니다:
        *   1단계에서 얻은 목록에서 고유한 **서비스 이름**(`product_product_name`)을 추출합니다.
        *   추출된 서비스 이름들에 대해 AWS CUR(Cost and Usage Report) 데이터를 저장하는 Athena 테이블을 쿼리합니다.
        *   쿼리를 통해 해당 **서비스 이름**에 속하면서 **어제** 날짜 기준으로 실제 비용(`line_item_unblended_cost > 0`)이 발생한 고유한 리소스 ID(`line_item_resource_id`)를 찾습니다. (쿼리 시 오퍼레이션은 고려하지 않습니다.)
    *   최종적으로 식별된 리소스 ID 목록을 반환합니다.

## 구성 요소

*   **`cost_analyzer.py`**: S3에서 일별 비용 요약 데이터를 읽고 분석하여 잠재적 미사용 `Service::Operation` 목록을 생성하는 로직을 포함합니다.
*   **`athena_query_handler.py`**: `cost_analyzer` 결과에서 서비스 이름을 추출하고, Athena를 쿼리하여 해당 서비스에 속하는 어제 비용 발생 리소스 ID를 찾는 로직을 포함합니다.
*   **`lambda_function.py`**: AWS Lambda 핸들러 함수입니다. `cost_analyzer`와 `athena_query_handler`를 순서대로 호출하고 전체 워크플로우를 관리하며 최종 결과를 반환합니다.

## 설정 및 구성

이 프로젝트를 올바르게 실행하려면 다음 환경 변수를 설정해야 합니다 (주로 Lambda 함수 환경 변수로 설정).

**`cost_analyzer.py` 필요 환경 변수:**

*   `S3_BUCKET_NAME`: 일별 비용 요약 CSV 파일이 저장된 S3 버킷 이름 (필수)
*   `RECENT_PERIOD_DAYS`: 최근 비용 집계 기간 (일수, 기본값: 7)
*   `COMPARISON_PERIOD_DAYS`: 비교 대상 비용 집계 기간 (일수, 기본값: 30)
*   `COST_THRESHOLD_PERCENTAGE`: 비용 감소 기준 백분율 (기본값: 1.0, 즉 1%)
*   `MIN_COMPARISON_COST`: 비용 감소 기준을 적용하기 위한 최소 비교 기간 비용 (기본값: 0.01, 즉 $0.01)
*   `FILE_SUFFIX`: S3에서 찾을 일별 비용 요약 파일의 접미사 (기본값: `_sorted_costs.csv`)

**`athena_query_handler.py` 필요 환경 변수 (Lambda 함수 통해 설정):**

*   `ATHENA_DATABASE`: AWS CUR 데이터가 있는 Athena 데이터베이스 이름 (필수)
*   `ATHENA_TABLE`: AWS CUR 데이터가 있는 Athena 테이블 이름 (필수)
*   `ATHENA_QUERY_OUTPUT_LOCATION`: Athena 쿼리 결과를 저장할 S3 경로 (필수, 예: `s3://your-bucket/athena-results/`)

## 배포 및 실행

1.  **사전 준비**:
    *   AWS CUR이 활성화되어 있고, 관련 데이터가 S3에 저장되고 Athena에서 쿼리 가능한 상태여야 합니다. 테이블이 년/월 등으로 파티셔닝되어 있다면 성능에 도움이 됩니다.
    *   `cost_analyzer.py`가 읽을 일별 비용 요약 CSV 파일을 지정된 S3 버킷(`S3_BUCKET_NAME`)에 `YYMMDD{FILE_SUFFIX}` 형식으로 저장하는 프로세스가 필요합니다.
2.  **Lambda 배포**:
    *   `lambda_function.py`, `cost_analyzer.py`, `athena_query_handler.py` 파일을 함께 패키징하여 AWS Lambda 함수를 생성합니다.
    *   필요한 라이브러리(예: `boto3`)를 포함해야 합니다.
    *   Lambda 함수 실행 역할(Execution Role)에 필요한 IAM 권한을 부여합니다:
        *   S3 비용 요약 버킷(`S3_BUCKET_NAME`) 읽기 권한 (`s3:GetObject`)
        *   Athena 쿼리 실행 권한 (`athena:StartQueryExecution`, `athena:GetQueryExecution`, `athena:GetQueryResults`)
        *   Athena 결과 저장 S3 버킷(`ATHENA_QUERY_OUTPUT_LOCATION`) 쓰기/읽기 권한 (`s3:PutObject`, `s3:GetObject`)
        *   (CloudWatch Logs 권한 등 Lambda 기본 권한)
    *   위에서 설명한 환경 변수들을 Lambda 함수에 설정합니다.
3.  **실행**:
    *   Lambda 함수를 트리거합니다 (예: CloudWatch Events 스케줄 사용).
    *   함수가 실행되면 비용 분석 및 Athena 쿼리를 수행합니다.

## 출력 결과

Lambda 함수는 JSON 형식의 응답을 반환합니다. `body` 필드에는 다음 정보가 포함됩니다:

*   `message`: 실행 결과 요약 메시지.
*   `resource_ids_with_cost_yesterday`: **최종 결과.** `cost_analyzer`가 식별한 잠재적 미사용 후보의 **서비스 이름**과 동일한 서비스에 속하면서, 어제 비용이 발생한 리소스 ID 목록입니다.

```json
{
  "statusCode": 200,
  "body": {
    "message": "Cost analysis identified 8 potentially unused Service::Operation pairs based on cost decrease. Found 5 unique resource IDs with costs yesterday for the associated services.",
    "resource_ids_with_cost_yesterday": [
      "arn:aws:sagemaker:us-west-2:xxxxx:space/d-xxxxx/xxxxx",
      "eni-0abcdef1234567890",
      "vol-0fedcba9876543210",
      "i-0123456789abcdef0",
      "arn:aws:bedrock:us-east-1::provisioned-model/xxxxx"
    ]
    // "potentially_unused_resource_details" 는 기본적으로 포함되지 않음 (필요시 lambda_function.py 수정)
  }
}
```