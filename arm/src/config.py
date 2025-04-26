import os
import logging
import sys

# Detect if running in Lambda environment
IS_LAMBDA = os.environ.get('AWS_LAMBDA_FUNCTION_NAME') is not None

# Load environment variables from .env file if not in Lambda
if not IS_LAMBDA:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        print("Loaded environment variables from .env file for local development")
    except ImportError:
        print("python-dotenv not installed, using environment variables directly")

# --- Logging Configuration ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
# Basic logging setup
log_level_int = logging.getLevelName(LOG_LEVEL)  # Get the integer value for the level
# Get the root logger
logger = logging.getLogger()
# Remove existing handlers if any (important for Lambda)
if logger.hasHandlers():
    logger.handlers.clear()
logger.setLevel(log_level_int)
# Add a basic StreamHandler
ch = logging.StreamHandler()
ch.setLevel(log_level_int)  # Set level for the handler
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch.setFormatter(formatter)
logger.addHandler(ch)
logger.info(f"Logging level set to: {LOG_LEVEL}")
logger.info(f"Running in Lambda environment: {IS_LAMBDA}")


# --- GitHub Configuration ---
# Required for accessing GitHub repositories. Generate a token with 'repo' scope.
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
if not GITHUB_TOKEN:
    logger.warning(
        "GITHUB_TOKEN environment variable not set. GitHub API rate limits may be encountered."
    )
else:
    logger.info("GitHub token found.")


# --- Analyzer Configuration ---
# Helper function to convert string environment variables to boolean
def get_bool_env_var(var_name: str, default: str = "false") -> bool:
    return os.environ.get(var_name, default).lower() == "true"

# Control which analysis modules are active. Set environment variables to "true" or "false".
ENABLED_ANALYZERS = {
    "terraform": get_bool_env_var("ENABLE_TERRAFORM_ANALYZER", "False"),
    "docker": get_bool_env_var("ENABLE_DOCKER_ANALYZER", "False"),
    "dependency": get_bool_env_var("ENABLE_DEPENDENCY_ANALYZER", "True"),
}
logger.info(f"Enabled analyzers: {ENABLED_ANALYZERS}")


# --- Docker Hub Configuration (for Manifest Inspection) ---
# Required for accurate Docker base image analysis via API.
# Consider using AWS Secrets Manager in production instead of env vars.
DOCKERHUB_USERNAME = os.environ.get("DOCKERHUB_USERNAME", "")
DOCKERHUB_PASSWORD = os.environ.get(
    "DOCKERHUB_PASSWORD", ""
)  # Can be password or access token
# Alternatively, provide a pre-fetched token:
# DOCKERHUB_TOKEN = os.environ.get("DOCKERHUB_TOKEN", "") # If using a token directly

# Basic check for credentials needed for Docker Hub
if not DOCKERHUB_USERNAME or not DOCKERHUB_PASSWORD:
    logger.warning(
        "DOCKERHUB_USERNAME or DOCKERHUB_PASSWORD not set. Docker manifest inspection will likely fail."
    )
else:
    logger.info("Docker Hub credentials found.")
