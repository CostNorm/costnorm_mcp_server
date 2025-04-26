import boto3
from botocore.exceptions import ClientError
import json

# Load environment variables
# ARM_ANALYSIS_LAMBDA_FUNCTION_NAME = os.environ.get("ARM_ANALYSIS_LAMBDA_FUNCTION_NAME")

# --- Configuration for Lambda Invocation ---
ARM_ANALYSIS_LAMBDA_FUNCTION_NAME="arm-compatibility-analyzer"
if not ARM_ANALYSIS_LAMBDA_FUNCTION_NAME:
    print("Warning: ARM_ANALYSIS_LAMBDA_FUNCTION_NAME environment variable not set.")

# Initialize Boto3 Lambda client
try:
    boto3_session = boto3.Session(profile_name='costnorm', region_name='ap-northeast-2')
    lambda_client = boto3_session.client('lambda')
    print("Boto3 Lambda client initialized.")
except Exception as e:
    print(f"Error initializing Boto3 Lambda client: {e}")
    lambda_client = None



async def _invoke_arm_analysis_lambda(repo_url: str) -> dict:
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
