module "mcp_tool_iac_template" {
  source = "github.com/Fletacode/mcp_tool_iac_template_fork"
  function_name = "unused_resource2"
  lambda_handler = "lambda_function.lambda_handler"
  lambda_runtime = "python3.13"
  lambda_architecture = "x86_64"
  lambda_timeout = 100
  lambda_memory = 1024
  attach_s3_policy = true
  attach_athena_policy = true
  region = "us-east-1"
  profile = "costnorm"
}   