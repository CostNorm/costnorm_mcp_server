from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from mcp.server.sse import SseServerTransport
from starlette.requests import Request
from starlette.routing import Mount, Route
from mcp.server import Server
import uvicorn
import boto3
import json

# Initialize FastMCP server for Weather tools (SSE)
mcp = FastMCP("instance_manager")

# Constants
EXCLUDE_TAG_KEY = "CostNormExclude"


@mcp.tool()
async def analyze_unused_resource() -> dict:
    """Invokes a Lambda function to identify potentially unused and unattached resources.

    This tool calls a backend Lambda function that performs two main analyses:
    1. Detects unattached resources like Elastic IPs (EIPs) and potentially others across regions.
    2. Identifies resources that might be unused based on cost analysis.

    Returns:
        dict: A dictionary representing the Lambda function's HTTP-like response.
              It typically includes the following keys:
              - "statusCode" (int): The overall HTTP status code of the Lambda invocation.
              - "headers" (dict): Response headers, commonly including "Content-Type".
              - "body" (str): A JSON string. When parsed, this string reveals a
                nested dictionary with the following main keys:
                - "unattach_id" (dict): Contains information about unattached resources.
                  - "eips" (dict): Maps region names (e.g., "us-east-1") to a list of
                    unattached EIP allocation IDs found in that region.
                  - "enis" (dict): Maps region names (e.g., "us-east-1") to a list of
                    unattached ENI IDs found in that region.
                - "unused_id" (dict): Contains results from the cost-based unused resource analysis.
                  This itself often mirrors a Lambda response structure:
                  - "statusCode" (int): Status code from the cost analysis part.
                  - "body" (str): Another JSON string. When parsed, this provides:
                    - "message" (str): A summary message from the cost analysis (e.g., "No resource IDs to query.").
                    - "resource_ids_with_cost" (list): A list of resource IDs that incurred costs,
                      identified by the cost-based analysis.

              Example of a parsed "body" from a successful response:
              {
                "unattach_id": {
                  "eips": {
                    "us-east-1": ["eipalloc-0af31faca24be4bd1"]
                  },
                  "enis": {
                    "us-east-1": ["eni-0123456789abcdef0"]
                  }
                },
                "unused_id": {
                  "statusCode": 200,
                  "body": {
                    "message": "No resource IDs to query.",
                    "resource_ids_with_cost": []
                  }
                }
              }
    """
    results = boto3.client("lambda", region_name="us-east-1").invoke(
        FunctionName="unused_resource_tool",
        InvocationType="RequestResponse",
        Payload=json.dumps({"operation": "analyze"}),
    )
    results = json.loads(results["Payload"].read())
    return results


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
        Payload=json.dumps({"github_url": repo_url}),
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
        Payload=json.dumps({"body": {"tool_name": "get_instance_info"}}),
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
        Payload=json.dumps(
            {
                "body": {
                    "tool_name": "modify_instance_type",
                    "instance_id": instance_id,
                    "new_type": new_type,
                }
            }
        ),
    )
    results = json.loads(results["Payload"].read())
    return results


@mcp.tool()
async def analyze_vpc_endpoint_presence(
    instance_id: str, region: str, days: int = None, hours: int = 1
) -> dict:
    """Analyze VPC endpoint usage in a specific region over a given number of days.

    Args:

        region: The AWS region to analyze (e.g., 'us-east-1').
        days: The number of days to analyze (default is 1).
    """

    payload = json.dumps(
        {"instance_id": instance_id, "region": region, "days": days, "hours": hours}
    )

    results = boto3.client("lambda", region_name="ap-northeast-2").invoke(
        FunctionName="network_optimize_lambda",
        InvocationType="RequestResponse",
        Payload=payload,
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

    parser = argparse.ArgumentParser(description="Run MCP SSE-based server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    args = parser.parse_args()

    # Bind SSE request handling to MCP server
    starlette_app = create_starlette_app(mcp_server, debug=True)

    uvicorn.run(starlette_app, host=args.host, port=args.port)
