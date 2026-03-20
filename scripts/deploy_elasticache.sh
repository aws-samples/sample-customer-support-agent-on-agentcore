#!/bin/bash
#
# XXXX Agent - AWS ElastiCache Redis Deployment Script
#
# Creates an ElastiCache Redis OSS replication group (single-node, TLS enabled)
# in the same VPC as AgentCore Runtime for the Session Dispatcher.
#
# Uses a Replication Group (not standalone cache cluster) because TLS
# (transit encryption) requires a replication group in ElastiCache.
#
# Usage:
#   ./scripts/deploy_elasticache.sh              # Create ElastiCache
#   ./scripts/deploy_elasticache.sh --status      # Check status
#   ./scripts/deploy_elasticache.sh --delete      # Delete ElastiCache
#   ./scripts/deploy_elasticache.sh --endpoint    # Print endpoint URL
#   ./scripts/deploy_elasticache.sh --wait        # Wait until available
#
# Prerequisites:
#   - AWS CLI configured with appropriate permissions
#   - VPC (<VPC_ID>) already exists with private subnets
#

set -e

# =============================================================================
# Configuration
# =============================================================================

REGION="us-west-2"
ACCOUNT_ID="<ACCOUNT_ID>"

# VPC settings (same as AgentCore Runtime)
VPC_ID="<VPC_ID>"
SUBNET_1="<SUBNET_1>"   # us-west-2a
SUBNET_2="<SUBNET_2>"   # us-west-2b
SECURITY_GROUP="<SECURITY_GROUP>"

# ElastiCache settings
REPLICATION_GROUP_ID="xxxx-dispatcher"
SUBNET_GROUP_NAME="xxxx-dispatcher-subnets"
NODE_TYPE="cache.t3.micro"            # Smallest instance, sufficient for testing
ENGINE_VERSION="7.1"                  # Redis 7.1
PORT=6379

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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
# Create Subnet Group
# =============================================================================

create_subnet_group() {
    log_step "Step 1: Create ElastiCache Subnet Group"

    # Check if subnet group already exists
    EXISTING=$(aws elasticache describe-cache-subnet-groups \
        --cache-subnet-group-name "$SUBNET_GROUP_NAME" \
        --region "$REGION" \
        --query 'CacheSubnetGroups[0].CacheSubnetGroupName' \
        --output text 2>/dev/null || echo "None")

    if [ "$EXISTING" != "None" ]; then
        log_info "Subnet group '$SUBNET_GROUP_NAME' already exists, skipping."
        return 0
    fi

    log_info "Creating subnet group: $SUBNET_GROUP_NAME"
    aws elasticache create-cache-subnet-group \
        --cache-subnet-group-name "$SUBNET_GROUP_NAME" \
        --cache-subnet-group-description "Subnets for XXXX Dispatcher ElastiCache" \
        --subnet-ids "$SUBNET_1" "$SUBNET_2" \
        --region "$REGION"

    log_success "Subnet group created: $SUBNET_GROUP_NAME"
}

# =============================================================================
# Create ElastiCache Redis Replication Group (with TLS)
# =============================================================================

create_replication_group() {
    log_step "Step 2: Create ElastiCache Redis Replication Group"

    # Check if replication group already exists
    EXISTING_STATUS=$(aws elasticache describe-replication-groups \
        --replication-group-id "$REPLICATION_GROUP_ID" \
        --region "$REGION" \
        --query 'ReplicationGroups[0].Status' \
        --output text 2>/dev/null || echo "not-found")

    if [ "$EXISTING_STATUS" != "not-found" ]; then
        log_info "Replication group '$REPLICATION_GROUP_ID' already exists (status: $EXISTING_STATUS)"
        if [ "$EXISTING_STATUS" = "available" ]; then
            log_success "Replication group is ready!"
        else
            log_warning "Replication group is in state: $EXISTING_STATUS. Wait for it to become 'available'."
        fi
        return 0
    fi

    log_info "Creating ElastiCache Redis replication group..."
    log_info "  Replication Group: $REPLICATION_GROUP_ID"
    log_info "  Node Type:         $NODE_TYPE"
    log_info "  Engine:            redis $ENGINE_VERSION"
    log_info "  VPC:               $VPC_ID"
    log_info "  Subnets:           $SUBNET_1, $SUBNET_2"
    log_info "  Security Group:    $SECURITY_GROUP"
    log_info "  TLS:               enabled"

    # Using replication group with single node (no replicas) to enable TLS.
    # --transit-encryption-enabled is only supported with replication groups.
    aws elasticache create-replication-group \
        --replication-group-id "$REPLICATION_GROUP_ID" \
        --replication-group-description "XXXX Dispatcher Redis (TLS enabled)" \
        --engine redis \
        --engine-version "$ENGINE_VERSION" \
        --cache-node-type "$NODE_TYPE" \
        --num-cache-clusters 1 \
        --cache-subnet-group-name "$SUBNET_GROUP_NAME" \
        --security-group-ids "$SECURITY_GROUP" \
        --port "$PORT" \
        --transit-encryption-enabled \
        --no-automatic-failover-enabled \
        --region "$REGION" \
        --tags Key=Project,Value=XXXX Key=Component,Value=Dispatcher

    log_success "Replication group creation initiated!"
    log_warning "ElastiCache provisioning takes 5-10 minutes."
    log_info "Run './scripts/deploy_elasticache.sh --status' to check progress."
}

# =============================================================================
# Check Status
# =============================================================================

check_status() {
    log_step "ElastiCache Replication Group Status"

    STATUS=$(aws elasticache describe-replication-groups \
        --replication-group-id "$REPLICATION_GROUP_ID" \
        --region "$REGION" \
        --output json 2>/dev/null || echo "{}")

    if [ "$STATUS" = "{}" ]; then
        log_error "Replication group '$REPLICATION_GROUP_ID' not found."
        return 1
    fi

    echo "$STATUS" | python3 -c "
import json, sys
data = json.load(sys.stdin)
rg = data['ReplicationGroups'][0]
print(f\"  Replication Group: {rg['ReplicationGroupId']}\")
print(f\"  Status:            {rg['Status']}\")
print(f\"  Description:       {rg.get('Description', 'N/A')}\")
print(f\"  TLS:               {rg.get('TransitEncryptionEnabled', False)}\")

# Primary endpoint
pe = rg.get('NodeGroups', [{}])[0].get('PrimaryEndpoint', {})
if pe:
    print(f\"  Primary Endpoint:  {pe.get('Address', 'N/A')}:{pe.get('Port', 'N/A')}\")

# Member clusters
members = rg.get('MemberClusters', [])
if members:
    print(f\"  Member Clusters:   {', '.join(members)}\")
"
}

# =============================================================================
# Get Endpoint
# =============================================================================

get_endpoint() {
    ENDPOINT_INFO=$(aws elasticache describe-replication-groups \
        --replication-group-id "$REPLICATION_GROUP_ID" \
        --region "$REGION" \
        --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint' \
        --output json 2>/dev/null || echo "null")

    if [ "$ENDPOINT_INFO" = "null" ] || [ -z "$ENDPOINT_INFO" ]; then
        log_error "Replication group not ready or not found. Run --status to check."
        return 1
    fi

    ADDRESS=$(echo "$ENDPOINT_INFO" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['Address'])")
    PORT_NUM=$(echo "$ENDPOINT_INFO" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['Port'])")

    # TLS enabled → use rediss://
    REDIS_URL="rediss://${ADDRESS}:${PORT_NUM}"

    echo ""
    log_success "ElastiCache Redis Endpoint:"
    echo ""
    echo "  Address:   $ADDRESS"
    echo "  Port:      $PORT_NUM"
    echo "  Redis URL: $REDIS_URL"
    echo ""
    echo "  To use in tests:"
    echo "    export REDIS_URL=\"$REDIS_URL\""
    echo ""
    echo "  To add to .env:"
    echo "    echo 'REDIS_URL=$REDIS_URL' >> .env"
    echo ""
    echo "  To set in Dockerfile/AgentCore:"
    echo "    REDIS_URL=$REDIS_URL"
    echo ""
}

# =============================================================================
# Delete Replication Group
# =============================================================================

delete_cluster() {
    log_step "Deleting ElastiCache Resources"

    log_warning "This will delete the replication group: $REPLICATION_GROUP_ID"
    read -p "Are you sure? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Cancelled."
        return 0
    fi

    # Delete replication group
    log_info "Deleting replication group: $REPLICATION_GROUP_ID"
    aws elasticache delete-replication-group \
        --replication-group-id "$REPLICATION_GROUP_ID" \
        --no-retain-primary-cluster \
        --region "$REGION" 2>/dev/null || true

    log_info "Waiting for replication group deletion (this may take several minutes)..."
    # Poll until deleted
    MAX_WAIT=120
    ELAPSED=0
    while [ $ELAPSED -lt $MAX_WAIT ]; do
        RG_STATUS=$(aws elasticache describe-replication-groups \
            --replication-group-id "$REPLICATION_GROUP_ID" \
            --region "$REGION" \
            --query 'ReplicationGroups[0].Status' \
            --output text 2>/dev/null || echo "deleted")
        if [ "$RG_STATUS" = "deleted" ]; then
            break
        fi
        printf "\r  Status: %-20s (%ds)" "$RG_STATUS" "$ELAPSED"
        sleep 10
        ELAPSED=$((ELAPSED + 10))
    done
    echo ""
    log_success "Replication group deleted."

    # Delete subnet group
    sleep 5  # Brief pause to ensure all resources are cleaned up
    log_info "Deleting subnet group: $SUBNET_GROUP_NAME"
    aws elasticache delete-cache-subnet-group \
        --cache-subnet-group-name "$SUBNET_GROUP_NAME" \
        --region "$REGION" 2>/dev/null || true

    log_success "ElastiCache resources deleted."
}

# =============================================================================
# Wait for replication group to become available
# =============================================================================

wait_for_available() {
    log_step "Waiting for Replication Group to Become Available"

    log_info "This may take 5-10 minutes..."

    MAX_ATTEMPTS=60  # 60 * 10s = 600s = 10 min
    ATTEMPT=0

    while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
        ATTEMPT=$((ATTEMPT + 1))
        STATUS=$(aws elasticache describe-replication-groups \
            --replication-group-id "$REPLICATION_GROUP_ID" \
            --region "$REGION" \
            --query 'ReplicationGroups[0].Status' \
            --output text 2>/dev/null || echo "not-found")

        if [ "$STATUS" = "available" ]; then
            echo ""
            log_success "Replication group is now available!"
            return 0
        elif [ "$STATUS" = "not-found" ]; then
            log_error "Replication group not found."
            return 1
        fi

        printf "\r  Status: %-20s (attempt %d/%d)" "$STATUS" "$ATTEMPT" "$MAX_ATTEMPTS"
        sleep 10
    done

    echo ""
    log_error "Timeout waiting for replication group to become available."
    return 1
}

# =============================================================================
# Main
# =============================================================================

main() {
    echo ""
    echo "=========================================="
    echo "  XXXX ElastiCache Redis Deployment"
    echo "=========================================="
    echo ""

    case "${1:-create}" in
        --status|-s)
            check_status
            ;;
        --endpoint|-e)
            get_endpoint
            ;;
        --delete|-d)
            delete_cluster
            ;;
        --wait|-w)
            wait_for_available
            get_endpoint
            ;;
        create|--create|-c)
            create_subnet_group
            create_replication_group
            echo ""
            log_info "Next steps:"
            echo "  1. Wait for cluster: ./scripts/deploy_elasticache.sh --wait"
            echo "  2. Get endpoint:     ./scripts/deploy_elasticache.sh --endpoint"
            echo "  3. Run tests:        REDIS_URL=<endpoint> uv run pytest tests/test_dispatcher_integration.py -v -s"
            ;;
        *)
            echo "Usage: $0 [create|--status|--endpoint|--delete|--wait]"
            echo ""
            echo "Commands:"
            echo "  create (default)   Create ElastiCache Redis replication group"
            echo "  --status, -s       Check replication group status"
            echo "  --endpoint, -e     Print Redis endpoint URL"
            echo "  --delete, -d       Delete replication group and subnet group"
            echo "  --wait, -w         Wait for available, then print endpoint"
            exit 1
            ;;
    esac
}

main "$@"
