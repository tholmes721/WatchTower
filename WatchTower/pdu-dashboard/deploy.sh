#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# WatchTower — Build, push, and deploy to AWS ECS
#
# Prerequisites:
#   - AWS CLI configured (aws configure)
#   - Docker running locally
#   - Terraform applied (infrastructure exists)
#
# Usage:
#   ./deploy.sh                    # Build, push, and force new deployment
#   ./deploy.sh --build-only       # Just build and push the image
#   ./deploy.sh --deploy-only      # Just trigger a new ECS deployment
# ─────────────────────────────────────────────────────────────────────────────
set -e

# ── Configuration (auto-detected from Terraform outputs) ─────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TF_DIR="$SCRIPT_DIR/terraform"

echo ""
echo "  WatchTower — ECS Deployment"
echo "  ════════════════════════════"
echo ""

# Get values from Terraform
cd "$TF_DIR"
ECR_REPO=$(terraform output -raw ecr_repository_url 2>/dev/null)
CLUSTER=$(terraform output -raw ecs_cluster_name 2>/dev/null)
SERVICE=$(terraform output -raw ecs_service_name 2>/dev/null)
REGION=$(terraform output -raw 2>/dev/null | grep -m1 "" || echo "us-east-1")

if [ -z "$ECR_REPO" ]; then
    echo "  ERROR: Could not read Terraform outputs."
    echo "  Make sure you've run 'terraform apply' first."
    exit 1
fi

# Extract region and account from ECR URL
AWS_ACCOUNT_ID=$(echo "$ECR_REPO" | cut -d'.' -f1)
AWS_REGION=$(echo "$ECR_REPO" | cut -d'.' -f4)

echo "  ECR Repo:  $ECR_REPO"
echo "  Cluster:   $CLUSTER"
echo "  Service:   $SERVICE"
echo "  Region:    $AWS_REGION"
echo ""

cd "$SCRIPT_DIR"

# ── Build & Push ─────────────────────────────────────────────────────────────
if [ "$1" != "--deploy-only" ]; then
    echo "  [1/3] Logging in to ECR..."
    aws ecr get-login-password --region "$AWS_REGION" | \
        docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

    echo ""
    echo "  [2/3] Building Docker image..."
    docker build -t "$ECR_REPO:latest" .

    echo ""
    echo "  [3/3] Pushing to ECR..."
    docker push "$ECR_REPO:latest"

    echo ""
    echo "  Image pushed successfully."
fi

# ── Deploy ───────────────────────────────────────────────────────────────────
if [ "$1" != "--build-only" ]; then
    echo ""
    echo "  Forcing new ECS deployment..."
    aws ecs update-service \
        --cluster "$CLUSTER" \
        --service "$SERVICE" \
        --force-new-deployment \
        --region "$AWS_REGION" \
        > /dev/null

    echo ""
    echo "  ════════════════════════════════════════════════════════"
    echo "  Deployment initiated!"
    echo ""
    echo "  ECS will pull the new image and replace the running task."
    echo "  This typically takes 1-2 minutes."
    echo ""
    echo "  Monitor progress:"
    echo "    aws ecs describe-services --cluster $CLUSTER --services $SERVICE --region $AWS_REGION"
    echo ""
    ALB_URL=$(cd "$TF_DIR" && terraform output -raw alb_url 2>/dev/null)
    if [ -n "$ALB_URL" ]; then
        echo "  Access WatchTower at:"
        echo "    $ALB_URL"
    fi
    echo "  ════════════════════════════════════════════════════════"
fi

echo ""
