# API Gateway REST API
resource "aws_api_gateway_rest_api" "mcp_api" {
  name        = "${local.lambda_name}-api"
  description = "API Gateway for OpenContext MCP Server"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# API Gateway Resource: /mcp
resource "aws_api_gateway_resource" "mcp" {
  rest_api_id = aws_api_gateway_rest_api.mcp_api.id
  parent_id   = aws_api_gateway_rest_api.mcp_api.root_resource_id
  path_part   = "mcp"
}

# API Gateway Method: POST
resource "aws_api_gateway_method" "mcp_post" {
  rest_api_id      = aws_api_gateway_rest_api.mcp_api.id
  resource_id      = aws_api_gateway_resource.mcp.id
  http_method      = "POST"
  authorization    = "NONE"
  api_key_required = false
}

# API Gateway Method: OPTIONS (for CORS, no API key required)
resource "aws_api_gateway_method" "mcp_options" {
  rest_api_id      = aws_api_gateway_rest_api.mcp_api.id
  resource_id      = aws_api_gateway_resource.mcp.id
  http_method      = "OPTIONS"
  authorization    = "NONE"
  api_key_required = false
}

# Lambda Integration for POST
resource "aws_api_gateway_integration" "mcp_post_integration" {
  rest_api_id = aws_api_gateway_rest_api.mcp_api.id
  resource_id = aws_api_gateway_resource.mcp.id
  http_method = aws_api_gateway_method.mcp_post.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.mcp_server.invoke_arn
}

# Lambda Integration for OPTIONS (mock response for CORS)
resource "aws_api_gateway_integration" "mcp_options_integration" {
  rest_api_id = aws_api_gateway_rest_api.mcp_api.id
  resource_id = aws_api_gateway_resource.mcp.id
  http_method = aws_api_gateway_method.mcp_options.http_method

  type = "MOCK"

  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

# Method Response for POST
resource "aws_api_gateway_method_response" "mcp_post_response_200" {
  rest_api_id = aws_api_gateway_rest_api.mcp_api.id
  resource_id = aws_api_gateway_resource.mcp.id
  http_method = aws_api_gateway_method.mcp_post.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = true
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
  }
}

# Method Response for OPTIONS
resource "aws_api_gateway_method_response" "mcp_options_response_200" {
  rest_api_id = aws_api_gateway_rest_api.mcp_api.id
  resource_id = aws_api_gateway_resource.mcp.id
  http_method = aws_api_gateway_method.mcp_options.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = true
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
  }
}

# Integration Response for OPTIONS
resource "aws_api_gateway_integration_response" "mcp_options_integration_response" {
  rest_api_id = aws_api_gateway_rest_api.mcp_api.id
  resource_id = aws_api_gateway_resource.mcp.id
  http_method = aws_api_gateway_method.mcp_options.http_method
  status_code = aws_api_gateway_method_response.mcp_options_response_200.status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type'"
    "method.response.header.Access-Control-Allow-Methods" = "'OPTIONS,POST'"
  }

  response_templates = {
    "application/json" = ""
  }

  depends_on = [aws_api_gateway_integration.mcp_options_integration]
}

# Lambda Permission for API Gateway
resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mcp_server.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.mcp_api.execution_arn}/*/*"
}

# API Gateway Deployment
resource "aws_api_gateway_deployment" "mcp_deployment" {
  rest_api_id = aws_api_gateway_rest_api.mcp_api.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.mcp.id,
      aws_api_gateway_method.mcp_post.id,
      aws_api_gateway_method.mcp_options.id,
      aws_api_gateway_integration.mcp_post_integration.id,
      aws_api_gateway_integration.mcp_options_integration.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_method.mcp_post,
    aws_api_gateway_method.mcp_options,
    aws_api_gateway_integration.mcp_post_integration,
    aws_api_gateway_integration.mcp_options_integration,
    aws_api_gateway_method_response.mcp_post_response_200,
    aws_api_gateway_method_response.mcp_options_response_200,
    # aws_api_gateway_integration_response.mcp_post_integration_response,  # Removed - AWS_PROXY ignores it
    aws_api_gateway_integration_response.mcp_options_integration_response,
  ]
}

# API Gateway Stage
resource "aws_api_gateway_stage" "prod" {
  deployment_id = aws_api_gateway_deployment.mcp_deployment.id
  rest_api_id   = aws_api_gateway_rest_api.mcp_api.id
  stage_name    = var.stage_name

  xray_tracing_enabled = true
}

# Method Settings: Throttling for all methods in stage (AWS format: */* not /*/*)
resource "aws_api_gateway_method_settings" "mcp_post" {
  rest_api_id = aws_api_gateway_rest_api.mcp_api.id
  stage_name  = aws_api_gateway_stage.prod.stage_name
  method_path = "*/*"

  settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
}

# Usage Plan
resource "aws_api_gateway_usage_plan" "mcp_usage_plan" {
  name = "${local.lambda_name}-usage-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.mcp_api.id
    stage  = aws_api_gateway_stage.prod.stage_name
  }

  quota_settings {
    limit  = var.api_quota_limit
    period = "DAY"
  }

  throttle_settings {
    burst_limit = var.api_burst_limit
    rate_limit  = var.api_rate_limit
  }
}
