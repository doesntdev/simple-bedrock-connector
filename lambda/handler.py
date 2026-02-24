"""
Lambda Handler — Simple Bedrock Connector

Accepts OpenAI-format requests, validates bearer tokens against
Secrets Manager, logs to CloudWatch, routes to Bedrock, and returns
OpenAI-compatible responses.

Endpoint: POST /v1/chat/completions
Auth: Bearer <token>
"""

import json
import os
import time
import uuid
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Clients (reused across invocations)
secrets_client = boto3.client("secretsmanager")
bedrock_client = boto3.client("bedrock-runtime")

SECRET_PREFIX = "sbc/tokens/"

# Model mapping: OpenAI model name → Bedrock model ID
MODEL_MAP = {
    # Claude
    "claude-4-opus": "anthropic.claude-4-opus-20250514-v1:0",
    "claude-4-sonnet": "anthropic.claude-4-sonnet-20250514-v1:0",
    "claude-3.5-sonnet": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "claude-3.5-haiku": "anthropic.claude-3-5-haiku-20241022-v1:0",
    # Llama
    "llama-3.3-70b": "meta.llama3-3-70b-instruct-v1:0",
    "llama-3.1-405b": "meta.llama3-1-405b-instruct-v1:0",
    # Mistral
    "mistral-large": "mistral.mistral-large-2407-v1:0",
    # Default
    "gpt-4": "anthropic.claude-4-sonnet-20250514-v1:0",
    "gpt-4o": "anthropic.claude-4-sonnet-20250514-v1:0",
    "gpt-3.5-turbo": "anthropic.claude-3-5-haiku-20241022-v1:0",
}

DEFAULT_MODEL = "anthropic.claude-4-sonnet-20250514-v1:0"


def validate_token(token: str) -> dict | None:
    """Validate bearer token against Secrets Manager. Returns identity or None."""
    secret_name = f"{SECRET_PREFIX}{token[:12]}"
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        secret = json.loads(response["SecretString"])
        
        # Verify exact token match
        if secret.get("token") != token:
            return None
        
        # Check expiry
        expires_at = datetime.fromisoformat(secret["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            logger.warning(f"Token expired for identity: {secret.get('identity')}")
            return None
        
        return {
            "identity": secret["identity"],
            "created_at": secret["created_at"],
            "expires_at": secret["expires_at"],
        }
    except ClientError:
        return None


def resolve_model(model_name: str) -> str:
    """Map OpenAI model name to Bedrock model ID."""
    return MODEL_MAP.get(model_name, model_name if "." in model_name else DEFAULT_MODEL)


def invoke_bedrock(model_id: str, messages: list, params: dict) -> dict:
    """Invoke Bedrock with Converse API and return response."""
    # Convert OpenAI messages to Bedrock Converse format
    bedrock_messages = []
    system_prompts = []
    
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        if role == "system":
            system_prompts.append({"text": content})
        else:
            bedrock_role = "user" if role == "user" else "assistant"
            bedrock_messages.append({
                "role": bedrock_role,
                "content": [{"text": content}],
            })
    
    kwargs = {
        "modelId": model_id,
        "messages": bedrock_messages,
        "inferenceConfig": {
            "maxTokens": params.get("max_tokens", 4096),
            "temperature": params.get("temperature", 0.7),
            "topP": params.get("top_p", 1.0),
        },
    }
    if system_prompts:
        kwargs["system"] = system_prompts
    
    response = bedrock_client.converse(**kwargs)
    return response


def to_openai_response(bedrock_response: dict, model_name: str) -> dict:
    """Convert Bedrock Converse response to OpenAI chat completion format."""
    output = bedrock_response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])
    text = "".join(b.get("text", "") for b in content_blocks)
    
    usage = bedrock_response.get("usage", {})
    
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:29]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": _map_stop_reason(
                    bedrock_response.get("stopReason", "end_turn")
                ),
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("inputTokens", 0),
            "completion_tokens": usage.get("outputTokens", 0),
            "total_tokens": usage.get("inputTokens", 0) + usage.get("outputTokens", 0),
        },
    }


def _map_stop_reason(reason: str) -> str:
    """Map Bedrock stop reasons to OpenAI finish reasons."""
    mapping = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "content_filtered": "content_filter",
    }
    return mapping.get(reason, "stop")


def response(status_code: int, body: dict) -> dict:
    """Build API Gateway response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def handler(event, context):
    """Lambda entry point."""
    # Extract auth header
    headers = event.get("headers", {})
    auth_header = headers.get("authorization", headers.get("Authorization", ""))
    
    if not auth_header.startswith("Bearer "):
        return response(401, {"error": {"message": "Missing or invalid Authorization header", "type": "auth_error"}})
    
    token = auth_header[7:]
    
    # Validate token
    identity = validate_token(token)
    if not identity:
        return response(403, {"error": {"message": "Invalid or expired token", "type": "auth_error"}})
    
    # Parse request body
    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return response(400, {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}})
    
    model_name = body.get("model", "gpt-4o")
    messages = body.get("messages", [])
    
    if not messages:
        return response(400, {"error": {"message": "messages is required", "type": "invalid_request_error"}})
    
    # Resolve Bedrock model
    model_id = resolve_model(model_name)
    
    # Log request (CloudWatch)
    logger.info(json.dumps({
        "event": "request",
        "identity": identity["identity"],
        "model_requested": model_name,
        "model_resolved": model_id,
        "message_count": len(messages),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))
    
    # Invoke Bedrock
    try:
        bedrock_response = invoke_bedrock(model_id, messages, {
            "max_tokens": body.get("max_tokens", 4096),
            "temperature": body.get("temperature", 0.7),
            "top_p": body.get("top_p", 1.0),
        })
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        logger.error(f"Bedrock error: {error_code} - {e}")
        return response(502, {"error": {"message": f"Bedrock error: {error_code}", "type": "api_error"}})
    
    # Convert to OpenAI format
    result = to_openai_response(bedrock_response, model_name)
    
    # Log response (CloudWatch)
    logger.info(json.dumps({
        "event": "response",
        "identity": identity["identity"],
        "model": model_name,
        "usage": result["usage"],
        "finish_reason": result["choices"][0]["finish_reason"],
    }))
    
    return response(200, result)
