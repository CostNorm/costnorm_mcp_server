import json
import os
# boto3, time 등 Athena 관련 import 제거

import cost_analyzer
import athena_query_handler

# Athena 관련 환경 변수 설정은 athena_query_handler.py 로 이동

# Lambda 핸들러 함수
def lambda_handler(event, context):
    """Lambda 핸들러: 비용 분석 후 잠재적 미사용 리소스의 ID를 Athena에서 조회"""
    print("--- Starting Unused Resource Identification Lambda --- ")

    potentially_unused_resource_details = []
    found_resource_ids = []
    status_code = 500
    message = "Lambda execution started."

    try:
        # 1. 비용 분석 모듈 호출하여 잠재적 미사용 리소스 정보 얻기
        print("\nStep 1: Analyzing cost data using cost_analyzer...")
        potentially_unused_resource_details = cost_analyzer.find_potentially_unused_resources()
        cost_analysis_message = f"Cost analysis identified {len(potentially_unused_resource_details)} potentially unused Service::Operation pairs based on cost decrease."
        print(cost_analysis_message)
        print(f"  Details (first 5): {potentially_unused_resource_details[:5]}")

        # 2. Athena 쿼리 실행 (잠재적 미사용 리소스가 있을 경우)
        if not potentially_unused_resource_details:
            print("\nStep 2: No potentially unused resources found by cost analysis. Skipping Athena query.")
            message = cost_analysis_message + " No resource IDs to query."
            status_code = 200
        else:
            print(f"\nStep 2: Querying Athena for resource IDs with cost yesterday...")
            # athena_query_handler 모듈의 함수 호출
            found_resource_ids = athena_query_handler.get_resource_ids_with_cost_yesterday(
                potentially_unused_resource_details
            )
            message = f"{cost_analysis_message} Found {len(found_resource_ids)} unique resource IDs with costs yesterday for the associated services."
            status_code = 200

    except ValueError as ve: # 환경 변수 오류 등
        message = f"Configuration error: {ve}"
        print(message)
        status_code = 400 # 설정 오류는 Bad Request 로 처리
    except Exception as e: # 비용 분석 또는 Athena 쿼리 중 발생한 모든 예외
        message = f"An error occurred: {e}"
        print(f"Error during execution: {e}")
        import traceback
        traceback.print_exc()
        status_code = 500 # 내부 서버 오류

    print(f"--- Lambda Finished (Status: {status_code}) --- ")

    # 최종 응답 반환
    return {
        'statusCode': status_code,
        'body': json.dumps({
            'message': message,
            # 비용 분석 결과 상세 정보는 로깅으로 확인하고, 최종 응답에는 ID 목록만 포함 (필요시 수정 가능)
            # 'potentially_unused_resource_details': potentially_unused_resource_details,
            'resource_ids_with_cost_yesterday': found_resource_ids
        }, indent=2)
    }

# --- 로컬 테스트용 코드 (수정 불필요) ---
if __name__ == '__main__':

    result = lambda_handler(None, None)
    print("\n--- Function Result ---")
    try:
        print(result)
        print(f"Status Code: {result.get('statusCode')}")
        body_content = json.loads(result.get('body', '{}'))
        print("Body:")
        print(json.dumps(body_content, indent=2))
    except Exception as e:
            print(f"Error parsing result: {e}")
            print(result)