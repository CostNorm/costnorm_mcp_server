import boto3
import json

LAMBDA_FUNCTION_NAME = "network_optimize_lambda"


async def _invoke_network_tool(instance_id, region, days=None, hours=1):
    print(f"instance_id: {instance_id}, region: {region}, days: {days}, hours: {hours}")
    boto3_session = boto3.Session(profile_name="costnorm", region_name="ap-northeast-2")

    lambda_client = boto3_session.client("lambda")

    payload = json.dumps(
        {"instance_id": instance_id, "region": region, "days": days, "hours": hours}
    )

    response = lambda_client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType="RequestResponse",  # Synchronous invocation
        Payload=payload,
    )

    return response
