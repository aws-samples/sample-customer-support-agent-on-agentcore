"""AgentCore Runtime module for XXXX Agent

This module provides the runtime entry point for deploying XXXX Agent
to AWS Bedrock AgentCore Runtime.

Usage:
    # Local development
    python -m agent.runtime

    # AgentCore deployment
    python scripts/deploy_runtime.py --gateway-id <gateway-id>
"""

from .entrypoint import app, process_request

__all__ = ["app", "process_request"]
