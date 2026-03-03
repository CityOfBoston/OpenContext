# Deployment Guide

Deploy OpenContext to AWS Lambda. See [Getting Started](GETTING_STARTED.md) for the quick path.

## Prerequisites

- AWS account, AWS CLI configured
- Terraform >= 1.0
- Python 3.11+

## AWS Permissions

- Lambda (create, update functions)
- IAM (roles, policies)
- CloudWatch Logs
- API Gateway / Lambda Function URLs

## Deployment

```bash
# Configure AWS
aws configure

# Create config from template (if needed)
cp config-example.yaml config.yaml
# Edit config.yaml - enable exactly ONE plugin

# Deploy (validates config, packages, deploys)
./scripts/deploy.sh
```

### Manual Terraform

First-time: bootstrap the S3 backend (run once). See [terraform/bootstrap/README.md](../terraform/bootstrap/README.md):

```bash
cd terraform/bootstrap
terraform init && terraform apply
```

Then deploy:

```bash
cd terraform/aws
terraform init
terraform plan -var="config_file=config.yaml"
terraform apply
```

The deploy script copies `config.yaml` into `terraform/aws/` before running Terraform. For manual runs, ensure `config.yaml` exists in the project root or pass the correct path.

## Endpoints

| Endpoint | Use Case | Auth |
|----------|----------|------|
| **API Gateway** | Production | Rate limiting, daily quota |
| **Lambda Function URL** | Testing | None |

### Get URLs

```bash
cd terraform/aws
terraform output -raw api_gateway_url   # Production (includes /mcp)
terraform output -raw lambda_url      # Testing
```

### API Gateway

- **Rate limit:** 100 burst, 50 sustained req/s (configurable via Terraform variables)
- **Daily quota:** 1000 requests/day (configurable via `api_quota_limit`)
- **Stage name:** Default is `staging`; URL format: `https://...execute-api.region.amazonaws.com/staging/mcp`
- **429** when exceeded
- Use for production; Lambda URL has no auth

## Configuration

Config is passed via `OPENCONTEXT_CONFIG` env var. Create `config.yaml` from `config-example.yaml`, edit it, and run `./scripts/deploy.sh` to update.

### Lambda Settings (in config.yaml)

```yaml
aws:
  region: "us-east-1"
  lambda_name: "my-mcp-server"   # Optional; defaults from server_name
  lambda_memory: 512             # 128–10240 MB
  lambda_timeout: 120            # 1–900 seconds
```

## Monitoring

- **CloudWatch Logs:** `/aws/lambda/<function-name>`, 14-day retention
- **Tail logs:** `aws logs tail /aws/lambda/my-mcp-server --follow`

## Updating & Cleanup

**Update:** Change code/config, run `./scripts/deploy.sh` again.

**Destroy:**
```bash
cd terraform/aws
terraform destroy -var="config_file=config.yaml"
```

## Cost (us-east-1)

- Lambda: ~$0.20/1M requests, ~$0.0000166667/GB-second
- Function URL: Free
- Example: 100K req/month, 512 MB, 1s avg ≈ **$1/month**

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Multiple plugins | Enable only ONE in `config.yaml` |
| Lambda timeout | Increase `lambda_timeout` |
| 500 error | Check CloudWatch logs, validate config |
| High cost | Reduce `lambda_memory`, review usage |

## Security

- Use API Gateway for production (rate limiting, quota)
- Lambda URL is public—testing only
- Store secrets in env vars, not code
