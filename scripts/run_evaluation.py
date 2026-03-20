#!/usr/bin/env python3
"""Run AgentCore Evaluations on CloudWatch spans from a test request.

Steps:
1. Send a test request to AgentCore Runtime with a known session_id
2. Wait for spans to appear in CloudWatch
3. Fetch spans using the official CloudWatchSpanHelper
4. Run all 13 built-in evaluators

Usage:
    python scripts/run_evaluation.py
    python scripts/run_evaluation.py --session-id "existing-session-id"
    python scripts/run_evaluation.py --skip-invoke  # Use existing spans
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import boto3

# ============================================================
# Configuration
# ============================================================

REGION = "us-west-2"
RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-west-2:<ACCOUNT_ID>:runtime/<RUNTIME_ID>"
RUNTIME_ID = "<RUNTIME_ID>"
EVENT_LOG_GROUP = f"/aws/bedrock-agentcore/runtimes/{RUNTIME_ID}-DEFAULT"
PARENT_ID = "<TEST_PARENT_ID>"

# All 13 built-in evaluators
EVALUATORS = [
    "Builtin.Coherence",
    "Builtin.Conciseness",
    "Builtin.Correctness",
    "Builtin.Faithfulness",
    "Builtin.GoalSuccessRate",
    "Builtin.Harmfulness",
    "Builtin.Helpfulness",
    "Builtin.InstructionFollowing",
    "Builtin.Refusal",
    "Builtin.ResponseRelevance",
    "Builtin.Stereotyping",
    "Builtin.ToolParameterAccuracy",
    "Builtin.ToolSelectionAccuracy",
]

TEST_PROMPT = "你好，请问如何查看我的课程安排？"


# ============================================================
# Step 1: Invoke Agent
# ============================================================

def invoke_agent(session_id: str) -> str:
    """Send a test request to AgentCore Runtime."""
    client = boto3.client("bedrock-agentcore", region_name=REGION)

    payload = json.dumps({
        "prompt": TEST_PROMPT,
        "parent_id": PARENT_ID,
        "session_id": session_id,
    })

    print(f"\n=== Invoking agent ===")
    print(f"Session ID: {session_id}")
    print(f"Prompt: {TEST_PROMPT}")

    response = client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        runtimeSessionId=session_id,
        payload=payload.encode("utf-8"),
    )

    # Read response
    body = response.get("body", b"")
    if hasattr(body, "read"):
        body = body.read()
    if isinstance(body, bytes):
        body = body.decode("utf-8")

    print(f"Response ({len(body)} chars): {body[:200]}...")
    return body


# ============================================================
# Step 2: Fetch spans from CloudWatch
# ============================================================

def fetch_spans(session_id: str, start_time: datetime) -> list[dict]:
    """Fetch ADOT spans from CloudWatch using official helper."""
    from bedrock_agentcore.evaluation.utils.cloudwatch_span_helper import (
        fetch_spans_from_cloudwatch,
    )

    print(f"\n=== Fetching spans from CloudWatch ===")
    print(f"Session ID: {session_id}")
    print(f"Event log group: {EVENT_LOG_GROUP}")
    print(f"Start time: {start_time}")

    spans = fetch_spans_from_cloudwatch(
        session_id=session_id,
        event_log_group=EVENT_LOG_GROUP,
        start_time=start_time,
        region=REGION,
    )

    print(f"Found {len(spans)} valid ADOT items")

    # Show summary
    span_types = {}
    for item in spans:
        scope_name = item.get("scope", {}).get("name", "unknown")
        has_session = "session.id" in item.get("attributes", {})
        key = f"{scope_name} (session.id={'yes' if has_session else 'NO'})"
        span_types[key] = span_types.get(key, 0) + 1

    print("Span breakdown:")
    for k, v in sorted(span_types.items()):
        print(f"  {k}: {v}")

    return spans


# ============================================================
# Step 3: Run evaluators
# ============================================================

def run_evaluators(spans: list[dict], evaluator_ids: list[str] | None = None) -> dict:
    """Run AgentCore evaluators on the fetched spans."""
    if evaluator_ids is None:
        evaluator_ids = EVALUATORS

    client = boto3.client("agentcore-evaluation-dataplane", region_name=REGION)

    print(f"\n=== Running {len(evaluator_ids)} evaluators ===")
    print(f"Input: {len(spans)} ADOT items")

    results = {}
    for eval_id in evaluator_ids:
        print(f"\n  Running: {eval_id}...")
        try:
            response = client.evaluate(
                evaluatorId=eval_id,
                evaluationInput={"sessionSpans": spans},
            )

            eval_results = response.get("evaluationResults", [])
            if eval_results:
                for r in eval_results:
                    score = r.get("score")
                    reasoning = r.get("reasoning", "")[:200]
                    print(f"    Score: {score}")
                    print(f"    Reasoning: {reasoning}...")
                results[eval_id] = {
                    "status": "success",
                    "results": eval_results,
                }
            else:
                print(f"    No results returned")
                results[eval_id] = {
                    "status": "empty",
                    "raw_response": response,
                }

        except Exception as e:
            error_msg = str(e)
            print(f"    ERROR: {error_msg[:200]}")
            results[eval_id] = {
                "status": "error",
                "error": error_msg,
            }

    return results


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Run AgentCore Evaluations")
    parser.add_argument("--session-id", help="Existing session ID to evaluate")
    parser.add_argument("--skip-invoke", action="store_true", help="Skip invoking agent")
    parser.add_argument("--wait", type=int, default=90, help="Seconds to wait for spans (default: 90)")
    parser.add_argument("--evaluator", action="append", help="Specific evaluator(s) to run")
    parser.add_argument("--start-minutes-ago", type=int, default=10, help="Start time offset in minutes")
    args = parser.parse_args()

    # Generate or use existing session ID
    session_id = args.session_id or f"eval-{uuid.uuid4().hex[:8]}-{int(time.time())}"
    start_time = datetime.now(timezone.utc) - timedelta(minutes=args.start_minutes_ago)

    # Step 1: Invoke agent
    if not args.skip_invoke:
        invoke_agent(session_id)
        wait_seconds = args.wait
        print(f"\n=== Waiting {wait_seconds}s for spans to appear in CloudWatch ===")
        for i in range(wait_seconds, 0, -10):
            print(f"  {i}s remaining...")
            time.sleep(min(10, i))
    else:
        print(f"\nSkipping invocation, using session_id: {session_id}")

    # Step 2: Fetch spans
    spans = fetch_spans(session_id, start_time)

    if not spans:
        print("\nERROR: No spans found in CloudWatch. Try increasing --wait or --start-minutes-ago.")
        sys.exit(1)

    # Save spans to file for debugging
    output_file = f"eval_spans_{session_id[:20]}.json"
    with open(output_file, "w") as f:
        json.dump(spans, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nSpans saved to: {output_file}")

    # Step 3: Run evaluators
    evaluator_ids = args.evaluator if args.evaluator else None
    results = run_evaluators(spans, evaluator_ids)

    # Summary
    print(f"\n{'='*60}")
    print("EVALUATION SUMMARY")
    print(f"{'='*60}")
    success = sum(1 for r in results.values() if r["status"] == "success")
    errors = sum(1 for r in results.values() if r["status"] == "error")
    empty = sum(1 for r in results.values() if r["status"] == "empty")
    print(f"Total: {len(results)} | Success: {success} | Errors: {errors} | Empty: {empty}")

    for eval_id, result in sorted(results.items()):
        status = result["status"]
        if status == "success":
            scores = [r.get("score") for r in result.get("results", [])]
            print(f"  {eval_id}: {scores}")
        elif status == "error":
            print(f"  {eval_id}: ERROR - {result['error'][:100]}")
        else:
            print(f"  {eval_id}: {status}")

    # Save results
    results_file = f"eval_results_{session_id[:20]}.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved to: {results_file}")


if __name__ == "__main__":
    main()
