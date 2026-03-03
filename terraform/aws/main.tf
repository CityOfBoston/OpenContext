terraform {
  required_version = ">= 1.0"

  backend "s3" {
    bucket  = "opencontext-terraform-state"
    key     = "opencontext/terraform.tfstate"
    region  = "us-east-1"
    encrypt = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Read config.yaml
locals {
  config = yamldecode(file(var.config_file))

  lambda_name = var.lambda_name != "" ? var.lambda_name : (
    local.config.server_name != "" ? lower(replace(local.config.server_name, " ", "-")) : "opencontext-mcp-server"
  )

  lambda_memory  = local.config.aws.lambda_memory != null ? local.config.aws.lambda_memory : var.lambda_memory
  lambda_timeout = local.config.aws.lambda_timeout != null ? local.config.aws.lambda_timeout : var.lambda_timeout

  # Serialize config to JSON for environment variable
  config_json = jsonencode(local.config)
}

# IAM role for Lambda
resource "aws_iam_role" "lambda_role" {
  name = "${local.lambda_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# Basic Lambda execution policy
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Lambda deployment package (created by scripts/deploy.sh)
# deploy.sh builds .deploy/ and lambda-deployment.zip, then copies the zip here.
locals {
  lambda_zip_path = "${path.module}/lambda-deployment.zip"
  lambda_zip_hash = filebase64sha256(local.lambda_zip_path)
}

# Lambda function
resource "aws_lambda_function" "mcp_server" {
  filename         = local.lambda_zip_path
  function_name    = local.lambda_name
  role             = aws_iam_role.lambda_role.arn
  handler          = "server.adapters.aws_lambda.lambda_handler"
  source_code_hash = local.lambda_zip_hash
  runtime          = "python3.11"
  memory_size      = local.lambda_memory
  timeout          = local.lambda_timeout

  environment {
    variables = {
      OPENCONTEXT_CONFIG = local.config_json
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic,
  ]
}

# Lambda Function URL
resource "aws_lambda_function_url" "mcp_server_url" {
  function_name      = aws_lambda_function.mcp_server.function_name
  authorization_type = "NONE"

  cors {
    allow_origins  = ["*"]
    allow_methods  = ["POST"]
    allow_headers  = ["content-type"]
    expose_headers = ["x-request-id", "mcp-session-id"]
    max_age        = 86400
  }
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${local.lambda_name}"
  retention_in_days = 14
}
