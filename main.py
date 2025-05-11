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
from code.guess_unused_resource.invoke_unused_lambda import invoke_unused_lambda
from code.detect_unattach_resource.invoke_unattach_lambda import invoke_unattach_lambda

# Initialize FastMCP server for Weather tools (SSE)
mcp = FastMCP("instance_manager")

# Constants
EXCLUDE_TAG_KEY = "CostNormExclude"


@mcp.tool()
async def guess_unused_resource_from_cost() -> dict:
    """Identified potentially unused resources ARNs and formatted them into a dictionary structure.
    
    Returns:
        dict: A dictionary with keys 'statusCode' and 'body'.
        'statusCode': The HTTP status code of the response.
        'body': 'message':A summary of the analysis 'resource_ids_with_cost_yesterday': A list of resource IDs with cost incurred yesterday.
    """
    return invoke_unused_lambda()


@mcp.tool()
async def get_unattached_resources() -> dict:
    """Get unattached EIPs and ENIs from all configured AWS regions.

    Invokes a Lambda function that scans specified AWS regions for Elastic IP addresses (EIPs)
    and Elastic Network Interfaces (ENIs) that are not currently associated with any running
    resource.

    Returns:
        dict: A dictionary containing the results of the scan.
              This nested dictionary maps region names (e.g., "us-east-1") to an object
              containing two lists: "unused_eips" and "unused_enis".
        
              {
                "us-east-1": {
                "unused_eips": [],
                "unused_enis": []
                },
                "ap-northeast-2": {
                "unused_eips": [],
                "unused_enis": []
                }
              }
    """
    return invoke_unattach_lambda()

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