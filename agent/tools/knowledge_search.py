"""知识库检索工具 - Bedrock Knowledge Base 实现"""

import logging
import os

import boto3

logger = logging.getLogger(__name__)

_kb_client = None


def _get_kb_client():
    """Lazy-init Bedrock Agent Runtime client."""
    global _kb_client
    if _kb_client is None:
        region = os.environ.get("AWS_REGION", "us-west-2")
        _kb_client = boto3.client("bedrock-agent-runtime", region_name=region)
    return _kb_client


def search_knowledge_base(query: str, kb_type: str = "all") -> dict:
    """搜索 Bedrock Knowledge Base。

    Args:
        query: 搜索关键词
        kb_type: 知识库类型 (保留参数，Bedrock KB 统一检索)

    Returns:
        搜索结果字典
    """
    kb_id = os.environ.get("KNOWLEDGE_BASE_ID", "")
    if not kb_id:
        return {
            "success": False,
            "error": "KNOWLEDGE_BASE_ID not configured",
            "query": query,
            "total_results": 0,
            "results": [],
        }

    try:
        client = _get_kb_client()

        response = client.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": 5,
                }
            },
        )

        results = []
        for item in response.get("retrievalResults", []):
            content_text = item.get("content", {}).get("text", "")
            score = item.get("score", 0)
            source = item.get("location", {}).get("s3Location", {}).get("uri", "")
            source_name = source.split("/")[-1] if source else ""

            results.append({
                "content": content_text,
                "score": round(score, 4) if isinstance(score, float) else score,
                "source": source_name,
            })

        return {
            "success": True,
            "query": query,
            "kb_type": kb_type,
            "total_results": len(results),
            "results": results,
        }

    except Exception as e:
        logger.error(f"Bedrock KB search failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "query": query,
            "total_results": 0,
            "results": [],
        }
