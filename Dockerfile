# Customer Service Agent - AgentCore Runtime Docker Image
#
# Build for ARM64 (required by AgentCore Runtime):
#   docker buildx build --platform linux/arm64 -t agent .
#
# Build and push to ECR:
#   docker buildx build --platform linux/arm64 \
#     -t <account>.dkr.ecr.<region>.amazonaws.com/<ECR_REPO>:latest \
#     --push .
#
# Run locally (for testing on ARM64 Mac or with emulation):
#   docker run -p 8080:8080 \
#     -e AWS_ACCESS_KEY_ID=xxx \
#     -e AWS_SECRET_ACCESS_KEY=xxx \
#     -e AWS_REGION=us-west-2 \
#     -e MEMORY_ID=xxx \
#     agent

# AgentCore Runtime requires ARM64 architecture
FROM --platform=linux/arm64 python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Download Bedrock cache proxy (Go static binary, ~10MB)
# Provides 1-hour prompt caching for all Claude API calls
ARG PROXY_VERSION=latest
RUN curl -Lo /usr/local/bin/bedrock-effort-proxy \
    "https://github.com/KevinZhao/claudecode-bedrock-proxy/releases/${PROXY_VERSION}/download/bedrock-effort-proxy-linux-arm64" \
    && chmod +x /usr/local/bin/bedrock-effort-proxy

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user (required: claude_agent_sdk doesn't allow bypassPermissions as root)
RUN useradd -m -u 1000 appuser

# Copy application code
COPY agent/ agent/
COPY .claude/ .claude/

# Copy startup script
COPY scripts/start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Change ownership to appuser
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV CLAUDE_CODE_USE_BEDROCK=1
ENV AWS_REGION=us-west-2

# Agent configuration
ENV MINIMAL_MODE=false
ENV USE_SKILLS=true
ENV DYNAMODB_TABLE_PREFIX=xxxx-demo
ENV MEMORY_ID=<MEMORY_ID>
ENV KNOWLEDGE_BASE_ID=<KNOWLEDGE_BASE_ID>

# OpenTelemetry: manual OTEL SDK (no ADOT auto-instrumentation)
ENV AGENTCORE_RUNTIME_ID=<RUNTIME_ID>

# Bedrock cache proxy: route Claude Code CLI calls through local proxy
# The proxy injects cache_control markers with 1h TTL into all Bedrock API calls
ENV ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:8888
ENV CLAUDE_CODE_SKIP_BEDROCK_AUTH=1
ENV CACHE_ENABLED=1
ENV CACHE_TTL=1h

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ping || exit 1

# Start proxy sidecar + agent
CMD ["/app/start.sh"]
