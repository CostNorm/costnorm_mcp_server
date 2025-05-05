import boto3
import json
from botocore.exceptions import ClientError

def invoke_unused_lambda():
    lambda_client = boto3.client('lambda', 'us-east-1')
    function_name = 'unused_resource' # Replace with your actual Lambda function name if different
    
    try:
        print(f"Invoking Lambda function: {function_name} in us-east-1")
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse', # Synchronous invocation
            Payload=json.dumps({}) # Sending an empty JSON object as payload, similar to calling lambda_handler(None, None)
        )
        
        # The response payload is a streaming body, read it and parse JSON
        response_payload = json.loads(response['Payload'].read().decode('utf-8'))
        print(f"Lambda function response received.")

        # Check if the Lambda function itself returned an error
        if response.get('FunctionError'):
             print(f"Lambda function executed with error: {response['FunctionError']}")
             # You might want to raise an exception or return a specific error structure
             # For now, returning the payload which might contain error details
             return response_payload
        
        return response_payload

    except ClientError as e:
        error_message = f"Error invoking Lambda function {function_name}: {e}"
        print(error_message)
        # Return an error structure consistent with Lambda's response format
        return {
            'statusCode': 500,
            'body': json.dumps({'message': error_message})
        }
    except Exception as e:
        error_message = f"An unexpected error occurred: {e}"
        print(error_message)
        return {
            'statusCode': 500,
            'body': json.dumps({'message': error_message})
        }