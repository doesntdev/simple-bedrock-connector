"""
Token Generator — Simple Bedrock Connector

Authenticates via AWS IAM, generates a bearer token, and stores
the token → identity mapping in AWS Secrets Manager.

Usage:
    python generate_token.py --identity "logan@dev" --ttl 30d
    python generate_token.py --identity "team-ci" --ttl 7d --region us-east-1
"""

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone, timedelta

import boto3
from botocore.exceptions import ClientError

SECRET_PREFIX = "sbc/tokens/"


def parse_ttl(ttl_str: str) -> timedelta:
    """Parse TTL string like '30d', '24h', '7d'."""
    unit = ttl_str[-1].lower()
    value = int(ttl_str[:-1])
    if unit == "d":
        return timedelta(days=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "m":
        return timedelta(minutes=value)
    raise ValueError(f"Unknown TTL unit: {unit}. Use d/h/m.")


def generate_token() -> str:
    """Generate a cryptographically secure bearer token."""
    return secrets.token_urlsafe(48)


def store_token(
    session: boto3.Session,
    token: str,
    identity: str,
    ttl: timedelta,
    region: str,
) -> dict:
    """Store token → identity mapping in Secrets Manager."""
    client = session.client("secretsmanager", region_name=region)
    secret_name = f"{SECRET_PREFIX}{token[:12]}"
    
    now = datetime.now(timezone.utc)
    expires_at = now + ttl
    
    secret_value = json.dumps({
        "token": token,
        "identity": identity,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "ttl_seconds": int(ttl.total_seconds()),
    })
    
    try:
        response = client.create_secret(
            Name=secret_name,
            Description=f"SBC token for {identity}",
            SecretString=secret_value,
            Tags=[
                {"Key": "app", "Value": "simple-bedrock-connector"},
                {"Key": "identity", "Value": identity},
                {"Key": "expires_at", "Value": expires_at.isoformat()},
            ],
        )
        return {
            "secret_arn": response["ARN"],
            "secret_name": secret_name,
            "expires_at": expires_at.isoformat(),
        }
    except ClientError as e:
        print(f"Error storing token: {e}", file=sys.stderr)
        sys.exit(1)


def get_caller_identity(session: boto3.Session) -> dict:
    """Verify IAM authentication and return caller identity."""
    sts = session.client("sts")
    try:
        return sts.get_caller_identity()
    except ClientError as e:
        print(f"IAM authentication failed: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a bearer token for Simple Bedrock Connector"
    )
    parser.add_argument(
        "--identity", required=True,
        help="Identity label for this token (e.g. 'logan@dev', 'ci-pipeline')"
    )
    parser.add_argument(
        "--ttl", default="30d",
        help="Token time-to-live (e.g. '30d', '24h', '7d'). Default: 30d"
    )
    parser.add_argument(
        "--region", default="us-east-1",
        help="AWS region. Default: us-east-1"
    )
    parser.add_argument(
        "--profile", default=None,
        help="AWS CLI profile name"
    )
    args = parser.parse_args()
    
    # Parse TTL
    ttl = parse_ttl(args.ttl)
    
    # Authenticate via IAM
    session = boto3.Session(profile_name=args.profile)
    caller = get_caller_identity(session)
    print(f"✓ Authenticated as: {caller['Arn']}")
    
    # Generate token
    token = generate_token()
    
    # Store in Secrets Manager
    result = store_token(session, token, args.identity, ttl, args.region)
    
    print(f"✓ Token generated for identity: {args.identity}")
    print(f"✓ Stored in Secrets Manager: {result['secret_name']}")
    print(f"✓ Expires: {result['expires_at']}")
    print()
    print("━" * 60)
    print(f"  Bearer Token: {token}")
    print("━" * 60)
    print()
    print("Configure your AI coding tool:")
    print(f'  API Key: {token}')
    print(f'  API Base: https://<your-api-id>.execute-api.{args.region}.amazonaws.com/v1')


if __name__ == "__main__":
    main()
