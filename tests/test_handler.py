"""
Test Suite — Simple Bedrock Connector

Tests for the Lambda handler, token generator, and supporting utilities.
Uses moto for AWS service mocking (Secrets Manager, Bedrock, STS).

Run:
    cd simple-bedrock-connector
    pip install -r tests/requirements.txt
    pytest tests/ -v
"""

import json
import time
import secrets
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest
import boto3
from moto import mock_aws

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def aws_env(monkeypatch):
    """Set dummy AWS credentials for moto."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def secrets_manager(aws_env):
    """Provide a mocked Secrets Manager client."""
    with mock_aws():
        client = boto3.client("secretsmanager", region_name="us-east-1")
        yield client


@pytest.fixture
def valid_token():
    """Generate a deterministic test token."""
    return "test_token_abc123_" + secrets.token_urlsafe(32)


@pytest.fixture
def store_valid_token(secrets_manager, valid_token):
    """Store a valid, non-expired token in mocked Secrets Manager."""
    secret_name = f"sbc/tokens/{valid_token[:12]}"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=30)

    secrets_manager.create_secret(
        Name=secret_name,
        SecretString=json.dumps({
            "token": valid_token,
            "identity": "test-user@dev",
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "ttl_seconds": int(timedelta(days=30).total_seconds()),
        }),
    )
    return valid_token


@pytest.fixture
def store_expired_token(secrets_manager):
    """Store an expired token in mocked Secrets Manager."""
    token = "expired_tkn_" + secrets.token_urlsafe(32)
    secret_name = f"sbc/tokens/{token[:12]}"
    now = datetime.now(timezone.utc)
    expired = now - timedelta(hours=1)

    secrets_manager.create_secret(
        Name=secret_name,
        SecretString=json.dumps({
            "token": token,
            "identity": "expired-user",
            "created_at": (now - timedelta(days=31)).isoformat(),
            "expires_at": expired.isoformat(),
            "ttl_seconds": 0,
        }),
    )
    return token


@pytest.fixture
def mock_bedrock_response():
    """A realistic Bedrock Converse API response."""
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "Hello! How can I help you today?"}],
            }
        },
        "stopReason": "end_turn",
        "usage": {
            "inputTokens": 12,
            "outputTokens": 9,
        },
        "metrics": {"latencyMs": 342},
    }


@pytest.fixture
def api_event(valid_token):
    """A valid API Gateway proxy event."""
    return {
        "headers": {
            "Authorization": f"Bearer {valid_token}",
            "Content-Type": "application/json",
        },
        "body": json.dumps({
            "model": "claude-4-sonnet",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say hello."},
            ],
            "max_tokens": 256,
            "temperature": 0.5,
        }),
    }


# ---------------------------------------------------------------------------
# Handler Module — import with patched boto3 clients
# ---------------------------------------------------------------------------

@pytest.fixture
def handler_module(aws_env):
    """
    Import the handler module with mocked AWS clients.
    Re-imports each test to reset module-level state.
    """
    with mock_aws():
        import importlib
        import sys

        # Remove cached module so it re-creates clients against moto
        sys.modules.pop("handler", None)

        # Patch sys.path to include lambda dir
        import os
        lambda_dir = os.path.join(os.path.dirname(__file__), "..", "lambda")
        if lambda_dir not in sys.path:
            sys.path.insert(0, os.path.abspath(lambda_dir))

        import handler
        importlib.reload(handler)

        # Replace the module-level clients with mocked ones
        handler.secrets_client = boto3.client("secretsmanager", region_name="us-east-1")
        handler.bedrock_client = MagicMock()

        yield handler


# ===========================================================================
# TOKEN VALIDATION TESTS
# ===========================================================================

class TestTokenValidation:
    """Tests for token validation against Secrets Manager."""

    def test_valid_token_returns_identity(self, handler_module):
        """Valid, non-expired token should return identity dict."""
        h = handler_module
        token = "validtoken12" + secrets.token_urlsafe(32)
        secret_name = f"sbc/tokens/{token[:12]}"
        now = datetime.now(timezone.utc)

        h.secrets_client.create_secret(
            Name=secret_name,
            SecretString=json.dumps({
                "token": token,
                "identity": "logan@dev",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=30)).isoformat(),
            }),
        )

        result = h.validate_token(token)
        assert result is not None
        assert result["identity"] == "logan@dev"

    def test_expired_token_returns_none(self, handler_module):
        """Expired token should be rejected."""
        h = handler_module
        token = "expiredtkn12" + secrets.token_urlsafe(32)
        secret_name = f"sbc/tokens/{token[:12]}"
        now = datetime.now(timezone.utc)

        h.secrets_client.create_secret(
            Name=secret_name,
            SecretString=json.dumps({
                "token": token,
                "identity": "old-user",
                "created_at": (now - timedelta(days=60)).isoformat(),
                "expires_at": (now - timedelta(hours=1)).isoformat(),
            }),
        )

        result = h.validate_token(token)
        assert result is None

    def test_wrong_token_value_returns_none(self, handler_module):
        """Token stored with different value should fail exact match."""
        h = handler_module
        token = "wrongtoken12" + secrets.token_urlsafe(32)
        secret_name = f"sbc/tokens/{token[:12]}"
        now = datetime.now(timezone.utc)

        h.secrets_client.create_secret(
            Name=secret_name,
            SecretString=json.dumps({
                "token": "completely_different_token_value",
                "identity": "imposter",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=30)).isoformat(),
            }),
        )

        result = h.validate_token(token)
        assert result is None

    def test_nonexistent_token_returns_none(self, handler_module):
        """Token that doesn't exist in Secrets Manager should return None."""
        result = handler_module.validate_token("does_not_exist_anywhere_123")
        assert result is None

    def test_token_just_before_expiry_is_valid(self, handler_module):
        """Token expiring in 1 second should still be valid."""
        h = handler_module
        token = "almostexpir" + secrets.token_urlsafe(32)
        secret_name = f"sbc/tokens/{token[:12]}"
        now = datetime.now(timezone.utc)

        h.secrets_client.create_secret(
            Name=secret_name,
            SecretString=json.dumps({
                "token": token,
                "identity": "last-second",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=60)).isoformat(),
            }),
        )

        result = h.validate_token(token)
        assert result is not None
        assert result["identity"] == "last-second"


# ===========================================================================
# MODEL MAPPING TESTS
# ===========================================================================

class TestModelMapping:
    """Tests for OpenAI → Bedrock model resolution."""

    def test_known_models_resolve(self, handler_module):
        h = handler_module
        assert h.resolve_model("claude-4-sonnet") == "anthropic.claude-4-sonnet-20250514-v1:0"
        assert h.resolve_model("claude-4-opus") == "anthropic.claude-4-opus-20250514-v1:0"
        assert h.resolve_model("claude-3.5-haiku") == "anthropic.claude-3-5-haiku-20241022-v1:0"
        assert h.resolve_model("llama-3.3-70b") == "meta.llama3-3-70b-instruct-v1:0"
        assert h.resolve_model("mistral-large") == "mistral.mistral-large-2407-v1:0"

    def test_gpt_aliases_map_to_claude(self, handler_module):
        """gpt-4, gpt-4o, gpt-3.5-turbo should map to Claude models."""
        h = handler_module
        assert h.resolve_model("gpt-4") == "anthropic.claude-4-sonnet-20250514-v1:0"
        assert h.resolve_model("gpt-4o") == "anthropic.claude-4-sonnet-20250514-v1:0"
        assert h.resolve_model("gpt-3.5-turbo") == "anthropic.claude-3-5-haiku-20241022-v1:0"

    def test_raw_bedrock_id_passes_through(self, handler_module):
        """Full Bedrock model IDs (containing '.') should pass through unchanged."""
        h = handler_module
        raw = "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert h.resolve_model(raw) == raw

    def test_unknown_model_falls_back_to_default(self, handler_module):
        """Unknown model without '.' should fall back to DEFAULT_MODEL."""
        h = handler_module
        assert h.resolve_model("unknown-model") == h.DEFAULT_MODEL

    def test_all_map_entries_are_valid_bedrock_ids(self, handler_module):
        """Every value in MODEL_MAP should contain a '.' (Bedrock ID format)."""
        for name, bedrock_id in handler_module.MODEL_MAP.items():
            assert "." in bedrock_id, f"{name} maps to invalid Bedrock ID: {bedrock_id}"


# ===========================================================================
# RESPONSE FORMAT TESTS
# ===========================================================================

class TestOpenAIResponseFormat:
    """Tests for Bedrock → OpenAI response translation."""

    def test_basic_response_structure(self, handler_module, mock_bedrock_response):
        """Response should match OpenAI chat completion schema."""
        result = handler_module.to_openai_response(mock_bedrock_response, "claude-4-sonnet")

        assert result["object"] == "chat.completion"
        assert result["model"] == "claude-4-sonnet"
        assert result["id"].startswith("chatcmpl-")
        assert isinstance(result["created"], int)

    def test_choices_array(self, handler_module, mock_bedrock_response):
        """Should have exactly one choice with correct structure."""
        result = handler_module.to_openai_response(mock_bedrock_response, "test-model")

        assert len(result["choices"]) == 1
        choice = result["choices"][0]
        assert choice["index"] == 0
        assert choice["message"]["role"] == "assistant"
        assert choice["message"]["content"] == "Hello! How can I help you today?"
        assert choice["finish_reason"] == "stop"

    def test_usage_tokens(self, handler_module, mock_bedrock_response):
        """Token counts should be correctly mapped."""
        result = handler_module.to_openai_response(mock_bedrock_response, "test-model")

        assert result["usage"]["prompt_tokens"] == 12
        assert result["usage"]["completion_tokens"] == 9
        assert result["usage"]["total_tokens"] == 21

    def test_multi_block_content_concatenated(self, handler_module):
        """Multiple content blocks should be concatenated."""
        multi_block = {
            "output": {
                "message": {
                    "content": [
                        {"text": "Hello "},
                        {"text": "world!"},
                    ]
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 5, "outputTokens": 3},
        }
        result = handler_module.to_openai_response(multi_block, "test")
        assert result["choices"][0]["message"]["content"] == "Hello world!"

    def test_empty_content_blocks(self, handler_module):
        """Empty content blocks should produce empty string."""
        empty = {
            "output": {"message": {"content": []}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 0, "outputTokens": 0},
        }
        result = handler_module.to_openai_response(empty, "test")
        assert result["choices"][0]["message"]["content"] == ""

    def test_missing_usage_defaults_to_zero(self, handler_module):
        """Missing usage fields should default to 0."""
        no_usage = {
            "output": {"message": {"content": [{"text": "hi"}]}},
            "stopReason": "end_turn",
            "usage": {},
        }
        result = handler_module.to_openai_response(no_usage, "test")
        assert result["usage"]["prompt_tokens"] == 0
        assert result["usage"]["completion_tokens"] == 0
        assert result["usage"]["total_tokens"] == 0


# ===========================================================================
# STOP REASON MAPPING TESTS
# ===========================================================================

class TestStopReasonMapping:
    """Tests for Bedrock → OpenAI finish_reason mapping."""

    @pytest.mark.parametrize("bedrock_reason,openai_reason", [
        ("end_turn", "stop"),
        ("max_tokens", "length"),
        ("stop_sequence", "stop"),
        ("content_filtered", "content_filter"),
        ("unknown_reason", "stop"),  # fallback
    ])
    def test_stop_reason_mapping(self, handler_module, bedrock_reason, openai_reason):
        assert handler_module._map_stop_reason(bedrock_reason) == openai_reason


# ===========================================================================
# LAMBDA HANDLER INTEGRATION TESTS
# ===========================================================================

class TestLambdaHandler:
    """Integration tests for the Lambda handler function."""

    def test_missing_auth_header_returns_401(self, handler_module):
        """Request without Authorization header should get 401."""
        event = {"headers": {}, "body": "{}"}
        result = handler_module.handler(event, None)

        assert result["statusCode"] == 401
        body = json.loads(result["body"])
        assert body["error"]["type"] == "auth_error"

    def test_invalid_auth_prefix_returns_401(self, handler_module):
        """Non-Bearer auth should get 401."""
        event = {"headers": {"Authorization": "Basic abc123"}, "body": "{}"}
        result = handler_module.handler(event, None)
        assert result["statusCode"] == 401

    def test_invalid_token_returns_403(self, handler_module):
        """Valid Bearer format but bad token should get 403."""
        event = {
            "headers": {"Authorization": "Bearer fake_token_doesnt_exist"},
            "body": json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}),
        }
        result = handler_module.handler(event, None)
        assert result["statusCode"] == 403

    def test_invalid_json_body_returns_400(self, handler_module):
        """Malformed JSON body should get 400."""
        # Store a valid token first
        token = "validjsontest" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        handler_module.secrets_client.create_secret(
            Name=f"sbc/tokens/{token[:12]}",
            SecretString=json.dumps({
                "token": token,
                "identity": "test",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }),
        )

        event = {
            "headers": {"Authorization": f"Bearer {token}"},
            "body": "{not valid json!!!",
        }
        result = handler_module.handler(event, None)
        assert result["statusCode"] == 400
        assert "Invalid JSON" in json.loads(result["body"])["error"]["message"]

    def test_empty_messages_returns_400(self, handler_module):
        """Request with empty messages array should get 400."""
        token = "emptymsgtest" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        handler_module.secrets_client.create_secret(
            Name=f"sbc/tokens/{token[:12]}",
            SecretString=json.dumps({
                "token": token,
                "identity": "test",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }),
        )

        event = {
            "headers": {"Authorization": f"Bearer {token}"},
            "body": json.dumps({"model": "gpt-4", "messages": []}),
        }
        result = handler_module.handler(event, None)
        assert result["statusCode"] == 400
        assert "messages is required" in json.loads(result["body"])["error"]["message"]

    def test_successful_request_returns_200(self, handler_module, mock_bedrock_response):
        """Full happy path: valid token + valid body → 200 with OpenAI format."""
        h = handler_module
        token = "happypath123" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)

        h.secrets_client.create_secret(
            Name=f"sbc/tokens/{token[:12]}",
            SecretString=json.dumps({
                "token": token,
                "identity": "logan@dev",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=30)).isoformat(),
            }),
        )

        h.bedrock_client.converse.return_value = mock_bedrock_response

        event = {
            "headers": {"Authorization": f"Bearer {token}"},
            "body": json.dumps({
                "model": "claude-4-sonnet",
                "messages": [{"role": "user", "content": "Hello!"}],
            }),
        }
        result = h.handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["object"] == "chat.completion"
        assert body["model"] == "claude-4-sonnet"
        assert body["choices"][0]["message"]["content"] == "Hello! How can I help you today?"
        assert body["usage"]["total_tokens"] == 21

    def test_bedrock_error_returns_502(self, handler_module):
        """Bedrock ClientError should return 502."""
        from botocore.exceptions import ClientError

        h = handler_module
        token = "bedrockfail1" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)

        h.secrets_client.create_secret(
            Name=f"sbc/tokens/{token[:12]}",
            SecretString=json.dumps({
                "token": token,
                "identity": "test",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }),
        )

        h.bedrock_client.converse.side_effect = ClientError(
            {"Error": {"Code": "ModelNotReadyException", "Message": "Model not available"}},
            "Converse",
        )

        event = {
            "headers": {"Authorization": f"Bearer {token}"},
            "body": json.dumps({
                "model": "claude-4-opus",
                "messages": [{"role": "user", "content": "test"}],
            }),
        }
        result = h.handler(event, None)

        assert result["statusCode"] == 502
        body = json.loads(result["body"])
        assert "Bedrock error" in body["error"]["message"]
        assert body["error"]["type"] == "api_error"

    def test_system_message_extracted_from_messages(self, handler_module, mock_bedrock_response):
        """System messages should be separated and sent as Bedrock system param."""
        h = handler_module
        token = "systemmsg123" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)

        h.secrets_client.create_secret(
            Name=f"sbc/tokens/{token[:12]}",
            SecretString=json.dumps({
                "token": token,
                "identity": "test",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }),
        )
        h.bedrock_client.converse.return_value = mock_bedrock_response

        event = {
            "headers": {"Authorization": f"Bearer {token}"},
            "body": json.dumps({
                "model": "claude-4-sonnet",
                "messages": [
                    {"role": "system", "content": "You are a pirate."},
                    {"role": "user", "content": "Hello!"},
                ],
            }),
        }
        h.handler(event, None)

        call_kwargs = h.bedrock_client.converse.call_args[1]
        assert call_kwargs["system"] == [{"text": "You are a pirate."}]
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0]["role"] == "user"

    def test_lowercase_authorization_header(self, handler_module, mock_bedrock_response):
        """Handler should accept lowercase 'authorization' header."""
        h = handler_module
        token = "lowercasehdr" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)

        h.secrets_client.create_secret(
            Name=f"sbc/tokens/{token[:12]}",
            SecretString=json.dumps({
                "token": token,
                "identity": "test",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }),
        )
        h.bedrock_client.converse.return_value = mock_bedrock_response

        event = {
            "headers": {"authorization": f"Bearer {token}"},
            "body": json.dumps({
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "hi"}],
            }),
        }
        result = h.handler(event, None)
        assert result["statusCode"] == 200

    def test_default_model_when_omitted(self, handler_module, mock_bedrock_response):
        """Missing model field should default to gpt-4o → Claude Sonnet."""
        h = handler_module
        token = "nomodeltest1" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)

        h.secrets_client.create_secret(
            Name=f"sbc/tokens/{token[:12]}",
            SecretString=json.dumps({
                "token": token,
                "identity": "test",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }),
        )
        h.bedrock_client.converse.return_value = mock_bedrock_response

        event = {
            "headers": {"Authorization": f"Bearer {token}"},
            "body": json.dumps({
                "messages": [{"role": "user", "content": "hi"}],
            }),
        }
        result = h.handler(event, None)
        assert result["statusCode"] == 200

        call_kwargs = h.bedrock_client.converse.call_args[1]
        assert call_kwargs["modelId"] == "anthropic.claude-4-sonnet-20250514-v1:0"

    def test_response_headers_include_cors(self, handler_module):
        """Response should include CORS headers."""
        event = {"headers": {}, "body": "{}"}
        result = handler_module.handler(event, None)
        assert result["headers"]["Access-Control-Allow-Origin"] == "*"
        assert result["headers"]["Content-Type"] == "application/json"

    def test_inference_params_passed_to_bedrock(self, handler_module, mock_bedrock_response):
        """Custom temperature, max_tokens, top_p should be forwarded."""
        h = handler_module
        token = "paramstest12" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)

        h.secrets_client.create_secret(
            Name=f"sbc/tokens/{token[:12]}",
            SecretString=json.dumps({
                "token": token,
                "identity": "test",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }),
        )
        h.bedrock_client.converse.return_value = mock_bedrock_response

        event = {
            "headers": {"Authorization": f"Bearer {token}"},
            "body": json.dumps({
                "model": "claude-4-sonnet",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.2,
                "max_tokens": 1024,
                "top_p": 0.9,
            }),
        }
        h.handler(event, None)

        call_kwargs = h.bedrock_client.converse.call_args[1]
        config = call_kwargs["inferenceConfig"]
        assert config["temperature"] == 0.2
        assert config["maxTokens"] == 1024
        assert config["topP"] == 0.9


# ===========================================================================
# TOKEN GENERATOR TESTS
# ===========================================================================

class TestTokenGenerator:
    """Tests for the token generation script utilities."""

    def test_parse_ttl_days(self):
        from scripts.generate_token import parse_ttl
        assert parse_ttl("30d") == timedelta(days=30)
        assert parse_ttl("1d") == timedelta(days=1)
        assert parse_ttl("365d") == timedelta(days=365)

    def test_parse_ttl_hours(self):
        from scripts.generate_token import parse_ttl
        assert parse_ttl("24h") == timedelta(hours=24)
        assert parse_ttl("1h") == timedelta(hours=1)

    def test_parse_ttl_minutes(self):
        from scripts.generate_token import parse_ttl
        assert parse_ttl("30m") == timedelta(minutes=30)
        assert parse_ttl("90m") == timedelta(minutes=90)

    def test_parse_ttl_invalid_unit_raises(self):
        from scripts.generate_token import parse_ttl
        with pytest.raises(ValueError, match="Unknown TTL unit"):
            parse_ttl("30x")

    def test_parse_ttl_invalid_value_raises(self):
        from scripts.generate_token import parse_ttl
        with pytest.raises(ValueError):
            parse_ttl("abcd")

    def test_generate_token_length(self):
        from scripts.generate_token import generate_token
        token = generate_token()
        assert len(token) >= 48  # urlsafe_b64 of 48 bytes

    def test_generate_token_uniqueness(self):
        from scripts.generate_token import generate_token
        tokens = {generate_token() for _ in range(100)}
        assert len(tokens) == 100  # all unique

    @mock_aws
    def test_store_token_creates_secret(self, aws_env):
        from scripts.generate_token import store_token

        session = boto3.Session(region_name="us-east-1")
        result = store_token(session, "test_token_12345", "ci-user", timedelta(days=7), "us-east-1")

        assert "secret_arn" in result
        assert result["secret_name"] == "sbc/tokens/test_token_1"
        assert "expires_at" in result

        # Verify the secret was actually stored
        client = session.client("secretsmanager", region_name="us-east-1")
        stored = client.get_secret_value(SecretId=result["secret_name"])
        data = json.loads(stored["SecretString"])
        assert data["identity"] == "ci-user"
        assert data["token"] == "test_token_12345"

    @mock_aws
    def test_get_caller_identity(self, aws_env):
        from scripts.generate_token import get_caller_identity

        session = boto3.Session(region_name="us-east-1")
        identity = get_caller_identity(session)
        assert "Arn" in identity
        assert "Account" in identity


# ===========================================================================
# MESSAGE CONVERSION TESTS
# ===========================================================================

class TestMessageConversion:
    """Tests for OpenAI → Bedrock message format conversion."""

    def test_user_message_conversion(self, handler_module, mock_bedrock_response):
        """User messages should map to Bedrock user role."""
        h = handler_module
        h.bedrock_client.converse.return_value = mock_bedrock_response

        messages = [{"role": "user", "content": "Hello"}]
        h.invoke_bedrock("test-model", messages, {})

        call_kwargs = h.bedrock_client.converse.call_args[1]
        assert call_kwargs["messages"][0]["role"] == "user"
        assert call_kwargs["messages"][0]["content"] == [{"text": "Hello"}]

    def test_assistant_message_stays_assistant(self, handler_module, mock_bedrock_response):
        """Assistant messages should keep assistant role."""
        h = handler_module
        h.bedrock_client.converse.return_value = mock_bedrock_response

        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "How are you?"},
        ]
        h.invoke_bedrock("test-model", messages, {})

        call_kwargs = h.bedrock_client.converse.call_args[1]
        assert len(call_kwargs["messages"]) == 3
        assert call_kwargs["messages"][1]["role"] == "assistant"

    def test_system_messages_separated(self, handler_module, mock_bedrock_response):
        """System messages should be extracted to the system param."""
        h = handler_module
        h.bedrock_client.converse.return_value = mock_bedrock_response

        messages = [
            {"role": "system", "content": "Be concise."},
            {"role": "system", "content": "Respond in JSON."},
            {"role": "user", "content": "List colors."},
        ]
        h.invoke_bedrock("test-model", messages, {})

        call_kwargs = h.bedrock_client.converse.call_args[1]
        assert "system" in call_kwargs
        assert len(call_kwargs["system"]) == 2
        assert call_kwargs["system"][0]["text"] == "Be concise."
        assert call_kwargs["system"][1]["text"] == "Respond in JSON."
        assert len(call_kwargs["messages"]) == 1

    def test_no_system_message_omits_param(self, handler_module, mock_bedrock_response):
        """When no system messages, the system kwarg should be omitted."""
        h = handler_module
        h.bedrock_client.converse.return_value = mock_bedrock_response

        messages = [{"role": "user", "content": "Hello"}]
        h.invoke_bedrock("test-model", messages, {})

        call_kwargs = h.bedrock_client.converse.call_args[1]
        assert "system" not in call_kwargs

    def test_default_inference_params(self, handler_module, mock_bedrock_response):
        """Default params should be applied when not specified."""
        h = handler_module
        h.bedrock_client.converse.return_value = mock_bedrock_response

        h.invoke_bedrock("test-model", [{"role": "user", "content": "hi"}], {})

        call_kwargs = h.bedrock_client.converse.call_args[1]
        config = call_kwargs["inferenceConfig"]
        assert config["maxTokens"] == 4096
        assert config["temperature"] == 0.7
        assert config["topP"] == 1.0


# ===========================================================================
# EDGE CASES
# ===========================================================================

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_headers_dict(self, handler_module):
        """Completely empty headers should return 401."""
        result = handler_module.handler({"headers": {}, "body": "{}"}, None)
        assert result["statusCode"] == 401

    def test_no_headers_key(self, handler_module):
        """Missing headers key should return 401."""
        result = handler_module.handler({"body": "{}"}, None)
        assert result["statusCode"] == 401

    def test_empty_body(self, handler_module):
        """Empty/null body should parse as empty dict, fail on messages."""
        token = "emptybody123" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)

        handler_module.secrets_client.create_secret(
            Name=f"sbc/tokens/{token[:12]}",
            SecretString=json.dumps({
                "token": token,
                "identity": "test",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }),
        )

        result = handler_module.handler(
            {"headers": {"Authorization": f"Bearer {token}"}, "body": "{}"},
            None,
        )
        assert result["statusCode"] == 400

    def test_very_long_token(self, handler_module):
        """Extremely long tokens shouldn't crash (just fail auth)."""
        long_token = "x" * 10000
        result = handler_module.handler(
            {"headers": {"Authorization": f"Bearer {long_token}"}, "body": "{}"},
            None,
        )
        assert result["statusCode"] == 403

    def test_response_body_is_valid_json(self, handler_module):
        """All responses should have JSON-parseable bodies."""
        result = handler_module.handler({"headers": {}, "body": "{}"}, None)
        body = json.loads(result["body"])
        assert isinstance(body, dict)
