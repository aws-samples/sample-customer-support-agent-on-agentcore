#!/bin/bash
#
# XXXX Agent - AgentCore Runtime Deployment Script
#
# Usage:
#   ./scripts/deploy_agentcore.sh                    # Deploy in PUBLIC mode
#   ./scripts/deploy_agentcore.sh --vpc              # Deploy in VPC mode (fixed IP)
#   ./scripts/deploy_agentcore.sh --skip-build       # Skip Docker build
#   ./scripts/deploy_agentcore.sh --test             # Test after deployment
#   ./scripts/deploy_agentcore.sh --setup-vpc        # Setup VPC endpoints only
#
# VPC mode provides fixed outbound IP via NAT Gateway for MCP whitelist.

set -e

# =============================================================================
# Configuration
# =============================================================================

REGION="us-west-2"
ACCOUNT_ID="<ACCOUNT_ID>"

# Runtime settings
RUNTIME_NAME="chatbot_agent"
RUNTIME_ID="<RUNTIME_ID>"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/AmazonBedrockAgentCoreSDKRuntime-${REGION}-<ROLE_SUFFIX>"

# ECR settings
ECR_REPO="<ECR_REPO>"
IMAGE_TAG="latest"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"

# VPC settings (GenBI VPC)
VPC_ID="<VPC_ID>"
SUBNET_1="<SUBNET_1>"
SUBNET_2="<SUBNET_2>"
SECURITY_GROUP="<SECURITY_GROUP>"
NAT_GATEWAY_IP="<NAT_GATEWAY_IP>"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# =============================================================================
# Helper Functions
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}$1${NC}"
    echo -e "${GREEN}========================================${NC}"
}

# =============================================================================
# VPC Setup Functions
# =============================================================================

setup_security_group_rules() {
    log_step "Setting up Security Group Rules"

    # Check if egress rule exists
    EGRESS_COUNT=$(aws ec2 describe-security-groups \
        --group-ids "$SECURITY_GROUP" \
        --region "$REGION" \
        --query 'SecurityGroups[0].IpPermissionsEgress | length(@)' \
        --output text 2>/dev/null || echo "0")

    if [ "$EGRESS_COUNT" = "0" ]; then
        log_info "Adding egress rule (allow all outbound)..."
        aws ec2 authorize-security-group-egress \
            --group-id "$SECURITY_GROUP" \
            --protocol all \
            --cidr 0.0.0.0/0 \
            --region "$REGION" || true
        log_success "Egress rule added"
    else
        log_info "Egress rule already exists"
    fi

    # Check if ingress rule exists
    INGRESS_COUNT=$(aws ec2 describe-security-groups \
        --group-ids "$SECURITY_GROUP" \
        --region "$REGION" \
        --query 'SecurityGroups[0].IpPermissions | length(@)' \
        --output text 2>/dev/null || echo "0")

    if [ "$INGRESS_COUNT" = "0" ]; then
        log_info "Adding ingress rule (allow from same SG)..."
        aws ec2 authorize-security-group-ingress \
            --group-id "$SECURITY_GROUP" \
            --protocol all \
            --source-group "$SECURITY_GROUP" \
            --region "$REGION" || true
        log_success "Ingress rule added"
    else
        log_info "Ingress rule already exists"
    fi
}

create_vpc_endpoint() {
    local SERVICE=$1
    local TYPE=$2
    local SERVICE_NAME="com.amazonaws.${REGION}.${SERVICE}"

    # Check if endpoint already exists
    EXISTING=$(aws ec2 describe-vpc-endpoints \
        --filters "Name=vpc-id,Values=${VPC_ID}" "Name=service-name,Values=${SERVICE_NAME}" \
        --region "$REGION" \
        --query 'VpcEndpoints[0].VpcEndpointId' \
        --output text 2>/dev/null || echo "None")

    if [ "$EXISTING" != "None" ] && [ -n "$EXISTING" ]; then
        log_info "  $SERVICE: already exists ($EXISTING)"
        return 0
    fi

    log_info "  Creating $SERVICE endpoint..."

    if [ "$TYPE" = "Interface" ]; then
        aws ec2 create-vpc-endpoint \
            --vpc-id "$VPC_ID" \
            --service-name "$SERVICE_NAME" \
            --vpc-endpoint-type Interface \
            --subnet-ids "$SUBNET_1" "$SUBNET_2" \
            --security-group-ids "$SECURITY_GROUP" \
            --private-dns-enabled \
            --region "$REGION" \
            --query 'VpcEndpoint.VpcEndpointId' \
            --output text 2>/dev/null && log_success "    Created" || log_warning "    Failed or already exists"
    else
        # Gateway endpoint (S3)
        ROUTE_TABLE_1=$(aws ec2 describe-route-tables \
            --filters "Name=association.subnet-id,Values=${SUBNET_1}" \
            --region "$REGION" \
            --query 'RouteTables[0].RouteTableId' \
            --output text 2>/dev/null)
        ROUTE_TABLE_2=$(aws ec2 describe-route-tables \
            --filters "Name=association.subnet-id,Values=${SUBNET_2}" \
            --region "$REGION" \
            --query 'RouteTables[0].RouteTableId' \
            --output text 2>/dev/null)

        aws ec2 create-vpc-endpoint \
            --vpc-id "$VPC_ID" \
            --service-name "$SERVICE_NAME" \
            --vpc-endpoint-type Gateway \
            --route-table-ids "$ROUTE_TABLE_1" "$ROUTE_TABLE_2" \
            --region "$REGION" \
            --query 'VpcEndpoint.VpcEndpointId' \
            --output text 2>/dev/null && log_success "    Created" || log_warning "    Failed or already exists"
    fi
}

setup_vpc_endpoints() {
    log_step "Setting up VPC Endpoints"

    log_info "Creating required VPC endpoints for AgentCore Runtime..."
    echo ""

    # Required endpoints
    create_vpc_endpoint "ecr.dkr" "Interface"
    create_vpc_endpoint "ecr.api" "Interface"
    create_vpc_endpoint "s3" "Gateway"
    create_vpc_endpoint "logs" "Interface"
    create_vpc_endpoint "bedrock-runtime" "Interface"
    create_vpc_endpoint "bedrock-agentcore" "Interface"
    create_vpc_endpoint "sts" "Interface"

    log_info "Waiting for endpoints to become available..."
    sleep 30

    log_success "VPC endpoints setup complete"
}

# =============================================================================
# Docker Functions
# =============================================================================

docker_login() {
    log_step "Step 1: ECR Login"

    aws ecr get-login-password --region "$REGION" | \
        docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

    log_success "ECR login successful"
}

docker_build_push() {
    log_step "Step 2: Build & Push Docker Image"

    log_info "Building ARM64 image and pushing to ECR..."
    log_info "Image: $ECR_URI"

    docker buildx build \
        --platform linux/arm64 \
        -t "$ECR_URI" \
        --push \
        .

    log_success "Image pushed successfully"
}

# =============================================================================
# AgentCore Runtime Functions
# =============================================================================

get_runtime_status() {
    aws bedrock-agentcore-control get-agent-runtime \
        --agent-runtime-id "$RUNTIME_ID" \
        --region "$REGION" \
        --query 'status' \
        --output text 2>/dev/null || echo "NOT_FOUND"
}

get_runtime_version() {
    aws bedrock-agentcore-control get-agent-runtime \
        --agent-runtime-id "$RUNTIME_ID" \
        --region "$REGION" \
        --query 'agentRuntimeVersion' \
        --output text 2>/dev/null || echo "0"
}

update_runtime() {
    local VPC_MODE=$1

    log_step "Step 3: Update AgentCore Runtime"

    local CURRENT_STATUS=$(get_runtime_status)
    local CURRENT_VERSION=$(get_runtime_version)

    log_info "Current status: $CURRENT_STATUS"
    log_info "Current version: $CURRENT_VERSION"

    if [ "$VPC_MODE" = "true" ]; then
        log_info "Deploying in VPC mode..."
        NETWORK_CONFIG="networkMode=VPC,networkModeConfig={securityGroups=[${SECURITY_GROUP}],subnets=[${SUBNET_1},${SUBNET_2}]}"
    else
        log_info "Deploying in PUBLIC mode..."
        NETWORK_CONFIG="networkMode=PUBLIC"
    fi

    ARTIFACT_CONFIG="containerConfiguration={containerUri=${ECR_URI}}"

    NEW_VERSION=$(aws bedrock-agentcore-control update-agent-runtime \
        --agent-runtime-id "$RUNTIME_ID" \
        --region "$REGION" \
        --role-arn "$ROLE_ARN" \
        --network-configuration "$NETWORK_CONFIG" \
        --agent-runtime-artifact "$ARTIFACT_CONFIG" \
        --query 'agentRuntimeVersion' \
        --output text 2>&1)

    if [ $? -eq 0 ]; then
        log_success "Runtime updating to version: $NEW_VERSION"
    else
        log_error "Failed to update runtime: $NEW_VERSION"
        exit 1
    fi
}

wait_for_ready() {
    log_step "Step 4: Waiting for Runtime to be Ready"

    local MAX_WAIT=300
    local WAIT_INTERVAL=5
    local ELAPSED=0

    while [ $ELAPSED -lt $MAX_WAIT ]; do
        STATUS=$(get_runtime_status)
        log_info "Status: $STATUS ($ELAPSED s)"

        if [ "$STATUS" = "READY" ]; then
            log_success "Runtime is ready!"
            return 0
        fi

        if [ "$STATUS" = "FAILED" ] || [ "$STATUS" = "UPDATE_FAILED" ]; then
            log_error "Runtime failed!"
            return 1
        fi

        sleep $WAIT_INTERVAL
        ELAPSED=$((ELAPSED + WAIT_INTERVAL))
    done

    log_error "Timeout waiting for runtime to be ready"
    return 1
}

# =============================================================================
# Test Function
# =============================================================================

test_invocation() {
    log_step "Step 5: Testing Invocation"

    if command -v agentcore &> /dev/null; then
        log_info "Using agentcore CLI..."
        agentcore invoke '{"prompt": "hello", "parent_id": "deploy_test"}'
    else
        log_info "Using AWS CLI..."
        PAYLOAD=$(echo -n '{"prompt": "hello", "parent_id": "deploy_test"}' | base64)
        SESSION_ID="$(uuidgen)-$(date +%s)"

        aws bedrock-agentcore invoke-agent-runtime \
            --agent-runtime-arn "arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${RUNTIME_ID}" \
            --runtime-session-id "$SESSION_ID" \
            --payload "$PAYLOAD" \
            --region "$REGION" \
            /tmp/agentcore_test_output.json && cat /tmp/agentcore_test_output.json
    fi

    log_success "Test complete"
}

# =============================================================================
# Main
# =============================================================================

print_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --vpc           Deploy in VPC mode (fixed outbound IP)"
    echo "  --skip-build    Skip Docker build/push"
    echo "  --test          Test invocation after deployment"
    echo "  --setup-vpc     Setup VPC endpoints only (no deployment)"
    echo "  -h, --help      Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                      # Deploy in PUBLIC mode"
    echo "  $0 --vpc                # Deploy in VPC mode"
    echo "  $0 --vpc --test         # Deploy in VPC mode and test"
    echo "  $0 --skip-build --vpc   # Update to VPC mode without rebuilding"
    echo "  $0 --setup-vpc          # Setup VPC endpoints only"
}

main() {
    local VPC_MODE="false"
    local SKIP_BUILD="false"
    local RUN_TEST="false"
    local SETUP_VPC_ONLY="false"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --vpc)
                VPC_MODE="true"
                shift
                ;;
            --skip-build)
                SKIP_BUILD="true"
                shift
                ;;
            --test)
                RUN_TEST="true"
                shift
                ;;
            --setup-vpc)
                SETUP_VPC_ONLY="true"
                shift
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                print_usage
                exit 1
                ;;
        esac
    done

    # Print header
    echo ""
    echo "============================================================"
    echo "  XXXX Agent - AgentCore Runtime Deployment"
    echo "============================================================"
    echo "  Region:      $REGION"
    echo "  Runtime:     $RUNTIME_NAME"
    echo "  Image:       $ECR_URI"
    echo "  Mode:        $([ "$VPC_MODE" = "true" ] && echo "VPC" || echo "PUBLIC")"
    if [ "$VPC_MODE" = "true" ]; then
        echo "  NAT IP:      $NAT_GATEWAY_IP (for MCP whitelist)"
    fi
    echo "============================================================"

    # Setup VPC only mode
    if [ "$SETUP_VPC_ONLY" = "true" ]; then
        setup_security_group_rules
        setup_vpc_endpoints
        echo ""
        log_success "VPC setup complete!"
        echo ""
        echo "NAT Gateway IP for MCP whitelist: $NAT_GATEWAY_IP"
        exit 0
    fi

    # Setup VPC if VPC mode
    if [ "$VPC_MODE" = "true" ]; then
        setup_security_group_rules
        setup_vpc_endpoints
    fi

    # Docker build and push
    if [ "$SKIP_BUILD" = "false" ]; then
        docker_login
        docker_build_push
    else
        log_step "Step 1-2: Skipping Docker build (--skip-build)"
    fi

    # Update runtime
    update_runtime "$VPC_MODE"

    # Wait for ready
    wait_for_ready || exit 1

    # Show final status
    log_step "Deployment Complete!"

    FINAL_STATUS=$(get_runtime_status)
    FINAL_VERSION=$(get_runtime_version)
    FINAL_MODE=$(aws bedrock-agentcore-control get-agent-runtime \
        --agent-runtime-id "$RUNTIME_ID" \
        --region "$REGION" \
        --query 'networkConfiguration.networkMode' \
        --output text 2>/dev/null)

    echo ""
    echo "  Status:       $FINAL_STATUS"
    echo "  Version:      $FINAL_VERSION"
    echo "  Network Mode: $FINAL_MODE"

    if [ "$FINAL_MODE" = "VPC" ]; then
        echo ""
        echo -e "${YELLOW}  NAT Gateway IP for MCP whitelist: ${GREEN}$NAT_GATEWAY_IP${NC}"
    fi
    echo ""

    # Test if requested
    if [ "$RUN_TEST" = "true" ]; then
        test_invocation
    fi

    log_success "Done!"
}

main "$@"
