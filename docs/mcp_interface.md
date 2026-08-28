# MCP 接口设计

## 概述

Gaokao RAG 通过 tRPC-Agent-Python 的 **MCPToolset** 提供 MCP Server 能力。框架原生支持 MCP（`trpc_agent_sdk.tools` 提供 `MCPToolset`），支持 STDIO、SSE、Streamable HTTP 三种传输方式。

## 两种 MCP 视角

### 1. Agent 调用外部 MCP 工具（消费方）

Gaokao RAG 的 Agent 作为 MCP 客户端，接入外部工具服务器：

```python
from trpc_agent_sdk.tools import MCPToolset
from mcp.client.stdio import StdioConnectionParams, StdioServerParameters

class ExamToolsMCP(MCPToolset):
    """接入外部题目数据库 MCP 服务"""
    def __init__(self):
        super().__init__()
        self._connection_params = StdioConnectionParams(
            server_params=StdioServerParameters(
                command="python",
                args=["external_exam_server.py"],
            ),
            timeout=5,
        )
```

### 2. 暴露 Gaokao RAG 能力为 MCP Server（提供方）

把 Gaokao RAG 的检索/复习能力暴露给外部 Agent（如 Claude Code、其他 MCP 客户端）：

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gaokao-rag")

@mcp.tool()
async def search_questions(query: str, topic: str = None) -> list[dict]:
    """按知识点/题型/年份检索高考题目（MVP 数学，扩科后按 subject 过滤）"""
    ...

@mcp.tool()
async def get_question_detail(doc_id: str) -> dict:
    """获取题目完整信息（含 VLM 图形描述）"""
    ...
```

## MCP 工具清单

### 检索类

| 工具名 | 描述 | 参数 |
|--------|------|------|
| `search_questions` | 语义检索题目 | `query`（问题描述）、`topic`（知识点过滤）、`exam_region`（考区，匹配考区层级任一级，如"南昌"命中 ["深圳","南昌","全国一卷"] 的题）、`exam_year`（年份）、`question_type`（题型）、`top_k`（返回数量） |
| `get_question_detail` | 获取题目完整信息 | `doc_id`（题目 ID） |
| `list_topics` | 列出所有知识点 tag | — |
| `search_topic` | 按名字/别名搜索知识点 tag | `keyword`（关键词） |
| `get_questions_by_topic` | 按知识点列出题目 | `topic_name`（知识点名字/tag）、`limit` |

### 错题与复习类

| 工具名 | 描述 | 参数 |
|--------|------|------|
| `add_error` | 记录错题（用户口述错因 → LLM 生成错因总结后入库） | `question_id`、`error_type`、`user_reflection`（用户口述）、`error_summary`（LLM 生成） |
| `add_exam_attempt` | 记录整卷作答（用户口述 → LLM 解析逐题对错 + 整卷分析后入库） | `file_id`（files 表）、`user_statement`（口述，可含成绩单图片）、`attempt_date` |
| `get_error_stats` | 获取错题统计 | `user_id`（可选，MVP 默认单一用户） |
| `generate_review_plan` | 生成复习计划 | `user_id`、`focus_topics`（可选聚焦知识点） |
| `get_review_plan` | 获取已有复习计划 | `user_id` |

### 周期报告类（周报 / 月报）

| 工具名 | 描述 | 参数 |
|--------|------|------|
| `generate_periodic_report` | 生成周报/月报（错题聚合 + 知识点分析 + 针对性练习建议） | `user_id`、`period_type`（"weekly"/"monthly"）、`force`（可选，强制刷新缓存） |
| `get_periodic_report` | 获取已生成的周期报告 | `user_id`、`period_type`、`period_start`（可选） |
| `list_periodic_reports` | 列出历史报告 | `user_id`、`period_type`（可选） |

### 分析类

| 工具名 | 描述 | 参数 |
|--------|------|------|
| `analyze_weak_points` | 分析薄弱知识点 | `user_id`（可选，MVP 默认单一用户） |
| `recommend_similar_questions` | 推荐同类题目 | `question_id`、`top_k` |

## 实现方式

### 方案 A：MCP Server 对外暴露（推荐）

tRPC-Agent 内置 MCP 支持，通过 MCPToolset 把 Gaokao RAG 的能力暴露给外部 Agent（如 Claude Code）：

```python
from trpc_agent_sdk.tools import MCPToolset
from mcp.server.fastmcp import FastMCP

# 方式 1：FastMCP 直接定义工具
mcp = FastMCP("gaokao-rag")

@mcp.tool()
async def search_questions(query: str, topic: str = None) -> list[dict]:
    """按知识点/题型/年份检索高考题目（MVP 数学，扩科后按 subject 过滤）"""
    ...

@mcp.tool()
async def get_question_detail(doc_id: str) -> dict:
    """获取题目完整信息（含 VLM 图形描述）"""
    ...

# 方式 2：通过 MCPToolset 包装为 TeamAgent 子 Agent 的工具
class GaokaoMCPToolset(MCPToolset):
    """Gaokao RAG 自带的 MCP 工具集"""
    def __init__(self):
        super().__init__()
        self._connection_params = StdioConnectionParams(
            server_params=StdioServerParameters(
                command="python",
                args=["scripts/mcp_server.py"],
            ),
            timeout=10,
        )

# 挂载到搜索信息子 Agent
search_agent = LlmAgent(
    name="search",
    model=model,
    instruction=SEARCH_PROMPT,
    tools=[
        GaokaoMCPToolset(),           # MCP 工具集（检索类）
        FunctionTool(search_topic),
    ],
)

# 挂载到聚合数据子 Agent
aggregate_agent = LlmAgent(
    name="aggregate",
    model=model,
    instruction=AGGREGATE_PROMPT,
    tools=[
        FunctionTool(get_error_stats),
        FunctionTool(generate_review_plan),
        FunctionTool(generate_periodic_report),
    ],
)
```

### 方案 B：FunctionTool 直接挂到子 Agent

对于不依赖外部进程的工具，直接用 `FunctionTool` 包装业务函数，挂到对应的子 Agent：

| 工具 | 挂到子 Agent | 说明 |
|------|------------|------|
| `search_questions` / `get_question_detail` / `list_topics` / `search_topic` | 搜索信息 Agent | Chroma + SQLite 检索 |
| `get_error_stats` / `generate_review_plan` | 聚合数据 Agent | SQLite 错题统计 |
| `generate_periodic_report` | 聚合数据 Agent | 错题聚合 + LLM 建议 |
| `add_error` | 聚合数据 Agent | 错题录入 |
| VLM 理解 | VLM 理解 Agent | FunctionTool 封装 VLM 调用 |

```python
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.agents import LlmAgent

# 搜索信息子 Agent
search_agent = LlmAgent(
    name="search",
    model=model,
    instruction=SEARCH_PROMPT,
    tools=[
        FunctionTool(search_questions),
        FunctionTool(get_question_detail),
        FunctionTool(list_topics),
        FunctionTool(search_topic),
    ],
)

# 聚合数据子 Agent
aggregate_agent = LlmAgent(
    name="aggregate",
    model=model,
    instruction=AGGREGATE_PROMPT,
    tools=[
        FunctionTool(get_error_stats),
        FunctionTool(generate_review_plan),
        FunctionTool(generate_periodic_report),
        FunctionTool(add_error),
    ],
)

# VLM 理解子 Agent
vlm_agent = LlmAgent(
    name="vlm",
    model=model,
    instruction=VLM_PROMPT,
    tools=[FunctionTool(vlm_understand_image)],
)

# TeamAgent 组装
gaokao_team = TeamAgent(
    name="gaokao_team",
    leader=LlmAgent(model=model, instruction=LEADER_PROMPT),
    members=[
        search_agent,
        vlm_agent,
        aggregate_agent,
        LlmAgent(name="format", model=model, instruction=FORMAT_PROMPT),
    ],
)
```

**决策**：MVP 用方案 B（FunctionTool 直接挂子 Agent），工具归属清晰。MCP Server（方案 A）作为对外暴露接口单独实现，供 Claude Code 等外部 Agent 调用。

## MCP Server 传输方式

| 传输方式 | 适用场景 | 实现 |
|---------|---------|------|
| STDIO | Claude Code、本地进程调用 | `scripts/mcp_server.py` 直接运行 |
| SSE | HTTP 远程调用（跨进程） | 基于 FastAPI + SSE |
| Streamable HTTP | 最新标准，推荐 | 基于 FastAPI |

## CLI 命令行入口

```bash
# 启动 MCP Server（STDIO）
python scripts/mcp_server.py

# 启动 MCP Server（SSE，端口 8000）
python scripts/mcp_server.py --transport sse --port 8000

# 测试 MCP 工具
python scripts/mcp_client_test.py
```

## 外部 Agent 集成示例

配置到 Claude Code 的 MCP 配置：

```json
{
  "mcpServers": {
    "gaokao-rag": {
      "command": "python",
      "args": ["D:/AI_study/project/gaokao_rag/scripts/mcp_server.py"]
    }
  }
}
```

之后 Claude Code 可以直接调用 `search_questions`、`get_question_detail` 等工具。
