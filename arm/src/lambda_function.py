import json
import logging
from typing import Dict, Any

# Import necessary configurations and core components
from config import (
    logger,  # Use the configured logger
    GITHUB_TOKEN,
    DOCKERHUB_USERNAME, # Needed by DockerAnalyzer via config
    DOCKERHUB_PASSWORD, # Needed by DockerAnalyzer via config
    ENABLED_ANALYZERS, # Needed by Orchestrator via config
)
from services.github_service import GithubService
from analysis_orchestrator import AnalysisOrchestrator


# --- Instantiate Core Services ---
# These can be initialized once per Lambda container execution environment
# Configuration is implicitly passed via the imported config module
try:
    github_service = GithubService(github_token=GITHUB_TOKEN)
    analysis_orchestrator = AnalysisOrchestrator(github_service=github_service)
    logger.info("AnalysisOrchestrator initialized successfully.")
except Exception as init_error:
    logger.exception("Failed to initialize core services during cold start.")
    # Set services to None to handle gracefully in the handler
    github_service = None
    analysis_orchestrator = None


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    AWS Lambda handler function triggered directly (e.g., via SDK/API Gateway).
    Expects 'github_url' in the event payload.
    Performs ARM compatibility analysis and returns the structured result.
    """
    logger.info(f"Received event: {event}")

    if analysis_orchestrator is None:
         logger.error("Analysis orchestrator not initialized. Cannot process request.")
         return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal Server Error: Orchestrator not available"}),
        }

    # 1. Extract GitHub URL from the event payload
    #    Assuming the invoking service sends {'github_url': '...'}
    github_url = event.get("github_url")

    if not github_url:
        logger.error("Missing 'github_url' in request payload.")
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Missing 'github_url' in request payload"}),
        }

    # 2. Perform the analysis
    try:
        logger.info(f"Starting analysis for repository: {github_url}")
        analysis_result = analysis_orchestrator.analyze_repository(github_url)
        logger.info(f"Analysis finished for: {github_url}")

        # Check if the orchestrator returned an error structure
        if "error" in analysis_result:
            logger.error(f"Analysis failed for {github_url}: {analysis_result['error']}")
            # Return a client error (400) or server error (500) depending on the error type
            # For simplicity, let's use 400 for known analysis failures
            status_code = 400 if analysis_result.get("error") != "An unexpected error occurred" else 500
            return {
                "statusCode": status_code,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(analysis_result), # Return the error detail from orchestrator
            }
        else:
            # Success
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(analysis_result), # Return the successful analysis result
            }

    except Exception as e:
        # Catch any unexpected errors during the handler execution
        logger.exception(f"Unexpected error processing request for {github_url}: {e}")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": f"An unexpected internal error occurred: {str(e)}"}),
        }
