# ──────────────────────────────────────────────────────────────
# API Gateway (REST API)
# ──────────────────────────────────────────────────────────────

resource "aws_api_gateway_rest_api" "connector" {
  name        = var.project_name
  description = "OpenAI-compatible gateway for AWS Bedrock"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = var.tags
}

# ──────────────────────────────────────────────────────────────
# /v1 resource
# ──────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "v1" {
  rest_api_id = aws_api_gateway_rest_api.connector.id
  parent_id   = aws_api_gateway_rest_api.connector.root_resource_id
  path_part   = "v1"
}

# ──────────────────────────────────────────────────────────────
# /v1/chat resource
# ──────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "chat" {
  rest_api_id = aws_api_gateway_rest_api.connector.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "chat"
}

# ──────────────────────────────────────────────────────────────
# /v1/chat/completions — POST
# ──────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "completions" {
  rest_api_id = aws_api_gateway_rest_api.connector.id
  parent_id   = aws_api_gateway_resource.chat.id
  path_part   = "completions"
}

resource "aws_api_gateway_method" "completions_post" {
  rest_api_id   = aws_api_gateway_rest_api.connector.id
  resource_id   = aws_api_gateway_resource.completions.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "completions_post" {
  rest_api_id             = aws_api_gateway_rest_api.connector.id
  resource_id             = aws_api_gateway_resource.completions.id
  http_method             = aws_api_gateway_method.completions_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.connector.invoke_arn
}

# ──────────────────────────────────────────────────────────────
# /v1/chat/completions — OPTIONS (CORS preflight)
# ──────────────────────────────────────────────────────────────

resource "aws_api_gateway_method" "completions_options" {
  rest_api_id   = aws_api_gateway_rest_api.connector.id
  resource_id   = aws_api_gateway_resource.completions.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "completions_options" {
  rest_api_id = aws_api_gateway_rest_api.connector.id
  resource_id = aws_api_gateway_resource.completions.id
  http_method = aws_api_gateway_method.completions_options.http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

resource "aws_api_gateway_method_response" "completions_options_200" {
  rest_api_id = aws_api_gateway_rest_api.connector.id
  resource_id = aws_api_gateway_resource.completions.id
  http_method = aws_api_gateway_method.completions_options.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }

  response_models = {
    "application/json" = "Empty"
  }
}

resource "aws_api_gateway_integration_response" "completions_options_200" {
  rest_api_id = aws_api_gateway_rest_api.connector.id
  resource_id = aws_api_gateway_resource.completions.id
  http_method = aws_api_gateway_method.completions_options.http_method
  status_code = aws_api_gateway_method_response.completions_options_200.status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'POST,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }
}

# ──────────────────────────────────────────────────────────────
# /v1/models — GET
# ──────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "models" {
  rest_api_id = aws_api_gateway_rest_api.connector.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "models"
}

resource "aws_api_gateway_method" "models_get" {
  rest_api_id   = aws_api_gateway_rest_api.connector.id
  resource_id   = aws_api_gateway_resource.models.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "models_get" {
  rest_api_id             = aws_api_gateway_rest_api.connector.id
  resource_id             = aws_api_gateway_resource.models.id
  http_method             = aws_api_gateway_method.models_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.connector.invoke_arn
}

# ──────────────────────────────────────────────────────────────
# Deployment + Stage
# ──────────────────────────────────────────────────────────────

resource "aws_api_gateway_deployment" "connector" {
  rest_api_id = aws_api_gateway_rest_api.connector.id

  # Redeploy when any method/integration changes
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.v1.id,
      aws_api_gateway_resource.chat.id,
      aws_api_gateway_resource.completions.id,
      aws_api_gateway_resource.models.id,
      aws_api_gateway_method.completions_post.id,
      aws_api_gateway_integration.completions_post.id,
      aws_api_gateway_method.models_get.id,
      aws_api_gateway_integration.models_get.id,
      aws_api_gateway_method.completions_options.id,
      aws_api_gateway_integration.completions_options.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "connector" {
  deployment_id = aws_api_gateway_deployment.connector.id
  rest_api_id   = aws_api_gateway_rest_api.connector.id
  stage_name    = var.stage_name
  tags          = var.tags
}
