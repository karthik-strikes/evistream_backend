# AWS Setup Guide

## Prerequisites

- AWS CLI installed and configured with admin credentials
- `jq` installed (optional, for JSON formatting)
- Account ID available: `aws sts get-caller-identity --query Account --output text`

## Step 1: Create the S3 Bucket

```bash
# Create bucket (us-east-1 does not use CreateBucketConfiguration)
aws s3api create-bucket \
  --bucket evistream-production \
  --region us-east-1

# Block all public access
aws s3api put-public-access-block \
  --bucket evistream-production \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# Enable versioning (protects against accidental deletes)
aws s3api put-bucket-versioning \
  --bucket evistream-production \
  --versioning-configuration Status=Enabled

# Enable server-side encryption (AES-256)
aws s3api put-bucket-encryption \
  --bucket evistream-production \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'

# Lifecycle rule: abort incomplete multipart uploads after 7 days
aws s3api put-bucket-lifecycle-configuration \
  --bucket evistream-production \
  --lifecycle-configuration '{
    "Rules": [{
      "ID": "abort-incomplete-multipart",
      "Status": "Enabled",
      "Filter": {"Prefix": ""},
      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}
    }]
  }'
```

## Step 2: Create IAM Policy

Save the following as `evistream-s3-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListBucket",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::evistream-production"
    },
    {
      "Sid": "ReadWriteObjects",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::evistream-production/*"
    }
  ]
}
```

```bash
aws iam create-policy \
  --policy-name evistream-s3-policy \
  --policy-document file://evistream-s3-policy.json
```

Note your account ID:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo $ACCOUNT_ID
```

## Step 3: IAM Setup

### Option A: EC2 Instance Role (Recommended for Production)

Use this if the app runs on EC2. No long-lived credentials needed.

```bash
# Create the role
aws iam create-role \
  --role-name evistream-app-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

# Attach the S3 policy
aws iam attach-role-policy \
  --role-name evistream-app-role \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/evistream-s3-policy

# Create instance profile
aws iam create-instance-profile \
  --instance-profile-name evistream-app-profile

# Add role to instance profile
aws iam add-role-to-instance-profile \
  --instance-profile-name evistream-app-profile \
  --role-name evistream-app-role
```

To attach to a running EC2 instance (via console):

1. EC2 → Instances → Select instance
2. Actions → Security → Modify IAM Role
3. Select `evistream-app-profile` → Save

With an instance role, **do not set** `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` in `.env`. The SDK uses the instance metadata service automatically.

### Option B: IAM User (Development / Non-EC2)

Use this for local development or non-EC2 deployments.

```bash
# Create user
aws iam create-user --user-name evistream-app

# Attach policy
aws iam attach-user-policy \
  --user-name evistream-app \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/evistream-s3-policy

# Create access key — save the output securely
aws iam create-access-key --user-name evistream-app
```

Save the `AccessKeyId` and `SecretAccessKey` from the output.

## Step 4: Configure Environment Variables

Add to your `.env` file (in `backend/`):

```env
# Required for all deployments
AWS_REGION=us-east-1
S3_BUCKET=evistream-production

# Only needed when using IAM User (Option B above)
# Do NOT set these when using EC2 instance role (Option A)
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
```

## Step 5: Verify Setup

```bash
# Test bucket access
aws s3 ls s3://evistream-production/

# Test object upload
echo "test" | aws s3 cp - s3://evistream-production/test.txt

# Test object download
aws s3 cp s3://evistream-production/test.txt -

# Clean up test object
aws s3 rm s3://evistream-production/test.txt
```

## Bucket Structure Reference

```
evistream-production/
├── pdfs/
│   └── {project_id}/
│       └── {sha256}.pdf
└── markdown/
    └── {project_id}/
        └── {sha256}.md
```
