#!/bin/bash
# S3 Backend Setup Script
# Creates backend.tf for Terraform remote state

echo "Setting up S3 backend for Terraform..."

# Get AWS account ID and region
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
AWS_REGION=$(aws configure get region 2>/dev/null || echo "us-east-1")

if [ -z "$AWS_ACCOUNT_ID" ]; then
    echo "❌ Error: Could not get AWS account ID. Make sure AWS CLI is configured."
    exit 1
fi

# Generate bucket name
BUCKET_NAME="boston-opencontext-opendataterraform-state-${AWS_ACCOUNT_ID}-${AWS_REGION}"
TABLE_NAME="terraform-state-lock"

echo "AWS Account ID: $AWS_ACCOUNT_ID"
echo "AWS Region: $AWS_REGION"
echo "S3 Bucket: $BUCKET_NAME"
echo "DynamoDB Table: $TABLE_NAME"
echo ""

# Create S3 bucket if it doesn't exist
echo "Creating S3 bucket..."
if ! aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
    if [ "$AWS_REGION" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION"
    else
        aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION" --create-bucket-configuration LocationConstraint="$AWS_REGION"
    fi
    aws s3api put-bucket-versioning --bucket "$BUCKET_NAME" --versioning-configuration Status=Enabled
    aws s3api put-bucket-encryption --bucket "$BUCKET_NAME" --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
    echo "✅ S3 bucket created"
else
    echo "✅ S3 bucket already exists"
fi

# Create DynamoDB table if it doesn't exist
echo "Creating DynamoDB table..."
if ! aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$AWS_REGION" 2>/dev/null; then
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$AWS_REGION"
    echo "⏳ Waiting for table to be active..."
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$AWS_REGION"
    echo "✅ DynamoDB table created"
else
    echo "✅ DynamoDB table already exists"
fi

# Create backend.tf
BACKEND_TF_PATH="terraform/aws/backend.tf"
cat > "$BACKEND_TF_PATH" <<EOF
terraform {
  backend "s3" {
    bucket         = "$BUCKET_NAME"
    key            = "terraform.tfstate"
    region         = "$AWS_REGION"
    dynamodb_table = "$TABLE_NAME"
    encrypt        = true
  }
}
EOF

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Setup complete! Your backend.tf has been created."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Run the following commands to migrate your state to S3:"
echo ""
echo "  cd terraform/aws"
echo "  terraform init -migrate-state"
echo ""
echo "When prompted, type 'yes' to confirm migration"
echo ""
