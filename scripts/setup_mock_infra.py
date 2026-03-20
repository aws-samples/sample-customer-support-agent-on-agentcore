#!/usr/bin/env python3
"""
Setup Mock Infrastructure for XXXX Agent Demo.

Creates DynamoDB tables (with seed data) and Bedrock Knowledge Base
so that the local MCP tools have real AWS backends.

Usage:
    python scripts/setup_mock_infra.py
    python scripts/setup_mock_infra.py --region us-west-2
    python scripts/setup_mock_infra.py --skip-kb        # DynamoDB only
    python scripts/setup_mock_infra.py --skip-dynamodb   # KB only

Prerequisites:
    - AWS credentials configured
    - boto3 installed
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import boto3

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TABLE_PREFIX = "xxxx-demo"
KB_NAME = "xxxx-demo-kb"
KB_DESCRIPTION = "XXXX Demo Knowledge Base - FAQ, course info, and service support"
EMBEDDING_MODEL = "cohere.embed-multilingual-v3"
S3_PREFIX = "knowledge-base"
DOCS_DIR = Path(__file__).parent.parent / "docs"

# Only upload these KB-specific docs (not other docs/ like design docs)
KB_DOC_FILES = [
    "服务FAQ与操作指南.md",
    "课程与品牌介绍.md",
]


# ---------------------------------------------------------------------------
# DynamoDB Setup
# ---------------------------------------------------------------------------

def create_table_if_not_exists(dynamodb_client, table_name: str, key_schema: list, attr_defs: list):
    """Create a DynamoDB table if it doesn't already exist."""
    existing = dynamodb_client.list_tables()["TableNames"]
    if table_name in existing:
        print(f"  Table already exists: {table_name}")
        return

    print(f"  Creating table: {table_name}")
    dynamodb_client.create_table(
        TableName=table_name,
        KeySchema=key_schema,
        AttributeDefinitions=attr_defs,
        BillingMode="PAY_PER_REQUEST",
    )

    # Wait for table to become active
    waiter = dynamodb_client.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    print(f"  Table ready: {table_name}")


def setup_dynamodb(region: str) -> str:
    """Create DynamoDB tables and seed data. Returns table prefix."""
    print("\n=== Setting up DynamoDB tables ===")
    client = boto3.client("dynamodb", region_name=region)
    dynamodb = boto3.resource("dynamodb", region_name=region)

    # --- Table 1: bookings ---
    bookings_table_name = f"{TABLE_PREFIX}-bookings"
    create_table_if_not_exists(
        client,
        bookings_table_name,
        key_schema=[
            {"AttributeName": "parent_id", "KeyType": "HASH"},
            {"AttributeName": "booking_id", "KeyType": "RANGE"},
        ],
        attr_defs=[
            {"AttributeName": "parent_id", "AttributeType": "S"},
            {"AttributeName": "booking_id", "AttributeType": "S"},
        ],
    )

    # --- Table 2: accounts ---
    accounts_table_name = f"{TABLE_PREFIX}-accounts"
    create_table_if_not_exists(
        client,
        accounts_table_name,
        key_schema=[
            {"AttributeName": "parent_id", "KeyType": "HASH"},
        ],
        attr_defs=[
            {"AttributeName": "parent_id", "AttributeType": "S"},
        ],
    )

    # --- Table 3: available slots ---
    slots_table_name = f"{TABLE_PREFIX}-slots"
    create_table_if_not_exists(
        client,
        slots_table_name,
        key_schema=[
            {"AttributeName": "tutor_id", "KeyType": "HASH"},
            {"AttributeName": "date", "KeyType": "RANGE"},
        ],
        attr_defs=[
            {"AttributeName": "tutor_id", "AttributeType": "S"},
            {"AttributeName": "date", "AttributeType": "S"},
        ],
    )

    # --- Seed data ---
    print("\n  Seeding data...")

    now = datetime.now()

    # Bookings
    bookings_table = dynamodb.Table(bookings_table_name)
    bookings = [
        {
            "parent_id": "parent_001",
            "booking_id": "BK001",
            "student_id": "STU001",
            "student_name": "小明",
            "course_name": "中文标准版",
            "tutor_id": "TUT001",
            "tutor_name": "王老师",
            "start_time": (now + timedelta(days=1, hours=10)).strftime("%Y-%m-%dT%H:%M:%S"),
            "end_time": (now + timedelta(days=1, hours=11)).strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "confirmed",
        },
        {
            "parent_id": "parent_001",
            "booking_id": "BK002",
            "student_id": "STU001",
            "student_name": "小明",
            "course_name": "中文标准版",
            "tutor_id": "TUT001",
            "tutor_name": "王老师",
            "start_time": (now + timedelta(days=3, hours=14)).strftime("%Y-%m-%dT%H:%M:%S"),
            "end_time": (now + timedelta(days=3, hours=15)).strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "confirmed",
        },
        {
            "parent_id": "parent_001",
            "booking_id": "BK003",
            "student_id": "STU002",
            "student_name": "小红",
            "course_name": "新加坡数学",
            "tutor_id": "TUT002",
            "tutor_name": "李老师",
            "start_time": (now + timedelta(days=2, hours=16)).strftime("%Y-%m-%dT%H:%M:%S"),
            "end_time": (now + timedelta(days=2, hours=17)).strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "confirmed",
        },
    ]

    with bookings_table.batch_writer() as batch:
        for b in bookings:
            batch.put_item(Item=b)
    print(f"  Seeded {len(bookings)} bookings into {bookings_table_name}")

    # Accounts
    accounts_table = dynamodb.Table(accounts_table_name)
    accounts_table.put_item(Item={
        "parent_id": "parent_001",
        "parent_name": "张先生",
        "students": [
            {"student_id": "STU001", "student_name": "小明"},
            {"student_id": "STU002", "student_name": "小红"},
        ],
        "lesson_balance": {
            "中文标准版": {"balance": 24, "unit": "课时", "expire_date": "2025-12-31"},
            "新加坡数学": {"balance": 12, "unit": "课时", "expire_date": "2025-10-15"},
        },
        "checkin_count": {"中文": 3, "数学": 2},
        "points_balance": 1580,
        "timezone": "Asia/Shanghai",
    })
    print(f"  Seeded 1 account into {accounts_table_name}")

    # Available slots
    slots_table = dynamodb.Table(slots_table_name)
    slots_data = []
    for days_offset in range(1, 4):
        date_str = (now + timedelta(days=days_offset)).strftime("%Y-%m-%d")
        slots_data.append({
            "tutor_id": "TUT001",
            "tutor_name": "王老师",
            "date": date_str,
            "slots": ["09:00", "10:00", "14:00", "15:00"] if days_offset != 3 else ["10:00", "11:00", "15:00"],
        })
        slots_data.append({
            "tutor_id": "TUT002",
            "tutor_name": "李老师",
            "date": date_str,
            "slots": ["10:00", "11:00", "15:00", "16:00"] if days_offset == 1 else ["09:00", "10:00", "14:00"],
        })

    with slots_table.batch_writer() as batch:
        for s in slots_data:
            batch.put_item(Item=s)
    print(f"  Seeded {len(slots_data)} slot records into {slots_table_name}")

    return TABLE_PREFIX


# ---------------------------------------------------------------------------
# Bedrock Knowledge Base Setup
# ---------------------------------------------------------------------------

def get_account_id() -> str:
    """Get current AWS account ID."""
    return boto3.client("sts").get_caller_identity()["Account"]


def get_or_create_bucket(s3_client, bucket_name: str, region: str) -> str:
    """Get existing or create S3 bucket."""
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"  S3 bucket already exists: {bucket_name}")
        return bucket_name
    except s3_client.exceptions.ClientError:
        pass

    print(f"  Creating S3 bucket: {bucket_name}")
    create_kwargs = {"Bucket": bucket_name}
    if region != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3_client.create_bucket(**create_kwargs)
    print(f"  S3 bucket created: {bucket_name}")
    return bucket_name


def upload_docs(s3_client, bucket_name: str) -> int:
    """Upload KB docs to S3 (only KB-specific files)."""
    uploaded = 0
    for fname in KB_DOC_FILES:
        fpath = DOCS_DIR / fname
        if not fpath.exists():
            print(f"  WARNING: KB doc not found: {fpath}")
            continue
        key = f"{S3_PREFIX}/{fname}"
        print(f"  Uploading: {fname} -> s3://{bucket_name}/{key}")
        s3_client.upload_file(str(fpath), bucket_name, key, ExtraArgs={"ContentType": "text/markdown; charset=utf-8"})
        uploaded += 1

    if uploaded == 0:
        print(f"  ERROR: no KB docs uploaded")
        sys.exit(1)
    return uploaded


def get_or_create_kb_role(iam_client, bucket_name: str, region: str, account_id: str) -> str:
    """Create IAM role for Bedrock KB."""
    role_name = "BedrockKBRole_xxxx_demo"

    try:
        resp = iam_client.get_role(RoleName=role_name)
        print(f"  IAM role already exists: {role_name}")
        return resp["Role"]["Arn"]
    except iam_client.exceptions.NoSuchEntityException:
        pass

    print(f"  Creating IAM role: {role_name}")
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account_id},
                "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{region}:{account_id}:knowledge-base/*"},
            },
        }],
    }

    resp = iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description="Bedrock KB role for XXXX demo",
    )
    role_arn = resp["Role"]["Arn"]

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3Access",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket_name}", f"arn:aws:s3:::{bucket_name}/*"],
            },
            {
                "Sid": "BedrockModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": [f"arn:aws:bedrock:{region}::foundation-model/{EMBEDDING_MODEL}"],
            },
            {
                "Sid": "AOSS",
                "Effect": "Allow",
                "Action": ["aoss:APIAccessAll"],
                "Resource": [f"arn:aws:aoss:{region}:{account_id}:collection/*"],
            },
        ],
    }

    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="BedrockKBDemoPolicy",
        PolicyDocument=json.dumps(policy),
    )
    print("  Waiting for IAM role to propagate...")
    time.sleep(10)
    return role_arn


def setup_knowledge_base(region: str) -> str:
    """Create Bedrock Knowledge Base with S3 data source. Returns KB ID."""
    print("\n=== Setting up Bedrock Knowledge Base ===")

    session = boto3.Session(region_name=region)
    account_id = get_account_id()
    s3 = session.client("s3")
    iam = session.client("iam")
    bedrock_agent = session.client("bedrock-agent")

    # S3 bucket
    bucket_name = f"xxxx-demo-kb-{account_id}"
    get_or_create_bucket(s3, bucket_name, region)

    # Upload docs
    count = upload_docs(s3, bucket_name)
    print(f"  Uploaded {count} documents")

    # IAM role
    role_arn = get_or_create_kb_role(iam, bucket_name, region, account_id)

    # Check if KB already exists
    existing_kbs = bedrock_agent.list_knowledge_bases(maxResults=100)
    for kb in existing_kbs.get("knowledgeBaseSummaries", []):
        if kb["name"] == KB_NAME:
            kb_id = kb["knowledgeBaseId"]
            print(f"  Knowledge Base already exists: {kb_id}")

            # Re-sync data source
            ds_list = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id, maxResults=10)
            for ds in ds_list.get("dataSourceSummaries", []):
                ds_id = ds["dataSourceId"]
                print(f"  Re-syncing data source: {ds_id}")
                bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
                print("  Sync started (runs in background)")
            return kb_id

    # Create AOSS collection first
    collection_name = "xxxx-demo-kb"
    collection_arn = _create_aoss_collection(session, collection_name, account_id, role_arn)

    # Create KB
    print(f"  Creating Knowledge Base: {KB_NAME}")
    embedding_arn = f"arn:aws:bedrock:{region}::foundation-model/{EMBEDDING_MODEL}"

    kb_resp = bedrock_agent.create_knowledge_base(
        name=KB_NAME,
        description=KB_DESCRIPTION,
        roleArn=role_arn,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": embedding_arn,
            },
        },
        storageConfiguration={
            "type": "OPENSEARCH_SERVERLESS",
            "opensearchServerlessConfiguration": {
                "collectionArn": collection_arn,
                "vectorIndexName": "bedrock-knowledge-base-default-index",
                "fieldMapping": {
                    "vectorField": "bedrock-knowledge-base-default-vector",
                    "textField": "AMAZON_BEDROCK_TEXT",
                    "metadataField": "AMAZON_BEDROCK_METADATA",
                },
            },
        },
    )

    kb_id = kb_resp["knowledgeBase"]["knowledgeBaseId"]
    print(f"  Knowledge Base created: {kb_id}")

    # Wait for KB to become active
    for i in range(30):
        kb_info = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)
        status = kb_info["knowledgeBase"]["status"]
        if status == "ACTIVE":
            break
        print(f"  Waiting for KB to be active... ({status})")
        time.sleep(5)
    else:
        print("  WARNING: KB not active after 150s, continuing anyway")

    # Create S3 data source
    print("  Creating S3 data source...")
    ds_resp = bedrock_agent.create_data_source(
        knowledgeBaseId=kb_id,
        name="xxxx-demo-docs",
        description="XXXX demo KB documents",
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": f"arn:aws:s3:::{bucket_name}",
                "inclusionPrefixes": [f"{S3_PREFIX}/"],
            },
        },
    )

    ds_id = ds_resp["dataSource"]["dataSourceId"]
    print(f"  Data source created: {ds_id}")

    # Start sync
    print("  Starting ingestion job...")
    bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
    print("  Ingestion started (runs in background, takes 1-2 minutes)")

    return kb_id


def _create_aoss_collection(session, collection_name: str, account_id: str, role_arn: str) -> str:
    """Create OpenSearch Serverless collection with vector index for Bedrock KB."""
    aoss = session.client("opensearchserverless")
    region = session.region_name

    # Check if collection exists
    try:
        collections = aoss.list_collections(collectionFilters={"name": collection_name})
        for c in collections.get("collectionSummaries", []):
            if c["name"] == collection_name:
                print(f"  AOSS collection already exists: {c['arn']}")
                return c["arn"]
    except Exception:
        pass

    # Encryption policy
    enc_name = f"{collection_name}-enc"
    try:
        aoss.create_security_policy(
            name=enc_name, type="encryption",
            policy=json.dumps({
                "Rules": [{"ResourceType": "collection", "Resource": [f"collection/{collection_name}"]}],
                "AWSOwnedKey": True,
            }),
        )
        print(f"  Created encryption policy: {enc_name}")
    except aoss.exceptions.ConflictException:
        print(f"  Encryption policy already exists: {enc_name}")

    # Network policy (public access for demo)
    net_name = f"{collection_name}-net"
    try:
        aoss.create_security_policy(
            name=net_name, type="network",
            policy=json.dumps([{
                "Rules": [
                    {"ResourceType": "collection", "Resource": [f"collection/{collection_name}"]},
                    {"ResourceType": "dashboard", "Resource": [f"collection/{collection_name}"]},
                ],
                "AllowFromPublic": True,
            }]),
        )
        print(f"  Created network policy: {net_name}")
    except aoss.exceptions.ConflictException:
        print(f"  Network policy already exists: {net_name}")

    # Data access policy
    dap_name = f"{collection_name}-dap"
    caller_arn = session.client("sts").get_caller_identity()["Arn"]
    try:
        aoss.create_access_policy(
            name=dap_name, type="data",
            policy=json.dumps([{
                "Rules": [
                    {"ResourceType": "collection", "Resource": [f"collection/{collection_name}"],
                     "Permission": ["aoss:CreateCollectionItems", "aoss:UpdateCollectionItems",
                                    "aoss:DescribeCollectionItems"]},
                    {"ResourceType": "index", "Resource": [f"index/{collection_name}/*"],
                     "Permission": ["aoss:CreateIndex", "aoss:UpdateIndex", "aoss:DescribeIndex",
                                    "aoss:ReadDocument", "aoss:WriteDocument"]},
                ],
                "Principal": [role_arn, caller_arn],
            }]),
        )
        print(f"  Created data access policy: {dap_name}")
    except aoss.exceptions.ConflictException:
        print(f"  Data access policy already exists: {dap_name}")

    # Create collection
    print(f"  Creating AOSS collection: {collection_name}")
    resp = aoss.create_collection(name=collection_name, type="VECTORSEARCH")
    collection_arn = resp["createCollectionDetail"]["arn"]
    collection_id = resp["createCollectionDetail"]["id"]
    print(f"  Collection ARN: {collection_arn}")

    # Wait for ACTIVE
    print("  Waiting for AOSS collection to become ACTIVE...")
    endpoint = None
    for i in range(60):
        detail = aoss.batch_get_collection(ids=[collection_id])
        status = detail["collectionDetails"][0]["status"]
        if status == "ACTIVE":
            endpoint = detail["collectionDetails"][0]["collectionEndpoint"]
            print(f"  AOSS collection is ACTIVE (endpoint: {endpoint})")
            break
        if status == "FAILED":
            print("  ERROR: AOSS collection creation FAILED")
            sys.exit(1)
        if i % 6 == 0:
            print(f"  Collection status: {status}...")
        time.sleep(5)

    # Create vector index
    if endpoint:
        _create_vector_index(endpoint, session)

    return collection_arn


def _create_vector_index(endpoint: str, session):
    """Create vector index in AOSS collection."""
    try:
        from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
    except ImportError:
        print("  WARNING: opensearch-py not installed, skipping index creation")
        print("  Install with: pip install opensearch-py")
        return

    credentials = session.get_credentials()
    region = session.region_name
    auth = AWSV4SignerAuth(credentials, region, "aoss")

    host = endpoint.replace("https://", "")
    client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )

    index_name = "bedrock-knowledge-base-default-index"
    if client.indices.exists(index=index_name):
        print(f"  Vector index already exists: {index_name}")
        return

    print(f"  Creating vector index: {index_name}")
    client.indices.create(
        index=index_name,
        body={
            "settings": {"index.knn": True},
            "mappings": {
                "properties": {
                    "bedrock-knowledge-base-default-vector": {
                        "type": "knn_vector",
                        "dimension": 1024,
                        "method": {"engine": "faiss", "name": "hnsw"},
                    },
                    "AMAZON_BEDROCK_TEXT": {"type": "text"},
                    "AMAZON_BEDROCK_METADATA": {"type": "text"},
                }
            },
        },
    )
    print(f"  Vector index created: {index_name}")


# ---------------------------------------------------------------------------
# .env Update
# ---------------------------------------------------------------------------

def update_env_file(key: str, value: str):
    """Add or update a key in .env file."""
    env_path = Path(__file__).parent.parent / ".env"

    lines = []
    found = False
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                found = True
                break

    if not found:
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n")
    print(f"  Saved {key}={value} to .env")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Setup XXXX Demo Mock Infrastructure")
    parser.add_argument("--region", default="us-west-2", help="AWS region (default: us-west-2)")
    parser.add_argument("--skip-kb", action="store_true", help="Skip Knowledge Base setup")
    parser.add_argument("--skip-dynamodb", action="store_true", help="Skip DynamoDB setup")
    args = parser.parse_args()

    print("=" * 60)
    print("  XXXX Demo - Mock Infrastructure Setup")
    print(f"  Region: {args.region}")
    print("=" * 60)

    if not args.skip_dynamodb:
        prefix = setup_dynamodb(args.region)
        update_env_file("DYNAMODB_TABLE_PREFIX", prefix)
        update_env_file("AWS_REGION", args.region)

    if not args.skip_kb:
        kb_id = setup_knowledge_base(args.region)
        update_env_file("KNOWLEDGE_BASE_ID", kb_id)

    print("\n" + "=" * 60)
    print("  Setup complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
