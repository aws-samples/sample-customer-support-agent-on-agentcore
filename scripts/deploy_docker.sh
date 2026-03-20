#!/bin/bash
# XXXX Agent - Docker-based AgentCore Runtime Deployment
#
# This script deploys using Docker container to ensure .claude/skills is included.
#
# Usage:
#   ./scripts/deploy_docker.sh                    # Full deploy (build + push + create)
#   ./scripts/deploy_docker.sh --skip-build       # Skip Docker build, use existing image
#   ./scripts/deploy_docker.sh --role-arn <arn>   # Specify IAM role ARN

set -e

# Configuration
# Agent runtime name: only letters, numbers, underscores (no hyphens)
AGENT_NAME="${AGENT_NAME:-chatbot_agent}"
# ECR repo name: can have hyphens
ECR_REPO_NAME="${ECR_REPO_NAME:-<ECR_REPO>}"
REGION="${AWS_REGION:-us-west-2}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO_NAME}"
IMAGE_TAG="latest"
IMAGE_URI="${ECR_REPO}:${IMAGE_TAG}"

echo "=============================================="
echo "XXXX AgentCore Runtime Deployment (Docker)"
echo "=============================================="
echo "Agent Name: $AGENT_NAME"
echo "Region: $REGION"
echo "Account ID: $ACCOUNT_ID"
echo "Image URI: $IMAGE_URI"
echo "=============================================="

# Parse arguments
SKIP_BUILD=false
ROLE_ARN=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-build)
            SKIP_BUILD=true
            shift
            ;;
        --role-arn)
            ROLE_ARN="$2"
            shift 2
            ;;
        --region|-r)
            REGION="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Step 1: Ensure ECR repository exists
echo ""
echo "[Step 1] Ensuring ECR repository..."
aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" --region "$REGION" 2>/dev/null || \
    aws ecr create-repository --repository-name "$ECR_REPO_NAME" --region "$REGION" \
        --image-scanning-configuration scanOnPush=true

# Step 2: Login to ECR
echo ""
echo "[Step 2] Logging into ECR..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Step 3: Build and push Docker image (if not skipped)
if [ "$SKIP_BUILD" = false ]; then
    echo ""
    echo "[Step 3] Building and pushing ARM64 Docker image..."
    docker buildx build --platform linux/arm64 -t "$IMAGE_URI" --push .
else
    echo ""
    echo "[Step 3] Skipping build, using existing image: $IMAGE_URI"
fi

# Step 4: Create or update AgentCore Runtime
echo ""
echo "[Step 4] Creating/Updating AgentCore Runtime..."

# Check if runtime exists
EXISTING_RUNTIME=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
    --query "agentRuntimeSummaries[?agentRuntimeName=='${AGENT_NAME}'].agentRuntimeArn" --output text 2>/dev/null || echo "")

if [ -n "$EXISTING_RUNTIME" ] && [ "$EXISTING_RUNTIME" != "None" ]; then
    echo "  Runtime exists, updating..."
    RUNTIME_ARN="$EXISTING_RUNTIME"

    aws bedrock-agentcore-control update-agent-runtime \
        --agent-runtime-arn "$RUNTIME_ARN" \
        --agent-runtime-artifact "{\"containerConfiguration\": {\"containerUri\": \"${IMAGE_URI}\"}}" \
        --region "$REGION"
else
    echo "  Creating new runtime..."

    # Build create command
    CREATE_CMD="aws bedrock-agentcore-control create-agent-runtime \
        --agent-runtime-name $AGENT_NAME \
        --agent-runtime-artifact '{\"containerConfiguration\": {\"containerUri\": \"${IMAGE_URI}\"}}' \
        --network-configuration '{\"networkMode\": \"PUBLIC\"}' \
        --protocol-configuration '{\"serverProtocol\": \"HTTP\"}' \
        --region $REGION"

    # Add role ARN if provided
    if [ -n "$ROLE_ARN" ]; then
        CREATE_CMD="$CREATE_CMD --role-arn $ROLE_ARN"
    fi

    # Execute
    RESPONSE=$(eval "$CREATE_CMD")
    RUNTIME_ARN=$(echo "$RESPONSE" | jq -r '.agentRuntimeArn')
fi

echo "  Runtime ARN: $RUNTIME_ARN"

# Step 5: Wait for runtime to be ready
echo ""
echo "[Step 5] Waiting for runtime to be ready..."
while true; do
    STATUS=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
        --query "agentRuntimeSummaries[?agentRuntimeName=='${AGENT_NAME}'].status" --output text)

    echo "  Status: $STATUS"

    if [ "$STATUS" = "READY" ]; then
        break
    elif [ "$STATUS" = "CREATE_FAILED" ] || [ "$STATUS" = "UPDATE_FAILED" ]; then
        echo "  Deployment failed!"
        exit 1
    fi

    sleep 10
done

# Step 6: Save ARN and show test command
echo ""
echo "=============================================="
echo "Deployment Complete!"
echo "=============================================="
echo "Runtime ARN: $RUNTIME_ARN"
echo "$RUNTIME_ARN" > .agent_arn
echo ""
echo "To test the agent:"
echo "  aws bedrock-agentcore invoke-agent-runtime \\"
echo "    --agent-runtime-arn $RUNTIME_ARN \\"
echo "    --runtime-session-id \$(uuidgen) \\"
echo "    --payload '{\"prompt\": \"你好\", \"parent_id\": \"test_user\"}' \\"
echo "    --region $REGION \\"
echo "    output.json && cat output.json"
echo ""
echo "Or use agentcore CLI:"
echo "  agentcore invoke '{\"prompt\": \"你好\"}'"
echo "=============================================="
