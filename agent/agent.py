"""XXXX 客服 Agent - 基于 Claude Agent SDK

支持:
- 本地 MCP Server (DynamoDB + Bedrock KB 后端)
- Skills 知识库 (从 .claude/skills/ 加载)

支持多模态:
- 图片 URL 自动下载并转换为 base64
"""

import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import AsyncIterator, Literal
from urllib.parse import urlparse

import httpx

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    HookMatcher,
)

from .prompts import SYSTEM_PROMPT
from .hooks import (
    MemoryManager,
    user_prompt_submit_hook,
    stop_hook,
    set_memory_manager,
)

# Memory 模式类型
MemoryMode = Literal["hook", "tool", "disabled"]

logger = logging.getLogger(__name__)


# ============================================================
# 图片处理工具函数
# ============================================================

def _get_image_media_type(url: str, content_type: str | None = None) -> str:
    """根据 URL 或 Content-Type 推断图片 MIME 类型"""
    if content_type and content_type.startswith("image/"):
        return content_type

    # 从 URL 扩展名推断
    path = urlparse(url).path.lower()
    if path.endswith(".jpg") or path.endswith(".jpeg"):
        return "image/jpeg"
    elif path.endswith(".png"):
        return "image/png"
    elif path.endswith(".gif"):
        return "image/gif"
    elif path.endswith(".webp"):
        return "image/webp"

    # 默认 JPEG
    return "image/jpeg"


async def download_image_as_base64(url: str, timeout: float = 30.0) -> dict | None:
    """
    下载图片并转换为 base64 格式

    Args:
        url: 图片 URL
        timeout: 下载超时时间（秒）

    Returns:
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": "base64_encoded_data"
            }
        }
        或 None（下载失败时）
    """
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()

            # 获取 content-type
            content_type = response.headers.get("content-type", "")
            media_type = _get_image_media_type(url, content_type)

            # 转换为 base64
            image_data = base64.b64encode(response.content).decode("utf-8")

            logger.info(f"Downloaded image: {url[:50]}... ({len(response.content)} bytes, {media_type})")

            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_data,
                }
            }
    except Exception as e:
        logger.warning(f"Failed to download image {url}: {e}")
        return None


async def download_images_as_base64(urls: list[str]) -> list[dict]:
    """
    批量下载图片并转换为 base64

    Args:
        urls: 图片 URL 列表

    Returns:
        成功下载的图片 content block 列表
    """
    tasks = [download_image_as_base64(url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    image_blocks = []
    for i, result in enumerate(results):
        if isinstance(result, dict):
            image_blocks.append(result)
        else:
            logger.warning(f"Image {i+1} download failed: {result}")

    return image_blocks

# 获取项目根目录 (用于 Skills)
PROJECT_ROOT = Path(__file__).parent.parent


class CustomerServiceAgent:
    """XXXX 客服 Agent - 基于 Claude Agent SDK

    支持:
    - 本地 MCP Server (DynamoDB + Bedrock KB 后端)
    - Skills 知识库 (渐进披露)
    - 长期记忆 (tool/hook/disabled 模式)
    """

    DEFAULT_MODEL = "global.anthropic.claude-sonnet-4-6"

    def __init__(
        self,
        parent_id: str = "parent_001",
        model: str | None = None,
        memory_id: str | None = None,
        memory_mode: MemoryMode = "tool",  # 默认使用 tool 模式
        use_skills: bool = True,  # 默认启用 Skills
    ):
        """
        初始化 Agent

        Args:
            parent_id: 当前会话的家长用户ID
            model: Claude 模型ID，默认使用 DEFAULT_MODEL
            memory_id: AgentCore Memory ID，如果不提供则从 MEMORY_ID 环境变量读取
            memory_mode: 长期记忆模式
                - "tool": (默认) Agent 通过 tool 主动检索，Stop hook 自动保存
                - "hook": 使用 Hook 自动搜索记忆并注入上下文
                - "disabled": 禁用长期记忆功能
            use_skills: 是否启用 Skills 知识库
                - True: (默认) 从 .claude/skills/ 加载 Skills
                - False: 不使用 Skills
        """
        self.parent_id = parent_id
        self.model = model or self.DEFAULT_MODEL
        self.memory_mode = memory_mode
        self.use_skills = use_skills
        self.client: ClaudeSDKClient | None = None
        self._memory_manager: MemoryManager | None = None

        # 配置 MCP Servers
        mcp_servers, allowed_tools = self._setup_mcp(memory_mode)

        # 如果启用 Skills，添加 "Skill" 到 allowed_tools
        if use_skills:
            allowed_tools = ["Skill"] + allowed_tools
            logger.info("Skills enabled - loading from .claude/skills/")

        # 配置 Memory hooks
        hooks_config = self._setup_memory(parent_id, memory_id, memory_mode)

        # 构建 Agent 选项
        options_kwargs = {
            "system_prompt": self._build_system_prompt(),
            "mcp_servers": mcp_servers,
            "allowed_tools": allowed_tools,
            "permission_mode": "bypassPermissions",
            "model": self.model,
            "hooks": hooks_config,
        }

        # Skills 配置
        if use_skills:
            options_kwargs["setting_sources"] = ["project"]
            options_kwargs["cwd"] = str(PROJECT_ROOT)

        self.options = ClaudeAgentOptions(**options_kwargs)

    def _setup_mcp(self, memory_mode: MemoryMode) -> tuple:
        """
        配置 MCP Servers

        Returns:
            (mcp_servers dict, allowed_tools list)
        """
        include_memory_tool = (memory_mode == "tool")

        from .tools.mcp_tools import (
            create_mcp_server,
            TOOLS,
            TOOLS_WITH_MEMORY,
        )

        mcp_servers = {
            "tools": create_mcp_server(include_memory_tools=include_memory_tool),
        }
        allowed_tools = (
            TOOLS_WITH_MEMORY if include_memory_tool
            else TOOLS
        )

        logger.info("Using local MCP server (DynamoDB + Bedrock KB backend)")

        return mcp_servers, allowed_tools

    def _setup_memory(
        self,
        parent_id: str,
        memory_id: str | None,
        memory_mode: MemoryMode,
    ) -> dict | None:
        """
        配置 Memory hooks

        Returns:
            hooks_config or None
        """
        hooks_config = None

        if memory_mode == "disabled":
            logger.info("Memory mode: disabled")
            return None

        # 尝试从环境变量获取 memory_id
        effective_memory_id = memory_id or os.getenv("MEMORY_ID")

        if not effective_memory_id:
            logger.warning("No memory_id provided - memory features disabled")
            return None

        self._memory_manager = MemoryManager(
            actor_id=parent_id,
            memory_id=effective_memory_id,
        )

        if not self._memory_manager.is_enabled:
            logger.warning("Memory manager not enabled - memory features disabled")
            return None

        # 设置全局内存管理器
        set_memory_manager(self._memory_manager)

        if memory_mode == "hook":
            # Hook 模式：自动搜索记忆 + 自动保存对话
            hooks_config = {
                "UserPromptSubmit": [
                    HookMatcher(matcher=None, hooks=[user_prompt_submit_hook]),
                ],
                "Stop": [
                    HookMatcher(matcher=None, hooks=[stop_hook]),
                ],
            }
            logger.info("Memory mode: hook (auto search + auto save)")

        elif memory_mode == "tool":
            # Tool 模式：Agent 主动搜索，仅 Stop hook 自动保存
            hooks_config = {
                "Stop": [
                    HookMatcher(matcher=None, hooks=[stop_hook]),
                ],
            }
            logger.info("Memory mode: tool (agent-driven search + auto save)")

        return hooks_config

    def _build_system_prompt(self) -> str:
        """构建包含上下文的 system prompt"""
        # @prompt: session_context
        context_info = f"""
# 当前会话上下文
- 家长用户ID: {self.parent_id}
- 所有 tool 调用时请使用此 parent_id

"""
        # 如果是 tool 模式，添加长期记忆使用指引
        memory_guidance = ""
        if self.memory_mode == "tool":
            # @prompt: memory_tool_guidance
            memory_guidance = """
# 长期记忆使用指引
你有两个记忆工具可以检索用户的历史信息:

## search_user_preferences - 搜索用户偏好
用于查找用户的个人偏好设置，包括:
- 喜欢的老师（如：用户偏好王老师）
- 常用上课时间（如：通常约周六上午10点）
- 课程偏好（如：偏好1对1课程）
- 沟通偏好（如：偏好中文沟通）

**何时使用**:
- 用户说"帮我约上次那个老师"
- 用户说"和之前一样的时间"
- 用户说"还是老规矩"
- 需要个性化推荐时

## search_episodic_memories - 搜索情景记忆
用于查找用户的历史交互和操作记录，包括:
- 历史预约记录（如：上周约了周三的课）
- 取消/调课记录（如：之前取消过周五的课）
- 问题解决历史（如：之前反馈过音频问题，已解决）
- 投诉和反馈记录

**何时使用**:
- 用户说"我之前取消的那节课"
- 用户说"上次预约的情况"
- 用户问"之前那个问题解决了吗"
- 需要了解用户历史操作时

**注意**：不需要每次都检索，只在用户提到"之前"、"上次"等历史信息时使用。

"""

        # 如果启用 Skills，添加使用指引
        # @prompt: skills_guidance
        skills_guidance = ""
        if self.use_skills:
            # @prompt: skills_guidance
            skills_guidance = """
# 知识库 Skills 使用指引
系统已加载 XXXX 知识库 Skills，包含课程产品、操作指南、FAQ 等信息。
当用户询问相关问题时，系统会自动加载对应的 Skill 来回答。

可用的 Skills 包括:
- course-products: 课程产品知识
- course-operations: 课程操作指南
- payment-refund: 支付与退款
- credits-points: 课时与积分规则
- user-faq-*: 用户常见问题
- 等等

"""

        return context_info + memory_guidance + skills_guidance + SYSTEM_PROMPT

    async def connect(self):
        """建立连接"""
        self.client = ClaudeSDKClient(options=self.options)
        await self.client.connect()

    async def disconnect(self):
        """断开连接"""
        if self.client:
            await self.client.disconnect()
            self.client = None

    async def reset(self):
        """重置对话（断开并重新连接）"""
        await self.disconnect()
        await self.connect()

    def _build_text_message(
        self,
        user_message: str,
        conversation_history: str | None = None,
    ) -> str:
        """
        构建纯文本消息（不含图片）

        Args:
            user_message: 当前用户消息
            conversation_history: 历史对话上下文（可选）

        Returns:
            构建好的完整消息
        """
        parts = []

        # 添加历史上下文
        if conversation_history:
            parts.append(f"<conversation_history>\n{conversation_history}\n</conversation_history>")

        # 添加当前用户消息
        if conversation_history:
            parts.append(f"用户当前消息: {user_message}")
        else:
            parts.append(user_message)

        return "\n\n".join(parts)

    async def _build_multimodal_generator(
        self,
        user_message: str,
        conversation_history: str | None = None,
        image_urls: list[str] | None = None,
    ):
        """
        构建多模态消息生成器（支持图片）

        SDK 支持通过 generator 传入 structured content blocks，
        包含 text 和 base64 编码的 image。

        Args:
            user_message: 当前用户消息
            conversation_history: 历史对话上下文（可选）
            image_urls: 图片URL列表（可选）

        Yields:
            消息 content blocks
        """
        content_blocks = []

        # 1. 下载并转换图片为 base64
        if image_urls:
            logger.info(f"Downloading {len(image_urls)} images...")
            image_blocks = await download_images_as_base64(image_urls)
            content_blocks.extend(image_blocks)
            logger.info(f"Successfully downloaded {len(image_blocks)} images")

        # 2. 构建文本部分
        text_content = self._build_text_message(user_message, conversation_history)

        # 如果有图片，添加说明
        if image_urls:
            text_content = f"[用户发送了 {len(image_urls)} 张图片]\n\n{text_content}"

        content_blocks.append({"type": "text", "text": text_content})

        # 3. 使用 generator 格式 yield 消息
        yield {
            "type": "user",
            "message": {
                "role": "user",
                "content": content_blocks,
            }
        }

    async def chat(
        self,
        user_message: str,
        conversation_history: str | None = None,
        images: list[str] | None = None,
    ) -> str:
        """
        处理用户消息并返回回复

        Args:
            user_message: 用户输入的消息
            conversation_history: 历史对话上下文（可选）
            images: 图片URL列表（可选，将自动下载并转为base64）

        Returns:
            Agent 的回复
        """
        if not self.client:
            await self.connect()

        if self.client is None:
            raise RuntimeError("Failed to connect to Claude Agent SDK")

        # 根据是否有图片选择消息格式
        if images:
            # 使用 generator 格式传入多模态内容（预下载图片转 base64）
            generator = self._build_multimodal_generator(
                user_message,
                conversation_history=conversation_history,
                image_urls=images,
            )
            await self.client.query(generator)
        else:
            # 纯文本消息
            full_message = self._build_text_message(
                user_message,
                conversation_history=conversation_history,
            )
            await self.client.query(full_message)

        # 收集响应
        response_text = ""
        async for message in self.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text

        return response_text

    async def chat_stream(
        self,
        user_message: str,
        conversation_history: str | None = None,
        images: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """
        流式处理用户消息

        Args:
            user_message: 用户输入的消息
            conversation_history: 历史对话上下文（可选）
            images: 图片URL列表（可选，将自动下载并转为base64）

        Yields:
            响应文本片段
        """
        if not self.client:
            await self.connect()

        if self.client is None:
            raise RuntimeError("Failed to connect to Claude Agent SDK")

        # 根据是否有图片选择消息格式
        if images:
            # 使用 generator 格式传入多模态内容（预下载图片转 base64）
            generator = self._build_multimodal_generator(
                user_message,
                conversation_history=conversation_history,
                image_urls=images,
            )
            await self.client.query(generator)
        else:
            # 纯文本消息
            full_message = self._build_text_message(
                user_message,
                conversation_history=conversation_history,
            )
            await self.client.query(full_message)

        response_text_parts = []  # Collect full response for memory buffer
        output_messages = []      # For I/O summary log
        pending_text_blocks = []  # Text blocks before a tool call
        tool_use_records = []     # Tool use block records

        # Pre-set the user prompt in the buffer so stop hook has it even if
        # it fires before the generator finishes (stop hook fires from SDK
        # internal processing, before the async generator's final code runs).
        if self._memory_manager:
            self._memory_manager.set_last_turn(
                user_prompt=user_message,
                assistant_response="",
            )

        # Emit bedrock-format log: current user input
        from .observability import emit_bedrock_log, emit_structured_log, \
            capture_tool_span_context, _sanitize
        emit_bedrock_log(
            {"content": [{"text": _sanitize(user_message)}]},
            "gen_ai.user.message",
        )

        async for message in self.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text_parts.append(block.text)
                        pending_text_blocks.append(block.text)
                        # Update memory buffer incrementally so stop hook
                        # always has the latest assistant response
                        if self._memory_manager:
                            self._memory_manager.set_last_turn(
                                user_prompt=user_message,
                                assistant_response=" ".join(response_text_parts),
                            )
                        yield block.text

                    elif isinstance(block, ToolUseBlock):
                        # Capture span context for post_tool_use_hook
                        if block.id:
                            capture_tool_span_context(block.id)

                        plain_name = block.name.split("__")[-1] if "__" in block.name else block.name
                        tool_input = block.input if hasattr(block, "input") else {}

                        # Build content array with text + toolUse
                        assistant_content = []
                        for t in pending_text_blocks:
                            assistant_content.append({"text": t})
                        assistant_content.append({"toolUse": {
                            "name": plain_name,
                            "input": tool_input,
                            "toolUseId": block.id,
                        }})

                        tool_call_entry = {
                            "type": "function",
                            "id": block.id,
                            "function": {"name": plain_name, "arguments": tool_input},
                        }

                        # Bedrock-format logs
                        emit_bedrock_log({
                            "content": assistant_content,
                            "tool_calls": [tool_call_entry],
                        }, "gen_ai.assistant.message")
                        emit_bedrock_log({
                            "message": {"tool_calls": [tool_call_entry], "role": "assistant"},
                            "index": 0,
                            "finish_reason": "tool_use",
                        }, "gen_ai.choice")

                        # Track for I/O summary
                        tool_use_records.append({
                            "id": block.id,
                            "name": plain_name,
                            "preceding_text": list(pending_text_blocks),
                        })
                        import json as _json
                        output_messages.append({
                            "content": {"content": _json.dumps(assistant_content)},
                            "role": "assistant",
                        })
                        pending_text_blocks.clear()

                        yield f"\n[调用工具: {block.name}]\n"

        # Emit final text response logs
        if pending_text_blocks:
            for t in pending_text_blocks:
                emit_bedrock_log({"content": [{"text": t}]}, "gen_ai.assistant.message")
                emit_bedrock_log({
                    "message": {"content": [{"text": t}], "role": "assistant"},
                    "index": 0,
                    "finish_reason": "end_turn",
                }, "gen_ai.choice")
            import json as _json
            output_messages.append({
                "content": {
                    "message": _json.dumps([{"text": t} for t in pending_text_blocks]),
                    "finish_reason": "end_turn",
                },
                "role": "assistant",
            })
            pending_text_blocks.clear()

        # Emit structured I/O summary log (the key log for evaluations)
        import json as _json
        input_messages = [{
            "content": {"content": _json.dumps([{"text": _sanitize(user_message)}])},
            "role": "user",
        }]
        emit_structured_log({
            "output": {"messages": output_messages},
            "input": {"messages": input_messages},
        })

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.connect()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
        """异步上下文管理器退出"""
        await self.disconnect()


# ============================================================
# 同步包装器（方便非异步环境使用）
# ============================================================

class CustomerServiceAgentSync:
    """同步版本的 XXXX Agent（包装异步实现）

    注意: 此包装器创建独立的事件循环，不应在已有异步上下文中使用。
    在异步环境中请直接使用 CustomerServiceAgent。
    """

    def __init__(
        self,
        parent_id: str = "parent_001",
        model: str | None = None,
        memory_id: str | None = None,
        memory_mode: MemoryMode = "tool",
        use_skills: bool = True,
    ):
        self.agent = CustomerServiceAgent(
            parent_id=parent_id,
            model=model,
            memory_id=memory_id,
            memory_mode=memory_mode,
            use_skills=use_skills,
        )
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """获取或创建事件循环"""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def chat(self, user_message: str) -> str:
        """同步版本的 chat"""
        return self._get_loop().run_until_complete(self.agent.chat(user_message))

    def reset(self):
        """重置对话"""
        self._get_loop().run_until_complete(self.agent.reset())

    def close(self):
        """关闭连接和事件循环"""
        if self._loop and not self._loop.is_closed():
            self._loop.run_until_complete(self.agent.disconnect())
            self._loop.close()
            self._loop = None

    def __enter__(self):
        self._get_loop().run_until_complete(self.agent.connect())
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        self.close()


# ============================================================
# 便捷函数
# ============================================================

async def quick_chat(
    user_message: str,
    parent_id: str = "parent_001",
    model: str | None = None,
    memory_id: str | None = None,
    memory_mode: MemoryMode = "tool",
    use_skills: bool = True,
) -> str:
    """
    快速单次对话（无状态）

    Args:
        user_message: 用户消息
        parent_id: 家长用户ID
        model: Claude 模型ID
        memory_id: AgentCore Memory ID
        memory_mode: 长期记忆模式 ("hook" | "tool" | "disabled")
        use_skills: 是否启用 Skills 知识库

    Returns:
        Agent 回复
    """
    async with CustomerServiceAgent(
        parent_id=parent_id,
        model=model,
        memory_id=memory_id,
        memory_mode=memory_mode,
        use_skills=use_skills,
    ) as agent:
        return await agent.chat(user_message)
