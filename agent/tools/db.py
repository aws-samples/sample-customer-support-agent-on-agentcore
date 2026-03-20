"""Shared DynamoDB client for tool implementations.

Provides lazy-initialized boto3 DynamoDB resource, cached for reuse.
Table names are derived from the DYNAMODB_TABLE_PREFIX env var (default: 'xxxx-demo').
"""

import os
import logging

import boto3

logger = logging.getLogger(__name__)

_dynamodb = None


def _get_dynamodb():
    """Lazy-init boto3 DynamoDB resource."""
    global _dynamodb
    if _dynamodb is None:
        region = os.environ.get("AWS_REGION", "us-west-2")
        _dynamodb = boto3.resource("dynamodb", region_name=region)
        logger.info(f"DynamoDB resource initialized (region={region})")
    return _dynamodb


def get_table(suffix: str):
    """Get a DynamoDB Table object by suffix.

    The full table name is '{DYNAMODB_TABLE_PREFIX}-{suffix}'.

    Args:
        suffix: e.g. 'bookings', 'accounts', 'slots'
    """
    prefix = os.environ.get("DYNAMODB_TABLE_PREFIX", "xxxx-demo")
    table_name = f"{prefix}-{suffix}"
    return _get_dynamodb().Table(table_name)
