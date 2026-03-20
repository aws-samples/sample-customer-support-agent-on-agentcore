# Customer Service Agent

English | [中文](README.md)

A customer service agent demo built with **Claude Agent SDK**, deployed on **Amazon Bedrock AgentCore Runtime**. This repo uses online education as the implementation scenario.

## Project Structure

```
sample-customer-support-agent-on-agentcore/
├── agent/
│   ├── __init__.py
│   ├── agent.py                 # Main Agent (Claude Agent SDK)
│   ├── hooks/                   # Long-term Memory Hooks
│   │   ├── memory_manager.py    # AgentCore Memory Manager
│   │   └── memory_hooks.py      # UserPromptSubmit/Stop Hooks
│   ├── prompts/
│   │   └── system_prompt.py     # System Prompt
│   ├── tools/
│   │   ├── db.py                # DynamoDB Client
│   │   ├── mcp_tools.py         # MCP Tools (SDK format)
│   │   ├── knowledge_search.py  # Knowledge Base Search (Bedrock KB)
│   │   ├── booking_operations.py# Class Booking Operations (DynamoDB)
│   │   ├── account_query.py     # Account Query (DynamoDB)
│   │   └── timezone_utils.py    # Timezone Utils (DynamoDB)
│   ├── dispatcher/              # WeChat Message Dispatcher
│   ├── runtime/
│   │   └── entrypoint.py        # AgentCore Runtime Entry Point
│   ├── observability.py         # Manual OTEL SDK (AgentCore Evaluations compatible)
├── docs/                        # Knowledge Base Documents
├── scripts/
│   ├── setup_mock_infra.py      # Infrastructure Setup (DynamoDB + Bedrock KB)
│   ├── setup_memory.py          # AgentCore Memory Setup
│   └── deploy_agentcore.sh      # AgentCore Runtime Deploy Script
├── Dockerfile                   # AgentCore Runtime Container
└── README.md
```

## Features

### Supported Operations

| Feature | Description | Backend |
|---------|-------------|---------|
| FAQ | Answer common questions from knowledge base | Bedrock Knowledge Base |
| Lesson Balance Query | Check remaining lessons per course | DynamoDB |
| Check-in Count Query | Check remaining check-in counts | DynamoDB |
| Points Balance Query | Check account points | DynamoDB |
| Schedule Query | View upcoming class schedule | DynamoDB |
| Book a Class | Book a new lesson | DynamoDB |
| Cancel a Class | Cancel an existing booking | DynamoDB |
| Reschedule a Class | Change class time | DynamoDB |
| Long-term Memory | Cross-session user preferences and interaction history | AgentCore Memory |

### Sensitive Scenario Handling

The following scenarios are automatically escalated to human agents:
- Refunds / Complaints
- Purchases / Payments
- Pricing / Promotions
- Lottery / Prize inquiries

## Prerequisites

- Python 3.11+
- AWS account with configured credentials

## Deployment Guide (From Scratch)

### Step 1: Install Dependencies

```bash
uv sync
# or
pip install -e .
```

### Step 2: Configure AWS Credentials

```bash
export AWS_REGION=us-west-2

# Option 1: Environment variables
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"

# Option 2: AWS CLI
aws configure
```

### Step 3: Create Infrastructure (DynamoDB + Bedrock KB)

```bash
python scripts/setup_mock_infra.py --region us-west-2
```

This script will automatically:
1. Create 3 DynamoDB tables (`xxxx-demo-bookings`, `xxxx-demo-accounts`, `xxxx-demo-slots`)
2. Insert test user data (parent_001)
3. Create S3 bucket and upload KB documents from `docs/`
4. Create OpenSearch Serverless collection + vector index
5. Create Bedrock Knowledge Base + S3 data source, start sync
6. Save `DYNAMODB_TABLE_PREFIX`, `KNOWLEDGE_BASE_ID`, `AWS_REGION` to `.env`

> **Dependency**: Requires `opensearch-py` for vector index creation.
>
> **Duration**: First run takes ~3-5 minutes (AOSS collection creation requires waiting).
>
> **Skip resources**: `--skip-dynamodb` or `--skip-kb`.

### Step 4: Create AgentCore Memory

```bash
python scripts/setup_memory.py --region us-west-2 --use-boto3 --name XXXXDemoMemory
```

The script creates a Memory with 3 Strategies (Semantic / UserPreference / Episodic) and saves `MEMORY_ID` to `.env`.

### Step 5: Local Development

```bash
# Default user
python run_agent.py

# Specify user ID
python run_agent.py --parent-id parent_001

# Disable Skills
python run_agent.py --no-skills

# Disable Memory
python run_agent.py --memory-mode disabled
```

CLI commands:
- Type message and press Enter to send
- `reset` - Reset conversation context
- `quit` / `exit` / `q` - Exit

### Step 6: Deploy to AgentCore Runtime (Production)

#### 6a. Replace Configuration Placeholders

The codebase uses `<PLACEHOLDER>` tokens that need to be replaced with your environment-specific values before deployment:

| Placeholder | Description | How to Obtain |
|-------------|-------------|---------------|
| `<ACCOUNT_ID>` | AWS Account ID (12 digits) | `aws sts get-caller-identity --query Account --output text` |
| `<REGION>` | AWS Region | e.g. `us-west-2` |
| `<RUNTIME_ID>` | AgentCore Runtime ID | Obtained after creating Runtime, format: `your_agent_name-AbCdEfGhIj` |
| `<ROLE_SUFFIX>` | IAM Role Suffix | Auto-generated suffix of the Runtime's IAM role |
| `<ECR_REPO>` | ECR Repository Name | `aws ecr create-repository --repository-name your-agent` |
| `<MEMORY_ID>` | AgentCore Memory ID | From Step 4, or read from `.env` |
| `<KNOWLEDGE_BASE_ID>` | Bedrock KB ID | From Step 3, or read from `.env` |
| `<TEST_PARENT_ID>` | Test User ID | User ID written to DynamoDB in Step 3, default `parent_001` |

**VPC mode additional configuration** (only needed for fixed outbound IP):

| Placeholder | Description | How to Obtain |
|-------------|-------------|---------------|
| `<VPC_ID>` | VPC ID | `aws ec2 describe-vpcs --query 'Vpcs[0].VpcId'` |
| `<SUBNET_1>`, `<SUBNET_2>` | Private Subnet IDs | Subnets with NAT Gateway route |
| `<SECURITY_GROUP>` | Security Group ID | Must allow all outbound + same-SG inbound |
| `<NAT_GATEWAY_IP>` | NAT Gateway Elastic IP | For MCP server IP whitelisting |

**Find all placeholders:**

```bash
grep -rn '<ACCOUNT_ID>\|<RUNTIME_ID>\|<ECR_REPO>\|<MEMORY_ID>\|<KNOWLEDGE_BASE_ID>' \
    --include="*.py" --include="*.sh" --include="*.md" --include="Dockerfile"
```

Key files:
- `Dockerfile` — `AGENTCORE_RUNTIME_ID`, `MEMORY_ID`, `KNOWLEDGE_BASE_ID`
- `scripts/deploy_agentcore.sh` — `ACCOUNT_ID`, `RUNTIME_ID`, `ROLE_SUFFIX`, `ECR_REPO`, VPC settings
- `scripts/batch_test.py` — `RUNTIME_ARN`, `PARENT_ID`
- `scripts/run_evaluation.py` — `RUNTIME_ARN`, `RUNTIME_ID`, `PARENT_ID`

#### 6b. Deploy with AgentCore Starter Toolkit

We recommend using [AgentCore Starter Toolkit](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-getting-started.html) to create and deploy the Runtime:

```bash
# Install Starter Toolkit
pip install bedrock-agentcore-starter-toolkit

# Initialize project config (generates .bedrock_agentcore.yaml)
agentcore init

# Launch deployment (auto builds Docker, pushes ECR, creates/updates Runtime)
agentcore launch
```

`agentcore launch` automatically:
1. Builds ARM64 Docker image
2. Creates ECR repository and pushes image
3. Creates AgentCore Runtime (or updates to new version)
4. Waits for Runtime status to become READY

After deployment, the Runtime ID is saved in `.bedrock_agentcore.yaml`.

> **Note**: Before deploying, ensure Dockerfile environment variables are set correctly:
> ```dockerfile
> ENV DYNAMODB_TABLE_PREFIX=xxxx-demo
> ENV MEMORY_ID=<your-memory-id>          # from .env
> ENV KNOWLEDGE_BASE_ID=<your-kb-id>      # from .env
> ENV AGENTCORE_RUNTIME_ID=<your-runtime-id>  # backfill after first deploy
> ```

#### 6c. Add IAM Permissions

The AgentCore Runtime IAM Role needs additional permissions:

```bash
# DynamoDB + Bedrock KB access
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

# AgentCore Memory access (if Memory is enabled)
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

#### 6d. Redeploy (Subsequent Updates)

After code changes, redeploy:

```bash
# Option 1: Starter Toolkit (recommended)
agentcore launch

# Option 2: Manual deploy script
./scripts/deploy_agentcore.sh          # Public mode
./scripts/deploy_agentcore.sh --vpc    # VPC mode (fixed outbound IP)
```

#### 6e. Verify

```bash
# Test invocation
PAYLOAD=$(echo -n '{"prompt": "Hello", "parent_id": "parent_001"}' | base64)
SESSION_ID=$(uuidgen)-$(date +%s)
aws bedrock-agentcore invoke-agent-runtime \
    --agent-runtime-arn "arn:aws:bedrock-agentcore:us-west-2:<ACCOUNT_ID>:runtime/<RUNTIME_ID>" \
    --runtime-session-id "$SESSION_ID" \
    --payload "$PAYLOAD" \
    --region us-west-2 \
    /tmp/output.json && cat /tmp/output.json

# View CloudWatch logs
aws logs tail "/aws/bedrock-agentcore/runtimes/<RUNTIME_ID>-DEFAULT" \
    --region us-west-2 --since 10m --format short
```

## AgentCore Evaluations (Quality Assessment)

After deploying to AgentCore Runtime, you can use AgentCore Evaluations' 13 built-in evaluators to score agent response quality.

### How It Works

During each request, the agent writes spans and events to CloudWatch via the manual OTEL SDK in `observability.py`:

```
Agent processes request
├── Span → X-Ray → aws/spans (invoke_agent, memory.save_turn)
└── Events → CloudWatch Logs → otel-rt-logs (gen_ai.user.message, gen_ai.choice, ...)
              ↓
    Evaluate API reads this data for scoring
```

### Prerequisites

1. **Enable CloudWatch Transaction Search** (one-time):

```bash
aws logs put-resource-policy \
    --policy-name AgentCoreXRayAccess \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Sid":"AllowXRayToWriteLogs","Effect":"Allow","Principal":{"Service":"xray.amazonaws.com"},"Action":"logs:PutLogEvents","Resource":"arn:aws:logs:us-west-2:<ACCOUNT_ID>:log-group:aws/spans:*"}]}'

aws xray update-trace-segment-destination --destination CloudWatchLogs --region us-west-2
```

2. **Create otel-rt-logs log stream** (one-time):

```bash
aws logs create-log-stream \
    --log-group-name "/aws/bedrock-agentcore/runtimes/<RUNTIME_ID>-DEFAULT" \
    --log-stream-name "otel-rt-logs" \
    --region us-west-2
```

3. **Increase X-Ray Indexing rate** (optional, for dev/test):

```bash
# Default is 1%, recommend 100% for dev/test
aws xray update-indexing-rule --name "Default" \
    --rule '{"Probabilistic": {"DesiredSamplingPercentage": 100}}' \
    --region us-west-2
```

### Usage

#### Step 1: Invoke Agent (generate trace data)

```python
import json, uuid, boto3

client = boto3.client("bedrock-agentcore", region_name="us-west-2")
session_id = f"eval-{uuid.uuid4()}-{int(__import__('time').time())}"

payload = json.dumps({
    "prompt": "I'd like to book a Chinese lesson",
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

> **Note**: Wait ~2 minutes after invocation for spans and events to appear in CloudWatch.

#### Step 2: Download spans + events from CloudWatch

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

#### Step 3: Run Evaluators

```python
client = boto3.client("bedrock-agentcore", region_name=REGION)

# Single evaluator
response = client.evaluate(
    evaluatorId="Builtin.Helpfulness",
    evaluationInput={"sessionSpans": items},
)
for r in response["evaluationResults"]:
    print(f"Score: {r.get('value'):.2f}, Label: {r.get('label')}")
    print(f"Explanation: {r.get('explanation', '')[:200]}")
```

```python
# Batch run all evaluators
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

### Evaluator Reference

| Evaluator | Level | Description |
|-----------|-------|-------------|
| Coherence | Trace | Is the response logically coherent |
| Conciseness | Trace | Is the response concise |
| Correctness | Trace | Is the response factually correct |
| Faithfulness | Trace | Is the response faithful to context |
| GoalSuccessRate | Session | Did the agent achieve the user's goal |
| Harmfulness | Trace | Is the response harmful |
| Helpfulness | Trace | Is the response helpful |
| InstructionFollowing | Trace | Does the response follow instructions |
| Refusal | Trace | Did the agent refuse to answer |
| ResponseRelevance | Trace | Is the response relevant to the question |
| Stereotyping | Trace | Does the response contain stereotypes |
| ToolParameterAccuracy | Tool | Are tool parameters correct |
| ToolSelectionAccuracy | Tool | Was the right tool selected |

### Technical Implementation

This project implements a manual OTEL SDK that produces trace output in the same format as the Strands Agents SDK, enabling AgentCore Evaluations to parse trace data from the Claude Agent SDK.

Key implementation files:
- `agent/observability.py` — Manual OTEL SDK (TracerProvider + LoggerProvider + CloudWatch direct write)
- `agent/agent.py` `chat_stream()` — Emits bedrock-format log events during response streaming
- `agent/runtime/entrypoint.py` — Calls `init_otel()` at startup
## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.


