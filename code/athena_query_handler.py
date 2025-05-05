import boto3
import time
import os
from datetime import datetime, timedelta, timezone

# Athena 관련 환경 변수
ATHENA_DATABASE = os.environ.get('ATHENA_DATABASE', 'MyProgrammaticCUR')
ATHENA_TABLE = os.environ.get('ATHENA_TABLE', 'my_programmatic_c_u_r')
ATHENA_QUERY_OUTPUT_LOCATION = os.environ.get('ATHENA_QUERY_OUTPUT_LOCATION', 's3://cur-test-dhkim/athena-results') # 필수!

athena_client = boto3.client('athena','ap-northeast-2')

def get_resource_ids_with_cost_yesterday(service_operation_list):
    """
    주어진 Service::Operation 목록에 해당하는 Service들에 대해
    어제 비용이 발생한 리소스 ID를 Athena에서 조회합니다. (Operation은 무시)
    """
    if not service_operation_list:
        print("No Service::Operation provided to query Athena.")
        return []

    if not ATHENA_QUERY_OUTPUT_LOCATION:
        raise ValueError("ATHENA_QUERY_OUTPUT_LOCATION environment variable is not set.")

    # 1. 고유한 서비스 이름 추출
    unique_service_names = set()
    for item in service_operation_list:
        resource_key = item.get('resource', '')
        try:
            service, _ = resource_key.split('::', 1) # Operation 부분은 무시
            if service: # 빈 문자열이 아닌 경우만 추가
                unique_service_names.add(service)
        except ValueError:
            print(f"Warning: Could not parse Service from key '{resource_key}'. Skipping.")

    if not unique_service_names:
        print("No valid service names extracted for Athena query.")
        return []

    print(f"Querying Athena for resource IDs of {len(unique_service_names)} unique services...")
    print(f"  Services: {', '.join(list(unique_service_names)[:10])}...") # 일부만 로깅

    # 2. WHERE 절 조건 생성 (IN 연산자 사용)
    formatted_service_names = []
    for name in unique_service_names:
        # SQL Injection 방지 및 작은따옴표 처리
        escaped_name = name.replace("'", "''")
        formatted_service_names.append(f"'{escaped_name}'")

    conditions_sql = f"product_product_name IN ({', '.join(formatted_service_names)})"

    print(conditions_sql)

    # 어제 날짜 계산 (UTC 기준) -> 파티션 필터용으로는 계속 필요
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    # Athena 쿼리 생성 (원래의 올바른 구조)
    query = f"""
    SELECT DISTINCT line_item_resource_id
    FROM "{ATHENA_DATABASE}"."{ATHENA_TABLE}"
    WHERE
        CAST(line_item_usage_start_date AS DATE) = (current_date - interval '1' day) -- 2. 날짜 필터 (AND 필요)
        AND {conditions_sql} -- 3. 서비스 필터 (AND 필요)
        AND line_item_unblended_cost > 0 -- 4. 비용 필터 (AND 필요)
        AND line_item_resource_id IS NOT NULL -- 5. ID 필터 (AND 필요)
        AND line_item_resource_id <> ''
    """

    print(f"Executing Athena Query (first 1000 chars): {query[:1000]}...")
    query_context = {'Database': ATHENA_DATABASE}

    try:
        execution = athena_client.start_query_execution(
            QueryString=query,
            QueryExecutionContext=query_context,
            ResultConfiguration={'OutputLocation': ATHENA_QUERY_OUTPUT_LOCATION}
        )
        query_execution_id = execution['QueryExecutionId']
        print(f"  Athena Query Execution ID: {query_execution_id}")

        # 쿼리 완료 대기
        while True:
            stats = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
            status = stats['QueryExecution']['Status']['State']
            if status in ['SUCCEEDED', 'FAILED', 'CANCELLED']:
                if status == 'SUCCEEDED':
                    print("  Athena Query SUCCEEDED. Fetching results...")
                    break
                else:
                    reason = stats['QueryExecution']['Status'].get('StateChangeReason', 'Unknown reason')
                    error_msg = f"Athena query {status}. Reason: {reason}"
                    print(f"  {error_msg}")
                    raise Exception(error_msg)
            print(f"  Athena Query status: {status}. Waiting...")
            time.sleep(2)

        # 결과 가져오기 (Paginator 사용)
        paginator = athena_client.get_paginator('get_query_results')
        results_iterator = paginator.paginate(QueryExecutionId=query_execution_id)

        resource_ids = set() # 중복 제거

        for page in results_iterator:
            rows = page['ResultSet']['Rows']
            if not rows: continue
            # 첫 행(헤더) 건너뛰기
            for row in rows[1:]:
                try:
                    res_id = row['Data'][0].get('VarCharValue')
                    if res_id:
                        resource_ids.add(res_id)
                except (KeyError, IndexError, TypeError) as parse_err:
                    print(f"Warning: Could not parse resource ID from row: {row}. Error: {parse_err}")

        print(f"  Fetched {len(resource_ids)} distinct resource IDs from Athena.")
        return list(resource_ids)

    except Exception as e:
        print(f"  Error during Athena query execution or result fetching: {e}")
        raise # 예외를 다시 발생시켜 lambda_handler에서 처리하도록 함 