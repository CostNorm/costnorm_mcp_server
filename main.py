from typing import Any
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
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize FastMCP server for Weather tools (SSE)
mcp = FastMCP("instance_manager")

# Constants
EXCLUDE_TAG_KEY = "CostNormExclude"
# --- Configuration for Lambda Invocation ---
ARM_ANALYSIS_LAMBDA_FUNCTION_NAME = os.environ.get("ARM_ANALYSIS_LAMBDA_FUNCTION_NAME")
if not ARM_ANALYSIS_LAMBDA_FUNCTION_NAME:
    print("Warning: ARM_ANALYSIS_LAMBDA_FUNCTION_NAME environment variable not set.")

# Initialize Boto3 Lambda client
try:
    lambda_client = boto3.client('lambda')
    print("Boto3 Lambda client initialized.")
except Exception as e:
    print(f"Error initializing Boto3 Lambda client: {e}")
    lambda_client = None

@mcp.tool()
async def analyze_repo_arm_compatibility(repo_url: str) -> dict:
    """
    Analyze the compatibility of a repository with ARM architecture by invoking a Lambda function.
    
    Args:
        repo_url: The URL of the GitHub repository to analyze.

    Returns:
        dict: A dictionary with compatibility analysis results.
    """
    if not lambda_client:
        return {"error": "Lambda client not initialized. Cannot perform analysis."}

    if not ARM_ANALYSIS_LAMBDA_FUNCTION_NAME:
        return {"error": "ARM analysis Lambda function name is not configured."}

    print(f"Invoking ARM analysis Lambda ({ARM_ANALYSIS_LAMBDA_FUNCTION_NAME}) for: {repo_url}")

    # Prepare payload for the Lambda function
    payload = json.dumps({
        "github_url": repo_url
    })

    try:
        # Invoke the Lambda function
        response = lambda_client.invoke(
            FunctionName=ARM_ANALYSIS_LAMBDA_FUNCTION_NAME,
            InvocationType='RequestResponse',  # Synchronous invocation
            Payload=payload
        )

        # Check for invocation errors
        status_code = response.get('StatusCode')
        function_error = response.get('FunctionError')

        if status_code not in [200, 202]:
             error_message = f"Lambda invocation failed with status code: {status_code}"
             try:
                 error_payload = json.loads(response['Payload'].read().decode('utf-8'))
                 error_message += f" - Payload: {error_payload}"
             except:
                 pass
             print(error_message)
             return {"error": error_message}

        if function_error:
            error_payload_str = response['Payload'].read().decode('utf-8')
            print(f"Lambda function executed with error ({function_error}): {error_payload_str}")
            try:
                error_detail = json.loads(error_payload_str)
                if isinstance(error_detail, dict) and 'error' in error_detail:
                    return error_detail
                else:
                     return {"error": f"Lambda function error ({function_error})", "details": error_payload_str}
            except json.JSONDecodeError:
                 return {"error": f"Lambda function error ({function_error})", "details": "Non-JSON error payload received"}

        # Successful invocation and function execution
        result_payload_str = response['Payload'].read().decode('utf-8')
        print(f"Lambda ({ARM_ANALYSIS_LAMBDA_FUNCTION_NAME}) returned successfully.")

        try:
            # Parse the result from the Lambda's response
            lambda_response = json.loads(result_payload_str)
            
            # Extract the analysis result from the Lambda response body if needed
            if "body" in lambda_response and isinstance(lambda_response, dict):
                try:
                    # The body might be a JSON string that needs to be parsed
                    analysis_result = json.loads(lambda_response["body"])
                    return analysis_result
                except (json.JSONDecodeError, TypeError):
                    # If body is not valid JSON or not a string, return the whole response
                    return lambda_response
            else:
                # Return the whole response if no body field exists
                return lambda_response
            
        except json.JSONDecodeError as e:
            print(f"Failed to decode JSON response from Lambda: {e}")
            return {"error": "Failed to parse analysis result from Lambda", "raw_response": result_payload_str}

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code')
        error_message = e.response.get('Error', {}).get('Message', str(e))
        print(f"AWS ClientError invoking Lambda: {error_code} - {error_message}")
        return {"error": f"AWS API Error: {error_code} - {error_message}"}
    except Exception as e:
        print(f"Unexpected error invoking Lambda: {e}")
        return {"error": f"An unexpected error occurred during Lambda invocation: {str(e)}"}

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