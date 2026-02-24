# Terraform Deployment

Alternative to SAM for teams already using Terraform.

## Quick Start

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars if needed

terraform init
terraform plan
terraform apply
```

## What Gets Created

| Resource | Description |
|----------|-------------|
| **Lambda Function** | Python 3.12, 256MB, 120s timeout |
| **API Gateway (REST)** | Regional endpoint with CORS |
| **IAM Role + Policy** | Least-privilege (Secrets Manager read, Bedrock invoke, CloudWatch logs) |
| **CloudWatch Log Group** | 90-day retention |

## Endpoints

After `terraform apply`, the outputs will show:

```
api_endpoint      = "https://abc123.execute-api.us-east-1.amazonaws.com/v1"
api_endpoint_chat = "https://abc123.execute-api.us-east-1.amazonaws.com/v1/v1/chat/completions"
```

## Customization

Override any variable in `terraform.tfvars`:

```hcl
aws_region         = "us-west-2"
project_name       = "my-bedrock-proxy"
lambda_memory      = 512
lambda_timeout     = 180
log_retention_days = 30

tags = {
  Project     = "my-bedrock-proxy"
  Environment = "production"
  Team        = "platform"
}
```

## Teardown

```bash
terraform destroy
```
