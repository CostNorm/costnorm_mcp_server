from typing import Any, Optional, Dict, List
import httpx
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from mcp.server.sse import SseServerTransport
from starlette.requests import Request
from starlette.routing import Mount, Route
from mcp.server import Server
import uvicorn
import boto3
from botocore.exceptions import ClientError
from datetime import datetime, timedelta, timezone
import json
import asyncio
import os
from loguru import logger

# Initialize FastMCP server for Weather tools (SSE)
mcp = FastMCP("instance_manager")

# Constants
EXCLUDE_TAG_KEY = "CostNormExclude"
# 배포된 Lambda 함수 이름 (Terraform 출력 또는 고정값)
# 실제 환경에서는 Terraform 출력값을 환경 변수 등으로 주입하는 것이 좋음
EBS_OPTIMIZER_LAMBDA_NAME = "ebs-optimizer-lambda" 
# Lambda 함수가 실제로 배포된 리전
LAMBDA_DEPLOYMENT_REGION = "ap-northeast-2" 

@mcp.tool()
async def get_instance_info() -> dict:
    """Get detailed EC2 instance information across regions, including CPU usage 
    and optimization recommendations, returned as a JSON object.

    Returns:
        dict: A dictionary with keys 'optimizations_needed', 'instances_ok', and 'errors'.
              'optimizations_needed': List of instances needing scaling adjustments.
              'instances_ok': List of instances with normal CPU usage.
              'errors': List of errors encountered during data fetching.
    """
    # Use default session credentials
    ec2_client = boto3.client('ec2', region_name='us-east-1')
    # Initialize result structure
    results = {
        "optimizations_needed": [],
        "instances_ok": [],
        "errors": []
    }
    regions = []
    now = datetime.now(timezone.utc)
    start_time = now - timedelta(hours=1)

    # Get available regions
    try:
        regions_response = ec2_client.describe_regions()
        regions = [region['RegionName'] for region in regions_response.get('Regions', [])]
    except ClientError as e:
        results["errors"].append({"region": "global", "error_message": f"Error fetching AWS regions: {e}"})
        return results # Return early if regions cannot be fetched
    except Exception as e:
        results["errors"].append({"region": "global", "error_message": f"An unexpected error occurred while fetching regions: {e}"})
        return results

    if not regions:
        results["errors"].append({"region": "global", "error_message": "No accessible AWS regions found."})
        return results

    # Iterate through regions and fetch instance data
    for region in regions:
        try:
            regional_ec2_client = boto3.client('ec2', region_name=region)
            regional_cw_client = boto3.client('cloudwatch', region_name=region)
            paginator = regional_ec2_client.get_paginator('describe_instances')
            page_iterator = paginator.paginate(
                Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
            )

            for page in page_iterator:
                for reservation in page.get('Reservations', []):
                    for instance in reservation.get('Instances', []):
                        # 추가 시작: 제외 태그 확인
                        instance_tags = instance.get('Tags', [])
                        should_exclude = False
                        for tag in instance_tags:
                            if tag.get('Key') == EXCLUDE_TAG_KEY:
                                should_exclude = True
                                break
                        
                        if should_exclude:
                            instance_id_for_log = instance.get('InstanceId', 'N/A')
                            print(f"Excluding instance {instance_id_for_log} due to tag '{EXCLUDE_TAG_KEY}'.")
                            continue # 이 인스턴스 처리 건너뛰기
                        # 추가 끝

                        instance_id = instance.get('InstanceId', 'N/A')
                        instance_type = instance.get('InstanceType', 'N/A')
                        state = instance.get('State', {}).get('Name', 'N/A')
                        launch_time = instance.get('LaunchTime')
                        launch_time_str = launch_time.isoformat() if launch_time else 'N/A' # Use ISO format

                        cpu_avg = None
                        recommendation = None
                        cpu_usage_str = 'N/A' # For display if needed, not part of core data

                        try:
                            response = regional_cw_client.get_metric_statistics(
                                Namespace='AWS/EC2',
                                MetricName='CPUUtilization',
                                Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
                                StartTime=start_time,
                                EndTime=now,
                                Period=3600,
                                Statistics=['Average'],
                                Unit='Percent'
                            )
                            if response['Datapoints']:
                                cpu_avg = response['Datapoints'][0]['Average']
                                cpu_usage_str = f"{cpu_avg:.1f}%" # Keep for potential logging/debugging
                                
                                # Determine recommendation
                                if cpu_avg > 80.0:
                                    recommendation = "scale_up"
                                elif cpu_avg < 20.0:
                                    recommendation = "scale_down"
                                else:
                                    recommendation = "ok"
                            else:
                                recommendation = "pending_data"
                                
                        except ClientError as cw_error:
                            print(f"Could not get CloudWatch metrics for {instance_id} in {region}: {cw_error}")
                            recommendation = "error_fetching_cpu"
                            results["errors"].append({
                                "region": region,
                                "instance_id": instance_id,
                                "error_message": f"CloudWatch ClientError: {cw_error}"
                            })
                        except Exception as cw_e:
                            print(f"Unexpected error getting CloudWatch metrics for {instance_id} in {region}: {cw_e}")
                            recommendation = "error_fetching_cpu"
                            results["errors"].append({
                                "region": region,
                                "instance_id": instance_id,
                                "error_message": f"Unexpected CloudWatch Error: {cw_e}"
                            })

                        # Prepare instance data dictionary
                        instance_data = {
                            "region": region,
                            "instance_id": instance_id,
                            "instance_type": instance_type,
                            "metric": "CPUUtilization",
                            "value": f"{round(cpu_avg, 1)}%" if cpu_avg is not None else None,
                            # Include recommendation only if action is needed or ok
                            # recommendation field will be added when categorizing below
                        }
                        
                        # Categorize instance
                        if recommendation == "scale_up" or recommendation == "scale_down":
                             instance_data["recommendation"] = recommendation
                             results["optimizations_needed"].append(instance_data)
                        elif recommendation == "ok":
                             results["instances_ok"].append(instance_data)
                        # Instances with pending_data or error_fetching_cpu are implicitly not OK,
                        # and errors are logged in the errors list.

        except ClientError as e:
            print(f"Could not access region {region}: {e}") 
            results["errors"].append({"region": region, "error_message": f"EC2 ClientError: {e}"})
            continue
        except Exception as e:
            print(f"An unexpected error occurred in region {region}: {e}")
            results["errors"].append({"region": region, "error_message": f"Unexpected EC2 Error: {e}"})
            continue

    # Return the structured results
    return results


@mcp.tool()
async def modify_instance_type(instance_id: str, new_type: str) -> str:
    """Modify the type of a specific EC2 instance.

    Args:
        instance_id: The ID of the instance to modify.
        new_type: The target instance type (e.g., t2.medium).
    """
    # Simulate modifying instance type
    # In a real scenario, this would involve calling a cloud provider API (e.g., ec2.modify_instance_attribute)
    # **WARNING**: Directly calling modification APIs can have real cost and operational impact.
    print(f"Attempting to change instance {instance_id} to type {new_type}...")
    # Simulate success
    success = True 

    if success:
        return f"Successfully modified instance {instance_id} to type {new_type}."
    else:
        # In a real scenario, you might return specific error details
        return f"Failed to modify instance {instance_id}."


def create_starlette_app(mcp_server: Server, *, debug: bool = False) -> Starlette:
    """Create a Starlette application that can server the provied mcp server with SSE."""
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> None:
        async with sse.connect_sse(
                request.scope,
                request.receive,
                request._send,  # noqa: SLF001
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )

    return Starlette(
        debug=debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )


@mcp.tool()
async def analyze_ebs_volumes_tool(
    region: str, # 분석 대상 리전
    volume_id: Optional[str] = None,
    volume_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Analyzes EBS volumes using the deployed Lambda function."""
    logger.info(f"Requesting EBS analysis via Lambda: target_region={region}, volume_id={volume_id}, volume_ids={volume_ids}")

    # costnorm 프로필을 사용하여 AWS 세션 생성
    session = boto3.Session(profile_name='costnorm')
    # 생성된 세션을 통해 Lambda 클라이언트를 함수가 배포된 리전으로 생성
    lambda_client = session.client('lambda', region_name=LAMBDA_DEPLOYMENT_REGION)

    # 페이로드에는 분석 대상 리전(region) 전달
    payload = {
        "operation": "analyze",
        "region": region, # 분석/액션 대상 리전
    }
    if volume_ids:
        payload["volume_ids"] = volume_ids
    elif volume_id:
        payload["volume_ids"] = [volume_id]

    try:
        response = await asyncio.to_thread(
            lambda_client.invoke,
            FunctionName=EBS_OPTIMIZER_LAMBDA_NAME,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )

        response_payload = json.loads(response['Payload'].read().decode('utf-8'))

        # Lambda 응답 상태 코드 확인 (Lambda 함수 자체 오류 처리)
        lambda_status_code = response.get('StatusCode', 200)
        if lambda_status_code != 200:
            logger.error(f"Lambda function execution error (status: {lambda_status_code}): {response_payload}")
            # Lambda 함수에서 반환한 body 내용을 그대로 반환하거나 가공
            error_body = response_payload.get('body', json.dumps(response_payload))
            try: # body가 JSON 문자열이면 파싱
                parsed_error = json.loads(error_body)
                return {"success": False, "error": f"Lambda execution failed (status {lambda_status_code})", "details": parsed_error}
            except json.JSONDecodeError:
                 return {"success": False, "error": f"Lambda execution failed (status {lambda_status_code})", "details": error_body}

        # Lambda 함수 내부 로직 결과 (body에 실제 결과 포함)
        if isinstance(response_payload, dict) and 'body' in response_payload:
             body_content = json.loads(response_payload['body']) # body 내용을 파싱
             logger.info(f"Lambda analysis successful. Summary: {body_content.get('summary')}")
             return body_content # 실제 분석 결과 반환
        else:
             # 예상치 못한 응답 형식
             logger.error(f"Unexpected Lambda response format: {response_payload}")
             return {"success": False, "error": "Unexpected Lambda response format", "details": response_payload}

    except ClientError as e:
        logger.error(f"Failed to invoke Lambda function '{EBS_OPTIMIZER_LAMBDA_NAME}' in {LAMBDA_DEPLOYMENT_REGION}: {e}", exc_info=True)
        return {"success": False, "error": f"Failed to invoke Lambda: {e}"}
    except json.JSONDecodeError as e:
         logger.error(f"Failed to decode Lambda response: {e}", exc_info=True)
         # response 객체 자체를 반환하거나 오류 메시지 생성
         return {"success": False, "error": f"Failed to decode Lambda response: {e}"}
    except Exception as e:
        logger.error(f"Error processing Lambda response: {e}", exc_info=True)
        return {"success": False, "error": f"Error processing Lambda response: {e}"}

@mcp.tool()
async def execute_ebs_action_tool(
    volume_id: str,
    action_type: str,
    region: str, # 액션 대상 리전
) -> Dict[str, Any]:
    """Executes a specific optimization action on an EBS volume via Lambda."""
    logger.info(f"Requesting EBS action via Lambda: target_region={region}, volume={volume_id}, action={action_type}")

    # costnorm 프로필을 사용하여 AWS 세션 생성
    session = boto3.Session(profile_name='costnorm')
    # 생성된 세션을 통해 Lambda 클라이언트를 함수가 배포된 리전으로 생성
    lambda_client = session.client('lambda', region_name=LAMBDA_DEPLOYMENT_REGION)

    # 페이로드에는 액션 대상 리전(region) 전달
    payload = {
        "operation": "execute",
        "region": region, # 분석/액션 대상 리전
        "volume_id": volume_id,
        "action_type": action_type,
        "volume_info": {
             "volume_id": volume_id
        }
    }

    try:
        response = await asyncio.to_thread(
            lambda_client.invoke,
            FunctionName=EBS_OPTIMIZER_LAMBDA_NAME,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )

        response_payload = json.loads(response['Payload'].read().decode('utf-8'))

        # Lambda 응답 상태 코드 확인
        lambda_status_code = response.get('StatusCode', 200)
        if lambda_status_code != 200:
            logger.error(f"Lambda function execution error (status: {lambda_status_code}): {response_payload}")
            error_body = response_payload.get('body', json.dumps(response_payload))
            try: # body가 JSON 문자열이면 파싱
                parsed_error = json.loads(error_body)
                return {"success": False, "error": f"Lambda execution failed (status {lambda_status_code})", "details": parsed_error}
            except json.JSONDecodeError:
                 return {"success": False, "error": f"Lambda execution failed (status {lambda_status_code})", "details": error_body}

        # Lambda 함수 내부 로직 결과 (body에 실제 결과 포함)
        if isinstance(response_payload, dict) and 'body' in response_payload:
             body_content = json.loads(response_payload['body']) # body 내용을 파싱
             logger.info(f"Lambda action execution successful for {volume_id}. Result: {body_content}")
             # Lambda 함수가 반환한 success/message/details 구조를 그대로 반환
             return body_content
        else:
             logger.error(f"Unexpected Lambda response format: {response_payload}")
             return {"success": False, "error": "Unexpected Lambda response format", "details": response_payload}

    except ClientError as e:
        logger.error(f"Failed to invoke Lambda function '{EBS_OPTIMIZER_LAMBDA_NAME}' in {LAMBDA_DEPLOYMENT_REGION}: {e}", exc_info=True)
        return {"success": False, "error": f"Failed to invoke Lambda: {e}"}
    except json.JSONDecodeError as e:
         logger.error(f"Failed to decode Lambda response: {e}", exc_info=True)
         return {"success": False, "error": f"Failed to decode Lambda response: {e}"}
    except Exception as e:
        logger.error(f"Error processing Lambda response: {e}", exc_info=True)
        return {"success": False, "error": f"Error processing Lambda response: {e}"}


if __name__ == "__main__":
    mcp_server = mcp._mcp_server  # noqa: WPS437

    import argparse
    
    parser = argparse.ArgumentParser(description='Run MCP SSE-based server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8080, help='Port to listen on')
    args = parser.parse_args()

    # Bind SSE request handling to MCP server
    starlette_app = create_starlette_app(mcp_server, debug=True)

    uvicorn.run(starlette_app, host=args.host, port=args.port)