# ──────────────────────────────────────────────────────────────
# Lambda Function
# ──────────────────────────────────────────────────────────────

# Package the handler
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../lambda"
  output_path = "${path.module}/.build/lambda.zip"
}

resource "aws_lambda_function" "connector" {
  function_name    = var.project_name
  description      = "OpenAI-to-Bedrock request proxy with token auth"
  role             = aws_iam_role.lambda.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  memory_size      = var.lambda_memory
  timeout          = var.lambda_timeout
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      POWERTOOLS_SERVICE_NAME = var.project_name
    }
  }

  tags = var.tags
}

# Allow API Gateway to invoke Lambda
resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.connector.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.connector.execution_arn}/*/*"
}

# ──────────────────────────────────────────────────────────────
# CloudWatch Log Group
# ──────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.connector.function_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}
