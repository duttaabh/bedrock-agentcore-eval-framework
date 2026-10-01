#!/usr/bin/env bash
# Build the runtime image (ARM64, required by AgentCore Runtime) and push it
# to the ECR repo Terraform created. Run `terraform apply` in tf/ first.
#
# Usage: scripts/build_and_push.sh [image_tag]

set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE_TAG="${1:-latest}"
TF_DIR="tf"

ECR_URL=$(terraform -chdir="$TF_DIR" output -raw ecr_repository_url)
REGION=$(terraform -chdir="$TF_DIR" output -raw aws_region)
ACCOUNT_ID=$(terraform -chdir="$TF_DIR" output -raw aws_account_id)

echo "Logging into ECR ($ECR_URL)..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "Building ${ECR_URL}:${IMAGE_TAG} (linux/arm64)..."
docker buildx build \
  --platform linux/arm64 \
  -t "${ECR_URL}:${IMAGE_TAG}" \
  --push \
  runtime/

echo "Pushed ${ECR_URL}:${IMAGE_TAG}"
echo
echo "If this is a NEW image tag (not re-pushing 'latest' in place), re-apply"
echo "Terraform with -var image_tag=${IMAGE_TAG}, or activate it directly:"
echo "  aws bedrock-agentcore update-agent-runtime --region ${REGION} \\"
echo "    --agent-runtime-id \$(terraform -chdir=${TF_DIR} output -raw agent_runtime_id) \\"
echo "    --agent-runtime-artifact '{\"containerConfiguration\":{\"containerUri\":\"'${ECR_URL}:${IMAGE_TAG}'\"}}'"
