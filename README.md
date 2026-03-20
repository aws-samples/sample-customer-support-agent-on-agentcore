# Customer Service Agent

[English](README_EN.md) | 中文

基于 **Claude Agent SDK** 实现的客服 Agent Demo。该Repo以在线教育作为实现场景。

## 项目结构

```
/
├── agent/
│   ├── __init__.py
│   ├── agent.py                 # 主 Agent (Claude Agent SDK)
│   ├── hooks/                   # 长期记忆 Hooks
│   │   ├── memory_manager.py    # AgentCore Memory 管理器 (V2 shared episodic)
│   │   └── memory_hooks.py      # UserPromptSubmit/Stop 钩子
│   ├── prompts/
│   │   └── system_prompt.py     # System Prompt 定义
│   ├── tools/
│   │   ├── db.py                # DynamoDB 共享客户端
│   │   ├── mcp_tools.py         # MCP Tools 定义 (SDK 格式)
│   │   ├── knowledge_search.py  # 知识库检索 (Bedrock KB)
│   │   ├── booking_operations.py# 课程操作 (DynamoDB)
│   │   ├── account_query.py     # 账户查询 (DynamoDB)
│   │   └── timezone_utils.py    # 时区工具 (DynamoDB)
│   ├── dispatcher/              # WeChat 消息调度器
│   ├── runtime/
│   │   └── entrypoint.py        # AgentCore Runtime 入口
│   ├── observability.py         # Manual OTEL SDK (AgentCore Evaluations 兼容)
├── docs/
│   ├── 服务FAQ与操作指南.md      # KB 文档: FAQ
│   └── 课程与品牌介绍.md         # KB 文档: 课程介绍
├── scripts/
│   ├── setup_mock_infra.py      # 基础设施初始化 (DynamoDB + Bedrock KB)
│   ├── setup_memory.py          # AgentCore Memory 初始化
│   └── deploy_agentcore.sh      # AgentCore Runtime 部署
├── demo_server.py               # FastAPI Demo Server (WebSocket + Dispatcher)
├── run_agent.py                 # 测试 CLI
├── Dockerfile                   # AgentCore Runtime 容器
└── README.md
```

## 功能特性

### 支持的操作

| 功能 | 描述 | 后端 |
|------|------|------|
| FAQ 问答 | 基于知识库回答常见问题 | Bedrock Knowledge Base |
| 课时余额查询 | 查询各课程的剩余课时 | DynamoDB |
| 打卡次数查询 | 查询剩余打卡次数 | DynamoDB |
| 积分余额查询 | 查询账户积分 | DynamoDB |
| 课程安排查询 | 查询未来的课程安排 | DynamoDB |
| 单节约课 | 预约新课程 | DynamoDB |
| 单节取消课 | 取消已约课程 | DynamoDB |
| 单节调课 | 调整课程时间 | DynamoDB |
| 长期记忆 | 跨会话记忆用户偏好和历史交互 | AgentCore Memory |

### 敏感场景处理

以下场景自动转人工处理：
- 退款/退费/投诉
- 购买/续费/支付
- 价格/优惠咨询
- 抽奖/奖品发放

## 前置条件

- Python 3.11+
- AWS 账户，配置好 Credentials

## 部署指南 (从零开始)

### Step 1: 安装依赖

```bash
uv sync
# 或
pip install -e .
```

### Step 2: 配置 AWS Credentials

```bash
export AWS_REGION=us-west-2

# 方式1: 环境变量
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"

# 方式2: AWS CLI
aws configure
```

### Step 3: 创建基础设施 (DynamoDB + Bedrock KB)

```bash
python scripts/setup_mock_infra.py --region us-west-2
```

此脚本会自动：
1. 创建 3 个 DynamoDB 表 (`xxxx-demo-bookings`, `xxxx-demo-accounts`, `xxxx-demo-slots`)
2. 向表中写入测试用户数据 (parent_001 张先生)
3. 创建 S3 桶，上传 `docs/` 下的 KB 文档
4. 创建 OpenSearch Serverless 集合 + 向量索引
5. 创建 Bedrock Knowledge Base + S3 数据源，启动同步
6. 将 `DYNAMODB_TABLE_PREFIX`、`KNOWLEDGE_BASE_ID`、`AWS_REGION` 保存到 `.env`

> **依赖**: 需要安装 `opensearch-py` 用于创建向量索引。
>
> **耗时**: 首次运行约 3-5 分钟 (AOSS collection 创建需要等待)。
>
> **跳过部分资源**: `--skip-dynamodb` 或 `--skip-kb`。

### Step 4: 创建 AgentCore Memory

```bash
python scripts/setup_memory.py --region us-west-2 --use-boto3 --name XXXXDemoMemory
```

脚本会创建 Memory 并添加 3 个 Strategy (Semantic / UserPreference / Episodic)，将 `MEMORY_ID` 保存到 `.env`。

> 如果脚本因超时退出但 Memory 已创建，手动添加策略：
> ```python
> import boto3
> client = boto3.client('bedrock-agentcore-control', region_name='us-west-2')
> client.update_memory(
>     memoryId='YOUR_MEMORY_ID',
>     memoryStrategies={'addMemoryStrategies': [
>         {'semanticMemoryStrategy': {'name': 'XXXX_semantic', 'description': 'Semantic memory', 'namespaces': ['/semantic/{actorId}/']}},
>         {'userPreferenceMemoryStrategy': {'name': 'XXXX_preferences', 'description': 'User preferences', 'namespaces': ['/users/{actorId}/preferences/']}},
>         {'episodicMemoryStrategy': {'name': 'XXXX_episodic', 'description': 'Episodic memory', 'namespaces': ['/strategies/{memoryStrategyId}/actors/{actorId}/sessions/{sessionId}/'], 'reflectionConfiguration': {'namespaces': ['/strategies/{memoryStrategyId}/actors/{actorId}/']}}},
>     ]},
> )
> ```


### Step 5: 本地运行

```bash
# 默认用户
python run_agent.py

# 指定用户 ID
python run_agent.py --parent-id parent_001

# 禁用 Skills
python run_agent.py --no-skills

# 禁用记忆
python run_agent.py --memory-mode disabled
```

CLI 命令:
- 输入消息后回车发送
- `reset` - 重置对话上下文
- `quit` / `exit` / `q` - 退出

### Step 6: 部署到 AgentCore Runtime (生产)

#### 6a. 替换配置占位符

代码中使用 `<PLACEHOLDER>` 标记了需要用户根据自己环境替换的值。部署前请全局搜索并替换以下占位符：

| 占位符 | 说明 | 如何获取 |
|--------|------|----------|
| `<ACCOUNT_ID>` | AWS 账户 ID (12位数字) | `aws sts get-caller-identity --query Account --output text` |
| `<REGION>` | AWS 区域 | 如 `us-west-2`，与部署区域一致 |
| `<RUNTIME_ID>` | AgentCore Runtime ID | 创建 Runtime 后从控制台或 CLI 获取，格式如 `your_agent_name-AbCdEfGhIj` |
| `<ROLE_SUFFIX>` | IAM Role 后缀 | 创建 Runtime 时自动生成的 Role 的后缀部分 |
| `<ECR_REPO>` | ECR 仓库名 | `aws ecr create-repository --repository-name your-agent` 创建 |
| `<MEMORY_ID>` | AgentCore Memory ID | Step 4 创建 Memory 后获取，或从 `.env` 读取 |
| `<KNOWLEDGE_BASE_ID>` | Bedrock KB ID | Step 3 创建 KB 后获取，或从 `.env` 读取 |
| `<TEST_PARENT_ID>` | 测试用户 ID | Step 3 写入 DynamoDB 的用户 ID，默认 `parent_001` |

**VPC 模式额外配置** (仅在需要固定出口 IP 时):

| 占位符 | 说明 | 如何获取 |
|--------|------|----------|
| `<VPC_ID>` | VPC ID | `aws ec2 describe-vpcs --query 'Vpcs[0].VpcId'` |
| `<SUBNET_1>`, `<SUBNET_2>` | 私有子网 ID | 需要有 NAT Gateway 路由的子网 |
| `<SECURITY_GROUP>` | 安全组 ID | 需允许出站全部流量 + 同安全组入站 |
| `<NAT_GATEWAY_IP>` | NAT Gateway 弹性 IP | 用于 MCP 服务器白名单 |

**需要替换的文件：**

```bash
# 快速查找所有占位符
grep -rn '<ACCOUNT_ID>\|<RUNTIME_ID>\|<ECR_REPO>\|<MEMORY_ID>\|<KNOWLEDGE_BASE_ID>' \
    --include="*.py" --include="*.sh" --include="*.md" --include="Dockerfile"
```

主要文件：
- `Dockerfile` — `AGENTCORE_RUNTIME_ID`, `MEMORY_ID`, `KNOWLEDGE_BASE_ID`
- `scripts/deploy_agentcore.sh` — `ACCOUNT_ID`, `RUNTIME_ID`, `ROLE_SUFFIX`, `ECR_REPO`, VPC 相关
- `scripts/batch_test.py` — `RUNTIME_ARN`, `PARENT_ID`
- `scripts/run_evaluation.py` — `RUNTIME_ARN`, `RUNTIME_ID`, `PARENT_ID`

#### 6b. 使用 AgentCore Starter Toolkit 部署

推荐使用 [AgentCore Starter Toolkit](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-getting-started.html) 一键创建和部署 Runtime：

```bash
# 安装 Starter Toolkit
pip install bedrock-agentcore-starter-toolkit

# 初始化项目配置 (生成 .bedrock_agentcore.yaml)
agentcore init

# 启动部署 (自动构建 Docker 镜像、推送 ECR、创建/更新 Runtime)
agentcore launch
```

`agentcore launch` 会自动完成：
1. 构建 ARM64 Docker 镜像
2. 创建 ECR 仓库并推送镜像
3. 创建 AgentCore Runtime（如果不存在）或更新到新版本
4. 等待 Runtime 状态变为 READY

部署完成后，Runtime ID 会保存在 `.bedrock_agentcore.yaml` 中。

> **注意**: 部署前请确认 `Dockerfile` 中的环境变量已正确设置：
> ```dockerfile
> ENV DYNAMODB_TABLE_PREFIX=xxxx-demo
> ENV MEMORY_ID=<your-memory-id>          # 从 .env 获取
> ENV KNOWLEDGE_BASE_ID=<your-kb-id>      # 从 .env 获取
> ENV AGENTCORE_RUNTIME_ID=<your-runtime-id>  # 首次部署后回填
> ```

#### 6c. 添加 IAM 权限

AgentCore Runtime IAM Role 需要以下额外权限：

```bash
# DynamoDB + Bedrock KB 访问权限
aws iam put-role-policy \
    --role-name AmazonBedrockAgentCoreSDKRuntime-<REGION>-<ROLE_SUFFIX> \
    --policy-name DynamoDBAndKBAccess \
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DynamoDBAccess",
                "Effect": "Allow",
                "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem",
                           "dynamodb:DeleteItem","dynamodb:Query","dynamodb:Scan",
                           "dynamodb:BatchWriteItem","dynamodb:BatchGetItem"],
                "Resource": ["arn:aws:dynamodb:us-west-2:<ACCOUNT_ID>:table/xxxx-demo-*"]
            },
            {
                "Sid": "BedrockKBAccess",
                "Effect": "Allow",
                "Action": ["bedrock:Retrieve","bedrock:RetrieveAndGenerate"],
                "Resource": ["arn:aws:bedrock:us-west-2:<ACCOUNT_ID>:knowledge-base/*"]
            }
        ]
    }'

# AgentCore Memory 访问权限 (如果启用了 Memory)
aws iam put-role-policy \
    --role-name AmazonBedrockAgentCoreSDKRuntime-<REGION>-<ROLE_SUFFIX> \
    --policy-name AgentCoreMemoryAccess \
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:CreateEvent","bedrock-agentcore:SearchMemory",
                       "bedrock-agentcore:GetMemory","bedrock-agentcore:ListMemories",
                       "bedrock-agentcore:RetrieveMemory","bedrock-agentcore:InvokeMemory",
                       "bedrock-agentcore:CreateMemoryEvent"],
            "Resource": ["arn:aws:bedrock-agentcore:us-west-2:<ACCOUNT_ID>:memory/*"]
        }]
    }'
```

#### 6d. 更新部署 (后续迭代)

代码修改后重新部署：

```bash
# 方式1: Starter Toolkit (推荐)
agentcore launch

# 方式2: 手动部署脚本
./scripts/deploy_agentcore.sh          # 公网模式
./scripts/deploy_agentcore.sh --vpc    # VPC 模式 (固定出口 IP)
```

#### 6e. 验证

```bash
# 测试调用
PAYLOAD=$(echo -n '{"prompt": "你好", "parent_id": "parent_001"}' | base64)
SESSION_ID=$(uuidgen)-$(date +%s)
aws bedrock-agentcore invoke-agent-runtime \
    --agent-runtime-arn "arn:aws:bedrock-agentcore:us-west-2:<ACCOUNT_ID>:runtime/<RUNTIME_ID>" \
    --runtime-session-id "$SESSION_ID" \
    --payload "$PAYLOAD" \
    --region us-west-2 \
    /tmp/output.json && cat /tmp/output.json

# 查看 CloudWatch 日志
aws logs tail "/aws/bedrock-agentcore/runtimes/<RUNTIME_ID>-DEFAULT" \
    --region us-west-2 --since 10m --format short
```

## 在代码中使用

### 异步

```python
import asyncio
from agent import CustomerServiceAgent

async def main():
    async with CustomerServiceAgent(parent_id="parent_001") as agent:
        response = await agent.chat("帮我查一下还有多少课时")
        print(response)

asyncio.run(main())
```

### 同步

```python
from agent import CustomerServiceAgentSync

with CustomerServiceAgentSync(parent_id="parent_001") as agent:
    response = agent.chat("帮我查一下还有多少课时")
    print(response)
```

### 快速单次对话

```python
import asyncio
from agent import quick_chat

response = asyncio.run(quick_chat("帮我查一下还有多少课时"))
print(response)
```

## API 参考

### CustomerServiceAgent

```python
class CustomerServiceAgent:
    DEFAULT_MODEL = "global.anthropic.claude-sonnet-4-6"

    def __init__(
        self,
        parent_id: str = "parent_001",
        model: str | None = None,
        memory_id: str | None = None,
        memory_mode: MemoryMode = "tool",   # "tool" | "hook" | "disabled"
        use_skills: bool = True,
    ): ...

    async def connect(self): ...
    async def disconnect(self): ...
    async def reset(self): ...
    async def chat(self, message: str) -> str: ...
    async def chat_stream(self, message: str, conversation_history: str = None, images: list[str] = None) -> AsyncIterator[str]: ...
```

### Memory 模式

| 模式 | 搜索 | 保存 | 说明 |
|------|------|------|------|
| `tool` (默认) | Agent 主动调用 `search_user_preferences` / `search_episodic_memories` | Stop hook 自动保存 | 推荐 |
| `hook` | `UserPromptSubmit` hook 自动搜索 semantic + preferences | Stop hook 自动保存 | |
| `disabled` | 无 | 无 | 测试/隐私场景 |

## 测试数据

Setup 脚本会向 DynamoDB 写入以下测试用户：

- **parent_001**: 张先生
  - 学生：小明 (STU001)、小红 (STU002)
  - 课时：中文标准版 24课时、新加坡数学 12课时
  - 打卡次数：中文 3次、数学 2次
  - 积分：1580分
  - 时区：Asia/Shanghai
  - 预约：BK001 (小明/王老师/中文)、BK002 (小明/王老师/中文)、BK003 (小红/李老师/数学)

## AWS 资源清单

部署后创建的资源：

| 资源类型 | 名称 / 前缀 |
|----------|-------------|
| DynamoDB Table | `xxxx-demo-bookings` |
| DynamoDB Table | `xxxx-demo-accounts` |
| DynamoDB Table | `xxxx-demo-slots` |
| S3 Bucket | `xxxx-demo-kb-<account-id>` |
| OpenSearch Serverless | `xxxx-demo-kb` |
| Bedrock Knowledge Base | `xxxx-demo-kb` |
| AgentCore Memory | `XXXXDemoMemory-*` |
| IAM Role (KB) | `BedrockKBRole_xxxx_demo` |
| AgentCore Runtime | `xxxx_chatbot_agent` |
| ECR Repository | `<ECR_REPO>` |

## 环境变量

| 变量 | 说明 | 来源 |
|------|------|------|
| `CLAUDE_CODE_USE_BEDROCK` | 启用 Bedrock | 手动设置 `1` |
| `AWS_REGION` | AWS 区域 | `setup_mock_infra.py` 写入 `.env` |
| `DYNAMODB_TABLE_PREFIX` | DynamoDB 表前缀 | `setup_mock_infra.py` 写入 `.env` |
| `KNOWLEDGE_BASE_ID` | Bedrock KB ID | `setup_mock_infra.py` 写入 `.env` |
| `MEMORY_ID` | AgentCore Memory ID | `setup_memory.py` 写入 `.env` |

## AgentCore Evaluations (质量评估)

部署到 AgentCore Runtime 后，可以使用 AgentCore Evaluations 的 13 个内置评估器对 Agent 回答质量进行评分。

### 工作原理

Agent 在处理每个请求时，通过 `observability.py` 中的手动 OTEL SDK 将 spans 和 events 写入 CloudWatch：

```
Agent 处理请求
├── Span → X-Ray → aws/spans (invoke_agent, memory.save_turn)
└── Events → CloudWatch Logs → otel-rt-logs (gen_ai.user.message, gen_ai.choice, ...)
              ↓
    Evaluate API 读取这些数据进行评分
```

### 前置条件

1. **启用 CloudWatch Transaction Search** (一次性):

```bash
aws logs put-resource-policy \
    --policy-name AgentCoreXRayAccess \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Sid":"AllowXRayToWriteLogs","Effect":"Allow","Principal":{"Service":"xray.amazonaws.com"},"Action":"logs:PutLogEvents","Resource":"arn:aws:logs:us-west-2:<ACCOUNT_ID>:log-group:aws/spans:*"}]}'

aws xray update-trace-segment-destination --destination CloudWatchLogs --region us-west-2
```

2. **创建 otel-rt-logs 日志流** (一次性):

```bash
aws logs create-log-stream \
    --log-group-name "/aws/bedrock-agentcore/runtimes/<RUNTIME_ID>-DEFAULT" \
    --log-stream-name "otel-rt-logs" \
    --region us-west-2
```

3. **提高 X-Ray Indexing 比例** (可选，开发/测试环境):

```bash
# 默认只索引 1%，dev/test 环境建议 100%
aws xray update-indexing-rule --name "Default" \
    --rule '{"Probabilistic": {"DesiredSamplingPercentage": 100}}' \
    --region us-west-2
```

### 使用方法

#### Step 1: 调用 Agent (生成 trace 数据)

```python
import json, uuid, boto3

client = boto3.client("bedrock-agentcore", region_name="us-west-2")
session_id = f"eval-{uuid.uuid4()}-{int(__import__('time').time())}"

payload = json.dumps({
    "prompt": "我想预约一节中文课",
    "parent_id": "<TEST_PARENT_ID>",
    "session_id": session_id,
}, ensure_ascii=False)

response = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-west-2:<ACCOUNT_ID>:runtime/<RUNTIME_ID>",
    runtimeSessionId=session_id,
    payload=payload.encode("utf-8"),
)
body = response["response"].read().decode("utf-8")
print(f"Session ID: {session_id}")
print(f"Response: {body[:200]}")
```

> **注意**: 调用后需等待约 2 分钟，让 spans 和 events 出现在 CloudWatch 中。

#### Step 2: 从 CloudWatch 下载 spans + events

```python
import json, time, boto3
from datetime import datetime, timedelta, timezone

REGION = "us-west-2"
AGENT_ID = "<RUNTIME_ID>"
SESSION_ID = "your-session-id-from-step-1"

logs = boto3.client("logs", region_name=REGION)
query = f"""fields @timestamp, @message
| filter ispresent(scope.name) and ispresent(attributes.session.id)
| filter attributes.session.id = "{SESSION_ID}"
| sort @timestamp asc"""

items = []
for log_group in ["aws/spans", f"/aws/bedrock-agentcore/runtimes/{AGENT_ID}-DEFAULT"]:
    r = logs.start_query(
        logGroupName=log_group,
        startTime=int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()),
        endTime=int(datetime.now(timezone.utc).timestamp()),
        queryString=query,
    )
    query_id = r["queryId"]
    while True:
        result = logs.get_query_results(queryId=query_id)
        if result["status"] == "Complete":
            break
        time.sleep(1)
    for row in result.get("results", []):
        for field in row:
            if field["field"] == "@message" and field["value"].strip().startswith("{"):
                items.append(json.loads(field["value"]))

print(f"Downloaded {len(items)} items (spans + events)")
```

#### Step 3: 运行评估器

```python
client = boto3.client("bedrock-agentcore", region_name=REGION)

# 单个评估器
response = client.evaluate(
    evaluatorId="Builtin.Helpfulness",
    evaluationInput={"sessionSpans": items},
)
for r in response["evaluationResults"]:
    print(f"Score: {r.get('value'):.2f}, Label: {r.get('label')}")
    print(f"Explanation: {r.get('explanation', '')[:200]}")
```

```python
# 批量运行所有评估器
EVALUATORS = [
    "Builtin.Coherence", "Builtin.Conciseness", "Builtin.Correctness",
    "Builtin.Faithfulness", "Builtin.GoalSuccessRate", "Builtin.Harmfulness",
    "Builtin.Helpfulness", "Builtin.InstructionFollowing", "Builtin.Refusal",
    "Builtin.ResponseRelevance", "Builtin.Stereotyping",
    "Builtin.ToolParameterAccuracy", "Builtin.ToolSelectionAccuracy",
]

for evaluator_id in EVALUATORS:
    response = client.evaluate(
        evaluatorId=evaluator_id,
        evaluationInput={"sessionSpans": items},
    )
    for r in response["evaluationResults"]:
        score = r.get("value")
        label = r.get("label", "")
        error = r.get("errorMessage", "")
        name = evaluator_id.replace("Builtin.", "")
        if error:
            print(f"  {name:25s} Error: {error[:60]}")
        elif score is not None:
            print(f"  {name:25s} Score={score:.2f} Label=\"{label}\"")
```

### 评估器说明

| 评估器 | 级别 | 说明 |
|--------|------|------|
| Coherence | Trace | 回答是否连贯、有逻辑 |
| Conciseness | Trace | 回答是否简洁 |
| Correctness | Trace | 回答是否正确 |
| Faithfulness | Trace | 回答是否忠实于上下文 |
| GoalSuccessRate | Session | 是否成功完成用户目标 |
| Harmfulness | Trace | 回答是否有害 |
| Helpfulness | Trace | 回答是否有帮助 |
| InstructionFollowing | Trace | 是否遵循指令 |
| Refusal | Trace | 是否拒绝回答 |
| ResponseRelevance | Trace | 回答是否与问题相关 |
| Stereotyping | Trace | 是否存在刻板印象 |
| ToolParameterAccuracy | Tool | 工具参数是否正确 |
| ToolSelectionAccuracy | Tool | 工具选择是否正确 |

### 技术实现

本项目通过手动 OTEL SDK 实现了与 Strands Agents SDK 相同格式的 trace 输出，使得 AgentCore Evaluations 能够解析 Claude Agent SDK 的 trace 数据。

关键实现文件:
- `agent/observability.py` — 手动 OTEL SDK (TracerProvider + LoggerProvider + CloudWatch 直写)
- `agent/agent.py` `chat_stream()` — 在响应流中发射 bedrock-format log events
- `agent/runtime/entrypoint.py` — 启动时调用 `init_otel()`

详细实现说明见 `CLAUDE.md` 的 Observability 章节。


## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.


