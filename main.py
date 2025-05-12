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
from typing import Any, Optional, Dict, List

# Initialize FastMCP server for Weather tools (SSE)
mcp = FastMCP("instance_manager")

# Constants
EXCLUDE_TAG_KEY = "CostNormExclude"

@mcp.tool()
async def analyze_repo_arm_compatibility(repo_url: str) -> dict:
    """
    Analyze the compatibility of a repository with ARM architecture by invoking a Lambda function.

    Args:
        repo_url: The URL of the GitHub repository to analyze.

    Returns:
        dict: A dictionary with compatibility analysis results.
    """
    results = boto3.client("lambda", region_name="ap-northeast-2").invoke(
        FunctionName="arm-compatibility-analyzer",
        InvocationType="RequestResponse",
        Payload=json.dumps({"github_url": repo_url})
    )
    results = json.loads(results["Payload"].read())
    return results

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
    results = boto3.client("lambda", region_name="us-east-1").invoke(
        FunctionName="instance_optimize_tool",
        InvocationType="RequestResponse",
        Payload=json.dumps({"body": {"tool_name": "get_instance_info"}})
    )
    results = json.loads(results["Payload"].read())

    # Return the structured results
    return results


@mcp.tool()
async def modify_instance_type(instance_id: str, new_type: str) -> str:
    """Modify the type of a specific EC2 instance.

    Args:
        instance_id: The ID of the instance to modify.
        new_type: The target instance type (e.g., t2.medium).
    """
    results = boto3.client("lambda", region_name="us-east-1").invoke(
        FunctionName="instance_optimize_tool",
        InvocationType="RequestResponse",
        Payload=json.dumps({"body": {"tool_name": "modify_instance_type", "instance_id": instance_id, "new_type": new_type}})
    )
    results = json.loads(results["Payload"].read())
    return results


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

# --- Re-added Tools to Invoke EBS Optimizer Lambda --- 

@mcp.tool()
async def analyze_ebs_volumes_tool(
    region: str, # 분석 대상 리전
    volume_id: Optional[str] = None,
    volume_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Analyzes AWS Elastic Block Store (EBS) volumes in a specified region for potential cost optimization opportunities, specifically checking for idleness and overprovisioning. This tool invokes a separate AWS Lambda function to perform the actual analysis.

    **When to use this tool:**
    - User explicitly asks to "analyze EBS volumes", "check for unused EBS volumes", "find idle EBS storage", "scan EBS for optimization", or similar requests, specifying a region.
    - User asks to analyze a *specific* EBS volume ID (e.g., "analyze volume vol-123abc").

    **When NOT to use this tool:**
    - User asks to *execute* an action (like delete, snapshot, resize) - use 'execute_ebs_action_tool' for that.
    - User asks about other AWS services like EFS, S3, or EC2 instances (use relevant tools if available).
    - User asks for general information about EBS pricing or features without requesting analysis of specific resources.

    Args:
        region (str): The AWS region (e.g., 'us-east-1', 'ap-northeast-2') where the EBS volumes reside. This parameter is REQUIRED.
        volume_id (Optional[str]): The specific ID of a single EBS volume to analyze (e.g., 'vol-0123456789abcdef0'). If provided, only this volume will be analyzed within the specified region. If omitted, *all* EBS volumes in the specified region will be analyzed. The format must start with 'vol-'.

    Returns:
        Dict[str, Any]: A dictionary containing the analysis results.
            - If the analysis was successful (even if no optimizable volumes were found):
                - 'success': True
                # Structure for analyzing ALL volumes in a region:
                - 'summary' (dict): Contains counts like 'total_volumes_analyzed', 'idle_volumes_count', 'overprovisioned_volumes_count'.
                - 'idle_volumes' (List[dict]): A list of details for volumes identified as idle. Each item includes 'volume_id', 'size', 'reason', 'recommendation', etc. **An empty list means no idle volumes were found.**
                - 'overprovisioned_volumes' (List[dict]): A list of details for overprovisioned volumes. Each item includes 'volume_id', 'size', 'reason', 'recommendation', 'recommended_size', etc. **An empty list means no overprovisioned volumes were found.**
                - 'errors' (List[dict]): A list of non-critical errors encountered during the analysis of specific volumes within the region.
            - If the analysis was successful for a SINGLE volume:
                - 'success': True
                - Contains keys like 'volume_id', 'region', 'size', 'volume_type', 'is_idle', 'is_overprovisioned', 'status' ('Idle', 'Overprovisioned', 'Optimized/In-use'), 'recommendation', 'details' (metrics, diagnostics).
            - If the Lambda invocation or analysis itself failed critically:
                - 'success': False
                - 'error': A string describing the error (e.g., "Lambda invocation failed", "Invalid volume ID format", "Region not found").
                - 'details' (Optional[Any]): Further details about the error if available.

    **Important Notes for LLM:**
    - An empty list for 'idle_volumes' or 'overprovisioned_volumes' means *none were found*, it does not indicate an error.
    - Check the 'success' key first. If 'success' is False, report the 'error' message to the user.
    - The analysis might take some time, especially when scanning all volumes in a region. Inform the user that the process is running.
    """
    lambda_client = boto3.client('lambda', region_name="ap-northeast-2")

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
            FunctionName="ebs-optimizer-lambda",
            InvocationType='RequestResponse', # Synchronous invocation
            Payload=json.dumps(payload)
        )

        response_payload_raw = response['Payload'].read().decode('utf-8')
        response_payload = json.loads(response_payload_raw)

        lambda_status_code = response.get('StatusCode', 200)
        if lambda_status_code != 200:
            error_body = response_payload.get('body', json.dumps(response_payload))
            try:
                parsed_error = json.loads(error_body)
                return {"success": False, "error": f"Lambda execution failed (status {lambda_status_code})", "details": parsed_error}
            except json.JSONDecodeError:
                 return {"success": False, "error": f"Lambda execution failed (status {lambda_status_code})", "details": error_body}

        # Lambda 함수의 응답 본문(body)을 직접 반환 (이미 JSON 객체로 가정)
        if isinstance(response_payload, dict) and 'body' in response_payload:
            try:
                body_content = json.loads(response_payload['body']) # body가 문자열일 경우 JSON 파싱
                return body_content
            except (json.JSONDecodeError, TypeError) as e:
                 # body가 이미 객체일 수 있으므로 그대로 반환 시도
                 if isinstance(response_payload['body'], dict):
                     return response_payload['body']
                 return {"success": False, "error": "Failed to parse Lambda response body", "raw_body": response_payload['body']}
        elif response.get('FunctionError'): # Check for unhandled errors in Lambda
             return {"success": False, "error": f"Lambda function error: {response['FunctionError']}", "details": response_payload_raw}
        else:
             return {"success": False, "error": "Unexpected Lambda response format", "details": response_payload}

    except ClientError as e:
        return {"success": False, "error": f"Failed to invoke Lambda: {e}"}
    except json.JSONDecodeError as e:
         return {"success": False, "error": f"Failed to decode Lambda response: {e}", "raw_response": response_payload_raw}
    except Exception as e:
        return {"success": False, "error": f"Error processing Lambda response: {e}"}

@mcp.tool()
async def execute_ebs_action_tool(
    volume_id: str,
    action_type: str,
    region: str, # 액션 대상 리전
) -> Dict[str, Any]:
    """Executes a specific action on an AWS Elastic Block Store (EBS) volume by invoking the EBS Optimizer Lambda function.

    **When to use this tool:**
    - User explicitly requests to perform an action on a specific EBS volume (e.g., "delete volume vol-123abc", "resize volume vol-123abc").
    - User wants to execute a recommended action from the analysis results of `analyze_ebs_volumes_tool`.

    **When NOT to use this tool:**
    - User asks to analyze or check EBS volumes (use `analyze_ebs_volumes_tool` instead).
    - User asks about other AWS services like EFS, S3, or EC2 instances.
    - User asks for general information about EBS pricing or features.

    **Supported action types:**
    - "snapshot_only": Creates a snapshot of the EBS volume without any other actions.
    - "snapshot_and_delete": Creates a snapshot of the EBS volume and then deletes the volume.
    - "change_type": Changes the EBS volume type (e.g., from gp2 to gp3).
    - "resize": Resizes the EBS volume to a more appropriate size based on usage patterns.
    - "change_type_and_resize": Changes both the volume type and size in a single operation.

    Args:
        volume_id (str): The ID of the EBS volume to act upon (e.g., 'vol-0123456789abcdef0'). Must start with 'vol-'.
        action_type (str): The type of action to perform. Must be one of: "snapshot_only", "snapshot_and_delete", "change_type", "resize", "change_type_and_resize".
        region (str): The AWS region (e.g., 'us-east-1', 'ap-northeast-2') where the EBS volume resides.

    Returns:
        Dict[str, Any]: A dictionary containing the action execution results.
            - If the action was successful:
                - 'success': True
                - 'message': A descriptive message about the action performed
                - 'details': Additional details about the action (if any)
            - If the action failed:
                - 'success': False
                - 'error': A string describing the error
                - 'details': Further details about the error (if available)

    **Important Notes for LLM:**
    - Always verify that the requested action_type is one of the supported types listed above.
    - For "snapshot_and_delete" actions, ensure the volume is truly idle and not needed before proceeding.
    - The volume must exist in the specified region.
    - Some actions may require additional permissions in the Lambda function's IAM role.
    - Actions like "snapshot_and_delete" are irreversible - use with caution.
    - Root volumes are protected from certain actions (e.g., deletion, size reduction).
    """
    lambda_client = boto3.client('lambda', region_name="ap-northeast-2")

    payload = {
        "operation": "execute",
        "region": region, # 액션 대상 리전
        "volume_id": volume_id,
        "action_type": action_type,
    }

    try:
        response = await asyncio.to_thread(
            lambda_client.invoke,
            FunctionName="ebs-optimizer-lambda",
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )
        
        response_payload_raw = response['Payload'].read().decode('utf-8')
        response_payload = json.loads(response_payload_raw)

        lambda_status_code = response.get('StatusCode', 200)
        if lambda_status_code != 200:
            error_body = response_payload.get('body', json.dumps(response_payload))
            try:
                parsed_error = json.loads(error_body)
                return {"success": False, "error": f"Lambda execution failed (status {lambda_status_code})", "details": parsed_error}
            except json.JSONDecodeError:
                 return {"success": False, "error": f"Lambda execution failed (status {lambda_status_code})", "details": error_body}

        if isinstance(response_payload, dict) and 'body' in response_payload:
             try:
                 body_content = json.loads(response_payload['body'])
                 return body_content
             except (json.JSONDecodeError, TypeError) as e:
                 if isinstance(response_payload['body'], dict):
                     return response_payload['body']
                 return {"success": False, "error": "Failed to parse Lambda response body", "raw_body": response_payload['body']}
        elif response.get('FunctionError'):
             return {"success": False, "error": f"Lambda function error: {response['FunctionError']}", "details": response_payload_raw}
        else:
             return {"success": False, "error": "Unexpected Lambda response format", "details": response_payload}

    except ClientError as e:
        return {"success": False, "error": f"Failed to invoke Lambda: {e}"}
    except json.JSONDecodeError as e:
         return {"success": False, "error": f"Failed to decode Lambda response: {e}", "raw_response": response_payload_raw}
    except Exception as e:
        return {"success": False, "error": f"Error processing Lambda response: {e}"}

# --- End Re-added Tools ---

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