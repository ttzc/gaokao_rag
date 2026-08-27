# Agent 编排设计

> 本目录是原 `docs/agent.md` 拆分后的 Agent 编排文档。**目录结构与 `src/agent/` 一一对应：每个 `.py` 文件对应一个 `.md` 文件**。整体架构为 TeamAgent（GraphAgent 备用方案已移除，2026-08-25）。

## 概述

Gaokao RAG 的 Agent 层基于 tRPC-Agent-Python 的 **TeamAgent** 构建（多 Agent 协作模式）。这是与 AlgoNotes RAG（单 RAG Agent）拉开差距的核心差异点。

**核心架构**：一个 **Team Leader**（LLM）接收用户请求，**自由委派**任务给 5 个查询侧子 Agent（意图识别/搜索信息/VLM 理解/聚合数据/输出整理）+ 4 个摄入侧子 Agent（文档识别/结构识别/知识整理/入库决策），再汇总成员结果生成最终答案。Leader 看问题灵活决定调谁、调几个、什么顺序——不是固定流程模板。

**为什么用 TeamAgent 而非 GraphAgent**：

- 职责解耦：每个子 Agent 独立测试/替换/升级（如换 VLM 模型只动 VLM Agent）
- 复杂度可管理：每个 Agent 的 prompt 只聚焦一个职责
- 灵活委派：Leader 按需调用，不同场景走不同成员组合
- **项目叙事**：单 Agent → 多 Agent 协作，是 README 亮点（对比 AlgoNotes）

> 注：GraphAgent 曾作为备用方案，但 trpc-agent 源码已确认 TeamAgent 设计可用（2026-08-12 实测跑通），备用方案已移除（2026-08-25）。主架构即 TeamAgent。

## 团队结构

团队分**查询侧**（读数据，产生回答）和**摄入侧**（写数据，接收学生资料），共用同一个 Leader：

```mermaid
flowchart TD
    U[用户请求] --> L[Team Leader<br/>自由委派 + 综合]

    subgraph "查询侧（读）"
        L --> A1[意图识别 Agent]
        L --> A2[搜索信息 Agent]
        L --> A3[VLM 理解 Agent]
        L --> A4[聚合数据 Agent]
        L --> A5[输出整理 Agent]
    end

    subgraph "摄入侧（写）"
        L --> B1[文档识别 Agent]
        L --> B2[结构识别 Agent]
        L --> B3[知识整理 Agent]
        L --> B4[入库决策 Agent]
    end

    A1 --> L
    A2 --> L
    A3 --> L
    A4 --> L
    A5 --> L
    B1 --> L
    B2 --> L
    B3 --> L
    B4 --> L
```

### 成员职责：查询侧（读）

| 子 Agent | 职责 | 挂载能力 |
| --------- | ------ | --------- |
| **意图识别 Agent** | 判断用户意图（question/review/report/browse/**ingest**） | LLM 分类 |
| **搜索信息 Agent** | 混合检索（Chroma + SQLite，不分子意图） | AgenticLangchainKnowledgeSearchTool |
| **VLM 理解 Agent** | 图形描述（有图才调用） | VLM FunctionTool |
| **聚合数据 Agent** | 错题/作答统计、周报聚合（**读写** SQLite：errors/exam_attempts 统计 + periodic_reports 落库） | SQLite 查询/写入工具 |
| **输出整理 Agent** | 格式化 + 分片发送 | 纯 LLM |

### 成员职责：摄入侧（写）

| 子 Agent | 职责 | 挂载能力 |
| --------- | ------ | --------- |
| **文档识别 Agent** | 接收照片/PDF → 提取内容（图片走 VLM，PDF 走 PyMuPDF） | VLM + PyMuPDF 工具 |
| **结构识别 Agent** | 区分讲解段 vs 题目段 → **语义划分每题「题目/答案/解析」**（不依赖关键词）→ 生成题目清单（每题一句话概括） | LLM 分类 |
| **知识整理 Agent** | 知识点提取 → tag 归位/别名归并（写 topics） | tag CRUD 工具（topics FunctionTool） |
| **入库决策 Agent** | 回显题目清单 → 收集学生选择（入库/错题/跳过）→ 写 questions/errors | SQLite 写入工具 |

**设计要点**：

- 查询侧与摄入侧**共用底层工具**（VLM、SQLite），但职责相反——查询侧读、摄入侧写
- **批量摄入**（ima 导出 20 份 PDF）走 CLI 脚本 `scripts/ingest.py`（开发者初始化用），不占 Agent 团队
- **即时摄入**（学生 QQ 发作业/错题照片）走摄入侧 Agent——这是学生侧唯一的资料录入入口
- 分层边界：`src/agent/ingestion/`（摄入侧子 Agent，含 LLM）与 `src/ingestion/`（写门面，无 LLM）是两层不同概念；子 Agent 只做意图判断与编排，具体存储读写一律委托给两个门面（详见 [architecture.md](../architecture.md)）

## 摄入侧数据流与 State 契约

学生拍照/发文档触发 `ingest` 意图后，摄入侧 4 个子 Agent 依次协作，通过 `GaokaoState` 传递中间产物：

```mermaid
flowchart TD
    U[学生上传<br/>照片 / PDF / 文字] --> L[Team Leader<br/>ingest 意图]
    L --> R[文档识别 Agent]
    R -->|raw_blocks| S[结构识别 Agent]
    S -->|lecture_segments| KN[讲解段自动入库<br/>knowledge_notes]
    S -->|pending_questions| K[知识整理 Agent]
    K -->|topic_draft| D[入库决策 Agent]
    D -->|回显题目清单| U2[用户选择<br/>入库 / 错题 / 跳过]
    U2 -->|ingest_decisions| D
    D -->|ingest_results| OUT[写入 questions / errors<br/>回显结果]
```

**State 契约字段**（摄入侧新增）：

| 字段 | 产出者 | 内容 | 消费方 |
|------|--------|------|--------|
| `raw_blocks` | 文档识别 | 结构化文本块 + 图像列表 + 坐标信息 | 结构识别 |
| `pending_questions` | 结构识别 | 题目清单（每题：一句话概括 + 原文块 + 关联图像） | 知识整理、入库决策（回显） |
| `lecture_segments` | 结构识别 | 讲解段文本列表 | 自动入库（knowledge_notes） |
| `topic_draft` | 知识整理 | 每题知识点草案（topic_name 列表，待归位） | 入库决策 |
| `ingest_decisions` | 用户 | 每题去向（入库 / 错题 / 跳过） | 入库决策 |
| `ingest_results` | 入库决策 | 写入结果（question_id / doc_id） | 输出整理 |

**4 个澄清要点**：

1. **文档识别只提取不写库**：`raw_blocks` 是内存态，由结构识别消费，不落任何表
2. **讲解段自动入库、题目才回显**：`lecture_segments` 直接写 knowledge_notes（无需用户确认）；只有题目进回显清单
3. **知识整理双路由**：题目段标注 → `question_topics` 关联；讲解段标注 → `knowledge_notes.topic_tags`。两者都走 tag 归位原语（见 [ingestion/knowledge_organize.md](ingestion/knowledge_organize.md)）
4. **错题先题后错**：标记「错题」的题**先** `ingest_question` 入库、**再**由错题本体系调 `ingest_error(question_id, user_reflection)` 写错因，杜绝循环依赖（见 [ingestion/storage_decision.md](ingestion/storage_decision.md)）

## 目录导航（与 src/agent/ 一一对应）

| 本文档 | 对应 `src/agent/` | 内容 |
|--------|-------------------|------|
| [README.md](README.md) | （包说明） | 总览、团队结构、摄入侧数据流契约、导航 |
| [leader.md](leader.md) | `leader.py` | TeamLeader 编排、委派策略、实测 3 铁律、Langfuse |
| [tools/README.md](tools/README.md) | （工具总览） | FunctionTool 挂载矩阵与门面边界 |
| [tools/vlm_tool.md](tools/vlm_tool.md) | `tools/vlm_tool.py` | VLM 图形理解工具 |
| [tools/knowledge_tool.md](tools/knowledge_tool.md) | `tools/knowledge_tool.py` | 知识点查询 / tag 归位工具 |
| [tools/error_tool.md](tools/error_tool.md) | `tools/error_tool.py` | 错题分析工具 |
| [tools/extract_tool.md](tools/extract_tool.md) | `tools/extract_tool.py` | PDF / 图像提取工具 |
| [tools/ingest_tool.md](tools/ingest_tool.md) | `tools/ingest_tool.py` | 题目 / 错题摄入（写库） |
| [ingestion/doc_recognition.md](ingestion/doc_recognition.md) | `ingestion/doc_recognition.py` | 文档识别 Agent |
| [ingestion/structure_recognition.md](ingestion/structure_recognition.md) | `ingestion/structure_recognition.py` | 结构识别 Agent |
| [ingestion/knowledge_organize.md](ingestion/knowledge_organize.md) | `ingestion/knowledge_organize.py` | 知识整理 Agent |
| [ingestion/storage_decision.md](ingestion/storage_decision.md) | `ingestion/storage_decision.py` | 入库决策 Agent |
| [retrieval/intent.md](retrieval/intent.md) | `retrieval/intent.py` | 意图识别 Agent |
| [retrieval/search.md](retrieval/search.md) | `retrieval/search.py` | 搜索信息 Agent |
| [retrieval/vlm.md](retrieval/vlm.md) | `retrieval/vlm.py` | VLM 理解 Agent（查询侧） |
| [retrieval/aggregate.md](retrieval/aggregate.md) | `retrieval/aggregate.py` | 聚合数据 Agent |
| [retrieval/output.md](retrieval/output.md) | `retrieval/output.py` | 输出整理 Agent |

## 纯 LLM Agent 的 Skill 萃取（2026-08-27）

意图识别、结构识别、输出整理 三个子 Agent **不挂载任何 FunctionTool**，仅做 LLM 语义处理。为降低 context 占用并实现 prompt 沉淀，三者的核心指令抽取为 trpc-agent Skill，统一存放于 `src/agent/skills/<name>/SKILL.md`，由对应 sub-agent 在构造时通过 `skill_load` 注入（渐进式披露，详见 tRPC-Agent-Python 的 skills 子系统）。

| 子 Agent | Skill 路径 | 说明 |
|----------|-----------|------|
| 意图识别 | `src/agent/skills/intent/SKILL.md` | 纯 LLM 分类（query_type / period_type） |
| 结构识别 | `src/agent/skills/structure-recognition/SKILL.md` | 语义切分「讲解段 / 题目段」 |
| 输出整理 | `src/agent/skills/output/SKILL.md` | 排版 + 溯源引用 + 分片发送 |

**边界**：保留 sub-agent 的 Leader 委派与 `GaokaoState` 回填结构 —— Skill 只承载 prompt，不替代 TeamAgent 的编排与并行委派能力。知识整理（挂 knowledge_tool）、聚合数据（经门面读写 SQLite）因依赖外部调用，不纳入本萃取。

**落地**：实际 SKILL.md 与 sub-agent 构造代码随 `src/agent/` 包落地（Claude 跟进）。

## Session 与 Memory

### Session（单会话上下文）

利用 tRPC-Agent 的 `SessionService`：

```python
from trpc_agent_sdk.sessions import InMemorySessionService
# 或 SqlSessionService（持久化）

session_service = SqlSessionService(db_path="data/gaokao.db")

runner = Runner(
    app_name="gaokao_rag",
    agent=gaokao_agent,
    session_service=session_service,
)
```

用户的多轮对话上下文自动管理——"刚才那道题如果改一下参数呢"这种追问天然支持。

### Memory（跨会话记忆）

利用 tRPC-Agent 的 `MemoryService`：

```python
from trpc_agent_sdk.memory import SqlMemoryService

memory_service = SqlMemoryService(db_path="data/gaokao.db")
```

存储内容：

- 用户的错题历史
- 常错的知识点
- 上次复习到了哪个知识点
- 用户的薄弱项画像

## Prompt 策略

### 系统 Prompt

```
你是一位帮助高中学生备考的 AI 助手（当前支持数学，后续扩展到理化生等科目）。

你的职责：
1. 帮助检索和理解题目
2. 提供清晰的解题思路，而非直接给出答案
3. 关联知识点，帮助用户建立知识体系
4. 根据错题分布给出复习建议

原则：
- 解题过程分步骤，每步标注知识点
- 涉及图形时结合 VLM 描述分析
- 鼓励用户思考，适当追问而非全盘输出
- 如果用户的问题描述不完整，先确认再检索
```

### 检索增强 Prompt

通过 tRPC-Agent 的 `prompt_template` 注入检索结果：

```python
RAG_PROMPT = """基于以下检索到的题目和知识点回答用户问题。

检索结果：
{context}

图形描述：
{vlm_context}

用户问题：{query}

要求：
1. 解题思路分步骤
2. 标注每步用到的知识点
3. 引用来源（哪份试卷第几题）
"""
```

## 对外接口

### CLI 交互

```bash
# 交互式问答
python scripts/chat.py

# 单次查询
python scripts/chat.py "椭圆离心率最值怎么求"

# 复习模式
python scripts/chat.py --mode review
```

### FastAPI HTTP

利用 tRPC-Agent 内置的 FastAPI 服务：

```python
from trpc_agent_sdk.server import create_fastapi_app

app = create_fastapi_app(
    agent=gaokao_agent,
    session_service=session_service,
    memory_service=memory_service,
)
```

### MCP Server

利用 tRPC-Agent 的 MCPToolset，暴露以下工具：

| MCP 工具 | 描述 |
| --------- | ------ |
| `search_questions` | 按知识点/题型/年份检索题目 |
| `get_question_detail` | 获取题目完整信息（含 VLM 描述） |
| `get_error_stats` | 获取用户错题统计 |
| `get_review_plan` | 获取/生成复习计划 |
| `add_error` | 添加错题记录 |
| `list_topics` | 列出所有知识点 tag |
| `search_topic` | 按名字/别名搜索知识点 tag |
