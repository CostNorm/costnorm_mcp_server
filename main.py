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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from loguru import logger
from arm.arm_processor import McpArmProcessor, VMArmProcessor
from arm.models import ServerClientMessage
from storage.models import VMSpec
from storage.ebs.actions.executor import RecommendationExecutor

# Initialize FastMCP server for Weather tools (SSE)
mcp = FastMCP("instance_manager")
vm_arm = VMArmProcessor() # VM ARM

# Constants
EXCLUDE_TAG_KEY = "CostNormExclude"

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
def get_virtual_machine_spec(instance_id: str) -> Dict[str, str]:
    """Fetch the specifications of a virtual machine given its instance ID."""
    # TODO: Implement actual logic to fetch VM specs from cloud provider API
    # Example dummy implementation
    logger.info(f"Fetching VM spec for instance ID: {instance_id}")
    # Simulate API call
    if instance_id == "i-1234567890abcdef0":
        spec = VMSpec(instance_id=instance_id, instance_type="t2.micro", region="us-east-1", cpu_cores=1, memory_gb=1)
    elif instance_id == "i-0987654321fedcba0":
        spec = VMSpec(instance_id=instance_id, instance_type="m5.large", region="eu-west-1", cpu_cores=2, memory_gb=8)
    else:
        # Simulate not found or error
        return {"error": f"Instance ID {instance_id} not found."}
    return spec.dict()

@mcp.tool()
def list_available_regions() -> list[str]:
    """List all available AWS regions."""
    # TODO: Replace with actual logic, e.g., using boto3 to get regions
    logger.info("Listing available AWS regions")
    # Dummy data
    return ["us-east-1", "us-west-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-1", "ap-northeast-1"]

@mcp.tool()
def get_current_user_info() -> Dict[str, str]:
    """Get information about the current user session."""
    logger.info("Fetching current user info")
    # Dummy data
    return {"user_id": "test_user_123", "username": "Test User", "role": "admin", "session_start_time": "2024-01-15T10:00:00Z"}

@mcp.tool()
async def analyze_ebs_volumes_tool(
    region: Optional[str] = None,
    volume_id: Optional[str] = None
) -> Dict[str, Any]:
    """Analyzes AWS EBS volumes for potential cost optimization opportunities (idleness, overprovisioning).

    Args:
        region (Optional[str]): The specific AWS region to analyze. If None, analyzes all accessible regions defined in the configuration.
        volume_id (Optional[str]): The specific EBS volume ID to analyze. If provided, 'region' should also be specified or the system will try to find it (less reliable). If None, analyzes all volumes in the specified or default regions.

    Returns:
        Dict[str, Any]: A JSON object summarizing the analysis findings. Structure depends on single volume vs all regions.
                       For a single volume, keys like 'volume_id', 'region', 'is_idle', 'is_overprovisioned', 'recommendation', 'details' are expected.
                       For all regions, keys like 'summary', 'idle_volumes', 'overprovisioned_volumes', 'errors' are expected.
                       Returns {'error': 'message'} if analysis fails.
    """
    logger.info(f"Placeholder: analyze_ebs_volumes_tool called with region={region}, volume_id={volume_id}")
    return {"status": "pending implementation"}

@mcp.tool()
async def execute_ebs_action_tool(
    volume_id: str,
    action_type: str,
    region: Optional[str] = None
) -> Dict[str, Any]:
    """Executes a specific optimization action DIRECTLY on an EBS volume.

    Args:
        volume_id (str): The ID of the EBS volume to take action on.
        action_type (str): The type of action requested. Initially supported: 'snapshot_only'. Other actions ('snapshot_and_delete', 'change_type', 'resize') might be enabled later but require extreme caution.
        region (Optional[str]): The AWS region where the volume exists. Required if not easily derivable.

    Returns:
        Dict[str, Any]: A dictionary indicating the outcome. Expected keys:
                       'success': True or False.
                       'message': Description of the result or error.
                       'details': Optional dictionary with specifics like snapshot ID.

    ** WARNING: This tool executes actions DIRECTLY on AWS resources. **
    ** Use with extreme caution, especially for destructive actions. **
    ** Double-check the volume_id and action_type before proceeding. **
    """
    logger.info(f"Attempting to execute EBS action: volume_id={volume_id}, action={action_type}, region={region}")

    # Input Validation
    if not volume_id:
        logger.error("Volume ID is required for EBS action execution.")
        return {"success": False, "message": "Error: volume_id parameter is missing."}
    if not action_type:
        logger.error("Action type is required for EBS action execution.")
        return {"success": False, "message": "Error: action_type parameter is missing."}
    
    # --- Safety Restriction --- 
    # TODO: Carefully review and enable other actions after thorough testing and safety checks.
    # Allowed actions: 'snapshot_and_delete', 'change_type', 'resize'
    allowed_action = 'snapshot_only'
    if action_type != allowed_action:
        logger.warning(f"Direct execution of action_type '{action_type}' is disabled. Only '{allowed_action}' is allowed.")
        return {
            "success": False, 
            "message": f"Error: Direct execution of action_type '{action_type}' is currently disabled for safety. Only '{allowed_action}' is allowed via this tool initially."
        }

    # Region is crucial for initializing the executor and potentially describing the volume
    if not region:
        # Attempt to get region from config if possible, or return error
        # For now, require region explicitly for actions
        # TODO: Implement logic to derive region if not provided (e.g., describe volume across regions - costly)
        logger.error("Region parameter is required to execute EBS actions.")
        return {"success": False, "message": "Error: region parameter is missing. It is required for executing actions."}

    try:
        # Initialize the executor for the specified region
        # Assuming RecommendationExecutor needs region for boto3 client setup
        logger.info(f"Initializing RecommendationExecutor for region: {region}")
        executor = RecommendationExecutor(region=region)

        # Prepare minimal volume_info. Executor might need more details internally.
        # The executor's method should handle fetching more details if needed.
        volume_info = {
            'volume_id': volume_id,
            # Pass region and potentially other context if needed by executor method
            'region': region 
        }

        logger.info(f"Executing action '{action_type}' for volume {volume_id} using executor.")
        
        # Call the appropriate executor method based on action_type
        # Currently, only 'snapshot_only' is allowed, which corresponds to execute_idle_volume_recommendation
        # Note: execute_idle_volume_recommendation is synchronous, run in executor
        action_result = await asyncio.to_thread(
            executor.execute_idle_volume_recommendation, 
            volume_info, 
            action_type
        )

        logger.info(f"Action execution result for {volume_id}: {action_result}")

        # Check the result from the executor
        if isinstance(action_result, dict) and action_result.get('success'):
            return {
                "success": True,
                "message": f"Action '{action_type}' initiated successfully for volume {volume_id}.",
                "details": action_result.get('details', {})
            }
        else:
            error_message = "Unknown error during action execution."
            if isinstance(action_result, dict):
                error_message = action_result.get('details', {}).get('error', error_message)
                # Include status if available
                status = action_result.get('status')
                if status:
                     error_message = f"{status}: {error_message}"

            logger.error(f"EBS action '{action_type}' failed for volume {volume_id}: {error_message}")
            return {"success": False, "message": error_message, "details": action_result}

    except Exception as e:
        logger.error(f"Error executing EBS action '{action_type}' for {volume_id}: {str(e)}", exc_info=True)
        return {"success": False, "message": f"Error executing EBS action: {str(e)}"}
    finally:
        logger.info(f"Finished EBS action execution attempt for volume_id={volume_id}, action={action_type}")

# --- FastAPI App Setup ---
app = FastAPI()

# Serve static files (like index.html, CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """Handles WebSocket connections for both MCP and VM ARM clients."""
    await websocket.accept()
    logger.info(f"Client {client_id} connected.")

    # Determine if the client is for MCP or VM ARM based on path or initial message (example)
    # For simplicity, let's assume client_id tells us ('mcp_' prefix for MCP, 'vm_' for VM ARM)
    # A more robust method would involve an initial handshake message.
    if client_id.startswith("mcp_"):
        processor = mcp
        logger.info(f"Client {client_id} assigned to MCP Processor.")
    elif client_id.startswith("vm_"):
        processor = vm_arm
        logger.info(f"Client {client_id} assigned to VM ARM Processor.")
    else:
        logger.warning(f"Client {client_id} has unknown type. Defaulting to MCP.")
        processor = mcp # Default or raise error

    # Send available tools upon connection
    await processor.send_available_tools(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            logger.debug(f"Received message from {client_id}: {data[:100]}...") # Log truncated message
            try:
                message = ServerClientMessage.parse_raw(data)
                await processor.handle_message(websocket, message)
            except Exception as e:
                logger.error(f"Error handling message from {client_id}: {e}", exc_info=True)
                # Optionally send an error message back to the client
                await websocket.send_text(ServerClientMessage(message_type="error", payload={"error": str(e)}).json())

    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected.")
    except Exception as e:
        logger.error(f"Unexpected error with client {client_id}: {e}", exc_info=True)

@app.get("/")
async def get_root():
    """Serves the main HTML page."""
    # Example: Redirect to the static index.html or return HTML directly
    from fastapi.responses import FileResponse
    return FileResponse('static/index.html')

# --- Application Entry Point ---
if __name__ == "__main__":
    logger.add("server.log", rotation="10 MB") # Add file logging
    logger.info("Starting server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)