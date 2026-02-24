# Simple Bedrock Connector

OpenAI-compatible API gateway for AWS Bedrock. Use Claude, Llama, and other Bedrock models from any AI coding tool (Cursor, Copilot, Cline) via a standard `/v1/chat/completions` endpoint.

## Architecture

```
Local Python Script ──→ AWS Secrets Manager (token + identity)
        │
        └── IAM Auth (STS)

AI Coding Tool ──→ API Gateway ──→ Lambda ──→ Bedrock
     ↑               │                │
     └── OpenAI      │                └── CloudWatch
         response     └── Bearer token
                         validation
```

## Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `scripts/generate_token.py` | Local | IAM-authenticated token generator |
| `lambda/handler.py` | AWS Lambda | Request validation, routing, response translation |
| `infra/template.yaml` | SAM/CFN | Infrastructure as code |

## Setup

### Prerequisites
- AWS CLI configured with IAM credentials
- Python 3.11+
- SAM CLI (for deployment)

### 1. Deploy Infrastructure
```bash
cd infra
sam build && sam deploy --guided
```

### 2. Generate Token
```bash
cd scripts
pip install -r requirements.txt
python generate_token.py --identity "logan@dev" --ttl 30d
```

### 3. Configure Coding Tool
```
API Base URL: https://<api-id>.execute-api.<region>.amazonaws.com/v1
API Key: <generated-bearer-token>
```

## Supported Models
- Claude 4 Opus / Sonnet / Haiku (Anthropic via Bedrock)
- Llama 3.x (Meta via Bedrock)
- Mistral Large (Mistral via Bedrock)
- Any Bedrock-supported model

## Cost
- Lambda: ~$0.20/1M invocations
- API Gateway: ~$1.00/1M requests
- Bedrock: Per-model token pricing
- Secrets Manager: $0.40/secret/month
