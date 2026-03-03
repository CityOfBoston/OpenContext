# Terraform Backend Bootstrap

Creates the S3 bucket and DynamoDB table used by the main OpenContext Terraform configuration for remote state storage.

## Prerequisites

- AWS CLI configured
- Terraform >= 1.0

## Usage

Run this **once** before using the main `terraform/aws` configuration:

```bash
cd terraform/bootstrap
terraform init
terraform apply
```

This creates:

- **S3 bucket** `opencontext-terraform-state` – stores Terraform state with versioning and encryption
- **DynamoDB table** `terraform-state-lock` – available for state locking (optional)

**Note:** The main `terraform/aws` configuration uses the S3 bucket for state. The bucket name must match the `bucket` in `terraform/aws/main.tf` backend block.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `aws_region` | `us-east-1` | AWS region for backend resources |
| `state_bucket_name` | `opencontext-terraform-state` | S3 bucket name (must match `terraform/aws/main.tf`) |
| `lock_table_name` | `terraform-state-lock` | DynamoDB table name |

## After Bootstrap

Deploy the main OpenContext stack:

```bash
cd ../aws
terraform init
terraform plan -var="config_file=config.yaml"
terraform apply
```

Or use the deploy script from the project root: `./scripts/deploy.sh`
