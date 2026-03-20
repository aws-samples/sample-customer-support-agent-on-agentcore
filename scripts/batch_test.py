#!/usr/bin/env python3
"""批量测试 XXXX Agent

读取测试集 Excel 文件，调用 AgentCore Runtime API 测试每行数据，
并将结果写入新的 Excel 文件。

Usage:
    python scripts/batch_test.py [--input FILE] [--sheet SHEET] [--limit N]

Examples:
    # 测试所有数据
    python scripts/batch_test.py

    # 只测试前 10 行
    python scripts/batch_test.py --limit 10

    # 只测试多模态数据
    python scripts/batch_test.py --sheet 多模态数据
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import boto3
import pandas as pd

# ============================================================
# 配置
# ============================================================

RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-west-2:<ACCOUNT_ID>:runtime/<RUNTIME_ID>"
PARENT_ID = "<TEST_PARENT_ID>"
DEFAULT_INPUT_FILE = "/Users/peijiaw/Desktop/lingo/chatbot/测试集.xlsx"
REGION = "us-west-2"

# 请求间隔（秒）- 避免限流
REQUEST_INTERVAL = 2.0


# ============================================================
# AgentCore 调用函数
# ============================================================

def invoke_agent(
    prompt: str,
    conversation_history: str = "",
    images: list[str] | None = None,
    session_id: str | None = None,
) -> str:
    """
    调用 AgentCore Runtime API (使用 agentcore CLI)

    Args:
        prompt: 用户问题
        conversation_history: 对话历史上下文
        images: 图片 URL 列表
        session_id: 会话 ID

    Returns:
        Agent 回复文本
    """
    import subprocess

    # 构建 payload
    payload = {
        "prompt": prompt,
        "parent_id": PARENT_ID,
        "session_id": session_id or f"batch-{int(time.time())}",
        "conversation_history": conversation_history,
        "images": images or [],
    }

    # 转为 JSON 字符串
    payload_json = json.dumps(payload, ensure_ascii=False)

    # 调用 agentcore invoke
    cmd = ["agentcore", "invoke", payload_json]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        raise Exception(f"agentcore error: {result.stderr}")

    # 解析输出 - agentcore invoke 输出 JSON 对象，用空行分隔
    # data 字段可能含未转义的换行符，需要特殊处理
    import re
    response_text = ""
    for block in result.stdout.split("\n\n"):
        block = block.strip()
        if not block or not block.startswith("{"):
            continue
        try:
            event_data = json.loads(block)
        except json.JSONDecodeError:
            # data 字段含未转义换行符 → 替换后重试
            fixed = re.sub(
                r'("data":\s*")(.*?)("\s*})',
                lambda m: m.group(1) + m.group(2).replace("\n", "\\n") + m.group(3),
                block,
                flags=re.DOTALL,
            )
            try:
                event_data = json.loads(fixed)
            except json.JSONDecodeError:
                continue
        if event_data.get("type") == "chunk":
            response_text += event_data.get("data", "")
        elif event_data.get("type") == "complete":
            break  # 收到 complete 即结束

    return response_text.strip()


def invoke_agent_simple(
    prompt: str,
    conversation_history: str = "",
    images: list[str] | None = None,
    row_idx: int = 0,
    sheet_name: str = "sheet",
) -> str:
    """
    简化的 Agent 调用（带重试）

    Args:
        prompt: 用户问题
        conversation_history: 对话历史
        images: 图片 URL 列表
        row_idx: 行索引
        sheet_name: Sheet 名称

    Returns:
        Agent 回复或错误信息
    """
    session_id = f"batch-{sheet_name}-{row_idx:04d}"

    max_retries = 2
    for attempt in range(max_retries):
        try:
            return invoke_agent(prompt, conversation_history, images, session_id)
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"    重试 {attempt + 1}/{max_retries}: {e}")
                time.sleep(3)
            else:
                return f"[错误] {type(e).__name__}: {str(e)}"

    return "[错误] 未知错误"


# ============================================================
# 数据处理函数
# ============================================================

def process_sheet(
    df: pd.DataFrame,
    sheet_name: str,
    has_images: bool = False,
    limit: int | None = None,
) -> list[str]:
    """
    处理单个 Sheet 的数据

    Args:
        df: DataFrame
        sheet_name: Sheet 名称
        has_images: 是否包含图片
        limit: 限制处理行数

    Returns:
        Agent 回复列表
    """
    results = []
    total = len(df) if limit is None else min(limit, len(df))

    print(f"\n处理 {sheet_name} ({total} 行)...")
    start_time = time.time()

    for idx, row in df.iterrows():
        if limit and idx >= limit:
            break

        # 提取数据
        prompt = str(row.get("用户问题", "")) if pd.notna(row.get("用户问题")) else ""
        history = str(row.get("history上下文", "")) if pd.notna(row.get("history上下文")) else ""

        # 图片 URL（仅多模态数据）
        images = []
        if has_images:
            url = row.get("附件url")
            if pd.notna(url) and str(url).startswith("http"):
                images = [str(url)]

        # 跳过空问题
        if not prompt.strip():
            results.append("[跳过] 用户问题为空")
            continue

        # 调用 Agent
        print(f"  [{idx + 1}/{total}] 处理中...", end="", flush=True)
        response = invoke_agent_simple(prompt, history, images, idx, sheet_name)
        results.append(response)

        # 显示简短结果
        preview = response[:50].replace("\n", " ") + "..." if len(response) > 50 else response.replace("\n", " ")
        print(f" 完成: {preview}")

        # 请求间隔
        time.sleep(REQUEST_INTERVAL)

    elapsed = time.time() - start_time
    print(f"  完成! 耗时: {elapsed:.1f} 秒 ({elapsed/total:.1f} 秒/行)")

    return results


def run_batch_test(
    input_file: str,
    output_file: str | None = None,
    sheet_filter: str | None = None,
    limit: int | None = None,
):
    """
    执行批量测试

    Args:
        input_file: 输入 Excel 文件路径
        output_file: 输出 Excel 文件路径（可选）
        sheet_filter: 只处理指定 Sheet
        limit: 每个 Sheet 限制处理行数
    """
    print(f"读取测试集: {input_file}")
    xlsx = pd.ExcelFile(input_file)
    print(f"Sheet 列表: {xlsx.sheet_names}")

    results = {}

    # 处理 Sheet 1: 线上case
    if sheet_filter is None or sheet_filter == "线上case":
        df1 = pd.read_excel(xlsx, sheet_name="线上case")
        responses = process_sheet(df1, "线上case", has_images=False, limit=limit)
        # 只更新处理过的行
        df1["AI回复_新"] = ""
        for i, resp in enumerate(responses):
            df1.at[i, "AI回复_新"] = resp
        results["线上case"] = df1

    # 处理 Sheet 2: 多模态数据
    if sheet_filter is None or sheet_filter == "多模态数据":
        df2 = pd.read_excel(xlsx, sheet_name="多模态数据")
        responses = process_sheet(df2, "多模态数据", has_images=True, limit=limit)
        # 只更新处理过的行
        df2["AI回复_新"] = ""
        for i, resp in enumerate(responses):
            df2.at[i, "AI回复_新"] = resp
        results["多模态数据"] = df2

    # 保存结果
    if not output_file:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = input_file.replace(".xlsx", f"_结果_{timestamp}.xlsx")

    print(f"\n保存结果到: {output_file}")
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        for sheet_name, df in results.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    print("完成!")
    return output_file


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="批量测试 XXXX Agent")
    parser.add_argument(
        "--input", "-i",
        default=DEFAULT_INPUT_FILE,
        help=f"输入 Excel 文件路径 (默认: {DEFAULT_INPUT_FILE})"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="输出 Excel 文件路径 (默认: 自动生成)"
    )
    parser.add_argument(
        "--sheet", "-s",
        default=None,
        choices=["线上case", "多模态数据"],
        help="只处理指定 Sheet"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="每个 Sheet 限制处理行数"
    )

    args = parser.parse_args()

    # 检查输入文件
    if not os.path.exists(args.input):
        print(f"错误: 输入文件不存在: {args.input}")
        sys.exit(1)

    # 执行测试
    output_file = run_batch_test(
        input_file=args.input,
        output_file=args.output,
        sheet_filter=args.sheet,
        limit=args.limit,
    )

    print(f"\n结果已保存到: {output_file}")


if __name__ == "__main__":
    main()
