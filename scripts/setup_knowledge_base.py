#!/usr/bin/env python3
"""
Setup AWS Bedrock Knowledge Base for XXXX Global Service Support.

This script:
1. Creates an S3 bucket and uploads knowledge base documents
2. Creates a Bedrock Knowledge Base with S3 data source
3. Syncs the data source
4. Saves the KNOWLEDGE_BASE_ID to the .env file

Usage:
    python scripts/setup_knowledge_base.py
    python scripts/setup_knowledge_base.py --region us-west-2
    python scripts/setup_knowledge_base.py --source-dir /path/to/docs
    python scripts/setup_knowledge_base.py --bucket-name my-custom-bucket

Documents are organized by category in S3 prefixes for logical separation.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Category mapping: filename -> S3 prefix category
FILE_CATEGORY_MAP = {
    "IT故障常见FAQ.md": "technical-support",
    "上课与技术问题.md": "technical-support",
    "作业与资料类.md": "learning-materials",
    "公司与品牌介绍.md": "company-info",
    "支付与退款类.md": "payment-refund",
    "活动与提醒类.md": "activities-notifications",
    "用户常见FAQ.md": "user-faq",
    "用户常见FAQ2.md": "user-faq",
    "课时与积分规则.md": "credits-rules",
    "课程与产品知识.md": "courses-products",
    "课程操作相关.md": "course-operations",
}

# S3 bucket naming
DEFAULT_BUCKET_PREFIX = "xxxx-kb"
DEFAULT_S3_PREFIX = "global-service-support"

# Bedrock KB config
KB_NAME = "xxxx-global-service-support"
KB_DESCRIPTION = "XXXX Global Service Support Knowledge Base - contains FAQ, course info, payment rules, and technical support documents"
# cohere.embed-multilingual-v3 is best for Chinese + English mixed content
EMBEDDING_MODEL_ARN = "arn:aws:bedrock:{region}::foundation-model/cohere.embed-multilingual-v3"


def get_or_create_bucket(s3_client, bucket_name: str, region: str) -> str:
    """Get existing or create new S3 bucket."""
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"  S3 bucket already exists: {bucket_name}")
        return bucket_name
    except s3_client.exceptions.ClientError:
        pass

    print(f"  Creating S3 bucket: {bucket_name}")
    create_kwargs = {"Bucket": bucket_name}
    # us-east-1 doesn't accept LocationConstraint
    if region != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {
            "LocationConstraint": region
        }
    s3_client.create_bucket(**create_kwargs)
    print(f"  S3 bucket created: {bucket_name}")
    return bucket_name


def upload_documents(s3_client, bucket_name: str, source_dir: str, s3_prefix: str) -> int:
    """Upload documents to S3 with category-based prefix organization."""
    source_path = Path(source_dir)
    if not source_path.exists():
        print(f"  ERROR: Source directory not found: {source_dir}")
        sys.exit(1)

    md_files = list(source_path.glob("*.md"))
    if not md_files:
        print(f"  ERROR: No .md files found in {source_dir}")
        sys.exit(1)

    uploaded = 0
    for md_file in md_files:
        category = FILE_CATEGORY_MAP.get(md_file.name, "general")
        s3_key = f"{s3_prefix}/{category}/{md_file.name}"

        print(f"  Uploading: {md_file.name} -> s3://{bucket_name}/{s3_key}")
        s3_client.upload_file(
            str(md_file),
            bucket_name,
            s3_key,
            ExtraArgs={"ContentType": "text/markdown; charset=utf-8"},
        )
        uploaded += 1

    print(f"  Uploaded {uploaded} files to s3://{bucket_name}/{s3_prefix}/")
    return uploaded


def get_or_create_kb_role(iam_client, bucket_name: str, region: str, account_id: str) -> str:
    """Get existing or create IAM role for Bedrock Knowledge Base."""
    role_name = "AmazonBedrockExecutionRoleForKnowledgeBase_xxxx"

    try:
        response = iam_client.get_role(RoleName=role_name)
        role_arn = response["Role"]["Arn"]
        print(f"  IAM role already exists: {role_name}")
        return role_arn
    except iam_client.exceptions.NoSuchEntityException:
        pass

    print(f"  Creating IAM role: {role_name}")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock:{region}:{account_id}:knowledge-base/*"
                    },
                },
            }
        ],
    }

    response = iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Description="Bedrock KB role for XXXX Global Service Support",
    )
    role_arn = response["Role"]["Arn"]

    # Attach permissions policy
    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3Access",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
            },
            {
                "Sid": "BedrockModelAccess",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": [
                    f"arn:aws:bedrock:{region}::foundation-model/cohere.embed-multilingual-v3",
                ],
            },
            {
                "Sid": "AOSSAccess",
                "Effect": "Allow",
                "Action": ["aoss:APIAccessAll"],
                "Resource": [
                    f"arn:aws:aoss:{region}:{account_id}:collection/*",
                ],
            },
        ],
    }

    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="BedrockKBXXXXPolicy",
        PolicyDocument=json.dumps(permissions_policy),
    )

    # Wait for role to propagate
    print("  Waiting for IAM role to propagate...")
    time.sleep(10)

    return role_arn


def create_aoss_collection_and_index(
    session, collection_name: str, account_id: str, role_arn: str
) -> str:
    """Create OpenSearch Serverless collection with vector index."""
    aoss_client = session.client("opensearchserverless")

    # Check if collection exists
    try:
        collections = aoss_client.list_collections(
            collectionFilters={"name": collection_name}
        )
        for c in collections.get("collectionSummaries", []):
            if c["name"] == collection_name:
                print(f"  AOSS collection already exists: {c['arn']}")
                return c["arn"]
    except Exception:
        pass

    # Create encryption policy
    enc_policy_name = f"{collection_name}-enc"
    try:
        aoss_client.create_security_policy(
            name=enc_policy_name,
            type="encryption",
            policy=json.dumps({
                "Rules": [{"ResourceType": "collection", "Resource": [f"collection/{collection_name}"]}],
                "AWSOwnedKey": True,
            }),
        )
        print(f"  Created encryption policy: {enc_policy_name}")
    except aoss_client.exceptions.ConflictException:
        print(f"  Encryption policy already exists: {enc_policy_name}")

    # Create network policy (public access for simplicity)
    net_policy_name = f"{collection_name}-net"
    try:
        aoss_client.create_security_policy(
            name=net_policy_name,
            type="network",
            policy=json.dumps([{
                "Rules": [{"ResourceType": "collection", "Resource": [f"collection/{collection_name}"]},
                           {"ResourceType": "dashboard", "Resource": [f"collection/{collection_name}"]}],
                "AllowFromPublic": True,
            }]),
        )
        print(f"  Created network policy: {net_policy_name}")
    except aoss_client.exceptions.ConflictException:
        print(f"  Network policy already exists: {net_policy_name}")

    # Create data access policy
    dap_name = f"{collection_name}-dap"
    caller_arn = session.client("sts").get_caller_identity()["Arn"]
    try:
        aoss_client.create_access_policy(
            name=dap_name,
            type="data",
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
    except aoss_client.exceptions.ConflictException:
        print(f"  Data access policy already exists: {dap_name}")

    # Create collection
    print(f"  Creating AOSS collection: {collection_name}")
    response = aoss_client.create_collection(
        name=collection_name,
        type="VECTORSEARCH",
    )
    collection_arn = response["createCollectionDetail"]["arn"]
    collection_id = response["createCollectionDetail"]["id"]
    print(f"  Collection created: {collection_arn}")

    # Wait for collection to be ACTIVE
    print("  Waiting for AOSS collection to become ACTIVE...")
    for i in range(60):
        detail = aoss_client.batch_get_collection(ids=[collection_id])
        status = detail["collectionDetails"][0]["status"]
        if status == "ACTIVE":
            print(f"  AOSS collection is ACTIVE")
            endpoint = detail["collectionDetails"][0]["collectionEndpoint"]
            print(f"  Endpoint: {endpoint}")
            # Create vector index
            _create_vector_index(endpoint, session)
            return collection_arn
        if status == "FAILED":
            print(f"  ERROR: Collection creation FAILED")
            sys.exit(1)
        if i % 6 == 0:
            print(f"  Collection status: {status}...")
        time.sleep(5)

    print("  WARNING: Collection still not active after timeout")
    return collection_arn


def _create_vector_index(endpoint: str, session):
    """Create vector index in OpenSearch Serverless collection."""
    from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

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
    # Check if index exists
    if client.indices.exists(index=index_name):
        print(f"  Vector index already exists: {index_name}")
        return

    print(f"  Creating vector index: {index_name}")
    index_body = {
        "settings": {
            "index.knn": True,
        },
        "mappings": {
            "properties": {
                "bedrock-knowledge-base-default-vector": {
                    "type": "knn_vector",
                    "dimension": 1024,
                    "method": {
                        "engine": "faiss",
                        "name": "hnsw",
                    },
                },
                "AMAZON_BEDROCK_TEXT": {"type": "text"},
                "AMAZON_BEDROCK_METADATA": {"type": "text"},
            }
        },
    }
    client.indices.create(index=index_name, body=index_body)
    print(f"  Vector index created: {index_name}")


def create_knowledge_base(
    bedrock_agent_client,
    kb_name: str,
    role_arn: str,
    embedding_model_arn: str,
    collection_arn: str,
) -> dict:
    """Create Bedrock Knowledge Base."""
    # Check if KB already exists
    existing = bedrock_agent_client.list_knowledge_bases(maxResults=100)
    for kb in existing.get("knowledgeBaseSummaries", []):
        if kb["name"] == kb_name and kb["status"] == "ACTIVE":
            print(f"  Knowledge Base already exists: {kb['knowledgeBaseId']}")
            return bedrock_agent_client.get_knowledge_base(
                knowledgeBaseId=kb["knowledgeBaseId"]
            )["knowledgeBase"]

    print(f"  Creating Knowledge Base: {kb_name}")
    response = bedrock_agent_client.create_knowledge_base(
        name=kb_name,
        description=KB_DESCRIPTION,
        roleArn=role_arn,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": embedding_model_arn,
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

    kb = response["knowledgeBase"]
    kb_id = kb["knowledgeBaseId"]
    print(f"  Knowledge Base created: {kb_id}")

    # Wait for KB to become active
    print("  Waiting for Knowledge Base to become ACTIVE...")
    for _ in range(30):
        status = bedrock_agent_client.get_knowledge_base(
            knowledgeBaseId=kb_id
        )["knowledgeBase"]["status"]
        if status == "ACTIVE":
            print(f"  Knowledge Base is ACTIVE")
            return bedrock_agent_client.get_knowledge_base(
                knowledgeBaseId=kb_id
            )["knowledgeBase"]
        if status == "FAILED":
            print(f"  ERROR: Knowledge Base creation FAILED")
            sys.exit(1)
        time.sleep(5)

    print("  WARNING: Knowledge Base still not ACTIVE after timeout")
    return kb


def create_and_sync_data_source(
    bedrock_agent_client,
    kb_id: str,
    bucket_name: str,
    s3_prefix: str,
) -> str:
    """Create S3 data source and start sync."""
    ds_name = "xxxx-service-support-docs"

    # Check existing data sources
    existing = bedrock_agent_client.list_data_sources(
        knowledgeBaseId=kb_id, maxResults=100
    )
    for ds in existing.get("dataSourceSummaries", []):
        if ds["name"] == ds_name:
            ds_id = ds["dataSourceId"]
            print(f"  Data source already exists: {ds_id}, re-syncing...")
            _start_sync(bedrock_agent_client, kb_id, ds_id)
            return ds_id

    print(f"  Creating data source: {ds_name}")
    response = bedrock_agent_client.create_data_source(
        knowledgeBaseId=kb_id,
        name=ds_name,
        description="XXXX Global Service Support documents from S3",
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": f"arn:aws:s3:::{bucket_name}",
                "inclusionPrefixes": [f"{s3_prefix}/"],
            },
        },
        vectorIngestionConfiguration={
            "chunkingConfiguration": {
                "chunkingStrategy": "SEMANTIC",
                "semanticChunkingConfiguration": {
                    "maxTokens": 512,
                    "bufferSize": 0,
                    "breakpointPercentileThreshold": 95,
                },
            }
        },
    )

    ds_id = response["dataSource"]["dataSourceId"]
    print(f"  Data source created: {ds_id}")

    _start_sync(bedrock_agent_client, kb_id, ds_id)
    return ds_id


def _start_sync(bedrock_agent_client, kb_id: str, ds_id: str):
    """Start data source sync and wait for completion."""
    print("  Starting data source sync...")
    bedrock_agent_client.start_ingestion_job(
        knowledgeBaseId=kb_id,
        dataSourceId=ds_id,
    )

    for i in range(60):
        jobs = bedrock_agent_client.list_ingestion_jobs(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            maxResults=1,
            sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
        )
        if jobs["ingestionJobSummaries"]:
            status = jobs["ingestionJobSummaries"][0]["status"]
            if status == "COMPLETE":
                stats = jobs["ingestionJobSummaries"][0].get("statistics", {})
                print(f"  Sync complete! Documents scanned: {stats.get('numberOfDocumentsScanned', '?')}, "
                      f"indexed: {stats.get('numberOfNewDocumentsIndexed', '?')}")
                return
            if status == "FAILED":
                print(f"  ERROR: Sync FAILED")
                failure = jobs["ingestionJobSummaries"][0].get("failureReasons", [])
                if failure:
                    print(f"  Reasons: {failure}")
                sys.exit(1)
            if i % 6 == 0:
                print(f"  Sync in progress ({status})...")
        time.sleep(5)

    print("  WARNING: Sync still in progress after timeout, check console")


def save_to_env(kb_id: str, env_path: str):
    """Save KNOWLEDGE_BASE_ID to .env file."""
    env_file = Path(env_path)

    lines = []
    found = False
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("KNOWLEDGE_BASE_ID="):
                lines[i] = f"KNOWLEDGE_BASE_ID={kb_id}"
                found = True
                break

    if not found:
        lines.append(f"KNOWLEDGE_BASE_ID={kb_id}")

    env_file.write_text("\n".join(lines) + "\n")
    print(f"  Saved KNOWLEDGE_BASE_ID={kb_id} to {env_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Setup Bedrock Knowledge Base for XXXX"
    )
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    parser.add_argument(
        "--source-dir",
        default=None,
        help="Directory containing .md files (default: ~/Downloads/Global_Service_Support)",
    )
    parser.add_argument("--bucket-name", default=None, help="S3 bucket name")
    args = parser.parse_args()

    # Defaults
    source_dir = args.source_dir or os.path.expanduser(
        "~/Downloads/Global_Service_Support"
    )
    region = args.region

    import boto3

    session = boto3.Session(region_name=region)
    account_id = session.client("sts").get_caller_identity()["Account"]

    bucket_name = args.bucket_name or f"{DEFAULT_BUCKET_PREFIX}-{account_id}-{region}"
    s3_prefix = DEFAULT_S3_PREFIX
    embedding_model_arn = EMBEDDING_MODEL_ARN.format(region=region)
    env_path = str(Path(__file__).parent.parent / ".env")

    print(f"\n{'='*60}")
    print("XXXX Bedrock Knowledge Base Setup")
    print(f"{'='*60}")
    print(f"  Region:          {region}")
    print(f"  Account:         {account_id}")
    print(f"  Source dir:      {source_dir}")
    print(f"  S3 bucket:       {bucket_name}")
    print(f"  Embedding model: cohere.embed-multilingual-v3")
    print()

    # Step 1: S3 bucket & upload
    print("[1/7] Setting up S3 bucket...")
    s3_client = session.client("s3")
    get_or_create_bucket(s3_client, bucket_name, region)

    print("\n[2/7] Uploading documents...")
    upload_documents(s3_client, bucket_name, source_dir, s3_prefix)

    # Step 2: IAM role
    print("\n[3/7] Setting up IAM role...")
    iam_client = session.client("iam")
    role_arn = get_or_create_kb_role(iam_client, bucket_name, region, account_id)

    # Step 3: AOSS collection
    print("\n[4/7] Creating OpenSearch Serverless collection...")
    collection_arn = create_aoss_collection_and_index(
        session, "xxxx-kb", account_id, role_arn
    )

    # Step 4: Create KB
    print("\n[5/7] Creating Knowledge Base...")
    bedrock_agent = session.client("bedrock-agent")
    kb = create_knowledge_base(
        bedrock_agent, KB_NAME, role_arn, embedding_model_arn, collection_arn
    )
    kb_id = kb["knowledgeBaseId"]

    # Step 5: Data source + sync
    print("\n[6/7] Creating data source and syncing...")
    create_and_sync_data_source(bedrock_agent, kb_id, bucket_name, s3_prefix)

    # Step 7: Save to .env
    print("\n[7/7] Saving configuration...")
    save_to_env(kb_id, env_path)

    print(f"\n{'='*60}")
    print("Setup complete!")
    print(f"  Knowledge Base ID: {kb_id}")
    print(f"  S3 bucket:         s3://{bucket_name}/{s3_prefix}/")
    print()
    print("To re-sync after updating documents:")
    print(f"  python scripts/setup_knowledge_base.py --region {region}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
