# ──────────────────────────────────────────────────────────────
# Outputs
# ──────────────────────────────────────────────────────────────

output "api_endpoint" {
  description = "API Gateway endpoint URL — use as your API base URL"
  value       = "${aws_api_gateway_stage.connector.invoke_url}"
}

output "api_endpoint_chat" {
  description = "Full chat completions URL"
  value       = "${aws_api_gateway_stage.connector.invoke_url}/v1/chat/completions"
}

output "lambda_function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.connector.function_name
}

output "lambda_function_arn" {
  description = "Lambda function ARN"
  value       = aws_lambda_function.connector.arn
}

output "lambda_role_arn" {
  description = "Lambda execution role ARN"
  value       = aws_iam_role.lambda.arn
}

output "log_group" {
  description = "CloudWatch log group name"
  value       = aws_cloudwatch_log_group.lambda.name
}

output "api_id" {
  description = "API Gateway REST API ID"
  value       = aws_api_gateway_rest_api.connector.id
}
