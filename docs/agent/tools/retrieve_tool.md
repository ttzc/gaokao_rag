# 检索工具（读门面适配层 + 框架检索工具介绍）

> 对应代码：`src/agent/tools/retrieve_tool.py`（合并自原 `knowledge_tool` 查询侧 / `error_tool`）。
> 读侧 FunctionTool 集合，封装 `src.retrieval` 读门面，**严禁 `import src.store.*`**（见 [architecture.md 分层边界契约](../../architecture.md)）；框架检索工具经 `src.retrieval.knowledge.get_knowledge()` 注入 `GaokaoKnowledge`，完全符合分层铁律（2026-08-28 `GaokaoKnowledge` 已归位读门面，无需特例）。

## 定位

子 Agent 的读能力全部通过 FunctionTool 注入：语义检索、题目 / 知识点查询、错题 / 作答统计、周报聚合。工具是子 Agent 与读门面之间的适配层——内部封装函数调用 + 参数整形，**不含 LLM 决策**（LLM 只负责判断与生成结构化数据，调用由工具执行）。

> 工具包按「写 / 读」拆为两个文件：`ingest_tool.py`（写侧，见 [ingest_tool.md](ingest_tool.md)）+ `retrieve_tool.py`（本文件，读侧）。

## 框架检索工具介绍（LangchainKnowledgeSearchTool / AgenticLangchainKnowledgeSearchTool）

tRPC-Agent-Python 在 `trpc_agent_sdk.server.knowledge.tools.langchain_knowledge_searchtool` 内置两个检索工具。它们直接消费 `LangchainKnowledge` 实例（本项目为 `src/retrieval/knowledge.py` 的 `GaokaoKnowledge` 子类，经 `get_knowledge()` 获取），把向量库的「语义检索 + metadata 过滤」封装成 LLM 可调的 FunctionTool。**MVP 搜索信息子 Agent（查询侧）挂基础版 `LangchainKnowledgeSearchTool`（纯向量比较，`knowledge_filter` 暂不配）；Agentic 版（LLM 动态过滤）留待具体带条件检索需求时再上**（2026-08-28 决策，见 [../retrieval/search.md](../retrieval/search.md)）。

### LangchainKnowledgeSearchTool — 静态过滤检索

- **工具名（LLM 可见）**：`knowledge_search`
- **描述**：`Search for relevant information in the knowledge base`
- **LLM 可见参数**：仅 `query`（string，必填）——不暴露任何 metadata 过滤
- **构造参数（开发者侧）**：`rag: LangchainKnowledge`、`top_k=3`、`search_type=SearchType.SIMILARITY`、`min_score=0.0`、`knowledge_filter: KnowledgeFilterExpr | None = None`
- **执行流程**：
  1. `extra_params = self.rag.build_search_extra_params(knowledge_filter)` —— 把 `KnowledgeFilterExpr` 翻成 Chroma `where`（由 `GaokaoKnowledge` 子类重写）
  2. `SearchParams(rank_top_k=top_k, search_type=search_type, extra_params=extra_params)`
  3. `self.rag.search(agent_context, SearchRequest(query=Part.from_text(query)))` → 框架 `SearchResult`
  4. 序列化为 `list[dict]`：`{"document": {"page_content": ..., "metadata": ...}, "score": float}`
  5. `score < min_score` 的文档直接丢弃（不返回）

### AgenticLangchainKnowledgeSearchTool — LLM 自构建动态过滤

`LangchainKnowledgeSearchTool` 的子类，额外暴露 `dynamic_filter` 参数，让 LLM 在运行时**自己生成** metadata 过滤条件，无需开发者预写。

- **工具名（LLM 可见）**：仍为 `knowledge_search`（继承）
- **描述**：`Search knowledge with an optional dynamic_filter expression`
- **LLM 可见参数**：`query`（必填）+ `dynamic_filter`（可选，object）
- **`dynamic_filter` 结构**：`KnowledgeFilterExpr` 对象
  - `field`：metadata 字段路径，如 `metadata.category`
  - `operator`：以下 **13 个**之一 —— `eq` / `ne` / `gt` / `gte` / `lt` / `lte` / `in` / `not in` / `like` / `not like` / `between` / `and` / `or`
  - `value`：比较值；当 `operator` 为 `and` / `or` 时，为一个子条件数组
  - 框架对 `dynamic_filter` 做 `KnowledgeFilterExpr.model_validate(...)` 解析后才下发
- **与静态 filter 的合并**：若同时给了构造期的 `knowledge_filter`（静态）和运行时 `dynamic_filter`（动态），二者以 `operator="and"` 合并：

  ```python
  final_filter = KnowledgeFilterExpr(
      operator="and",
      value=[self.knowledge_filter, parsed_filter],
  )
  ```

  若只有其一，则用之；都为空则无过滤。

### 与 GaokaoKnowledge 的关系

- 检索工具本身**不实现**过滤翻译——它委托 `rag.build_search_extra_params(knowledge_filter)`。
- 本项目 `GaokaoKnowledge`（`src/retrieval/knowledge.py`，`LangchainKnowledge` 子类，已落地）重写该方法，把 `KnowledgeFilterExpr` 翻译成 `{"filter": chroma_filter}`（langchain_chroma 的 `filter` 参数映射为底层 `where`；含 `doc_type` 区分 `question` / `note`、年份 / 题型 / 考区等）。
- 因此「挂哪个 Knowledge」决定过滤语义；工具代码与具体字段解耦，换模型 / 维度只需换 Knowledge 实例。

### 返回格式（序列化）

```json
[
  {
    "document": {
      "page_content": "题目/讲解的正文……",
      "metadata": { "doc_id": "q_42", "doc_type": "question", "subject": "数学", "...": "..." }
    },
    "score": 0.87
  }
]
```

> 上层（搜索信息子 Agent）直接取 `doc.document.metadata[...]`，**不二次包装**（见 [retrieval/README.md](../../retrieval/README.md)「混合检索语义」与 [knowledge.md](../../retrieval/knowledge.md)「不包装框架 SearchResult」约定，2026-08-28 决策）。

## 工具清单（读侧规划）

> 读侧工具分两类：**① 框架检索工具**（已内置，直接挂载，见上）；**② 业务查询工具**（薄封装 `src.retrieval` 门面，待实现）。

| 工具 | 类型 | 封装门面 | 挂载子 Agent | 状态 |
|------|------|----------|--------------|------|
| `LangchainKnowledgeSearchTool` | 框架内置 | `GaokaoKnowledge`（向量） | 搜索信息（MVP） | ✅ 框架提供 |
| `search_questions` | 业务查询 | `src.retrieval.question` | 搜索信息 | ⏳ 门面未落地 |
| `get_question_detail` | 业务查询 | `src.retrieval.question` | 搜索信息 / 输出整理 | ⏳ 门面未落地 |
| `browse_questions` | 业务查询 | `src.retrieval.question` | 浏览 | ⏳ 门面未落地 |
| `search_knowledge_notes` | 业务查询 | `src.retrieval.knowledge_note` | 搜索信息 | ⏳ 门面未落地 |
| `search_topics` / `list_topics` / `get_topic` | 业务查询 | `src.retrieval.topic` | 搜索信息 / 知识整理 | ⏳ 门面未落地 |
| `get_error_stats` / `get_error_details` / `get_weak_topics` | 业务查询 | `src.retrieval.error` | 聚合数据 | ⏳ 门面未落地 |
| `get_attempt_stats` | 业务查询 | `src.retrieval.exam_attempt` | 聚合数据 | ⏳ 门面未落地 |
| `aggregate_errors` / `aggregate_attempts` / `get_report` / `compute_trend` | 业务查询（含落库） | `src.retrieval.report` + `src.ingestion` 写 | 聚合数据 | ⏳ 门面未落地 |

## 业务查询工具规划（薄封装 src.retrieval）

读侧工具与写侧（`ingest_tool`）对称：**只透传 `src.retrieval` 门面函数，不包装框架 `SearchResult`**。

- **语义检索**：直接用框架 `LangchainKnowledgeSearchTool`（已内置，纯向量比较），不另建 `search` 工具——重复造轮子无意义。后续需要 metadata 条件检索时升级为 `AgenticLangchainKnowledgeSearchTool`（LLM 运行时自生成 `dynamic_filter`，见上节）。
- **题目 / 知识点详情**：`search_questions` / `get_question_detail` / `browse_questions` / `search_knowledge_notes` 调 `src.retrieval` 对应函数，回查 SQLite 权威数据（题目完整四要素、讲解原文），不依赖 Chroma 返回的截断文本。
- **统计聚合**：`error` / `exam_attempt` / `report` 三组工具调 `src.retrieval` 统计函数；周报落库走 `src.ingestion` 写门面（聚合数据子 Agent 是唯一会写库的查询侧成员，见 [../retrieval/aggregate.md](../retrieval/aggregate.md)）。
- **topic 查询**：`search_topics` 复用在摄入侧已挂载的 tag 归位原语（见 [ingest_tool.md](ingest_tool.md)「KnowledgeTool」），查询侧与摄入侧共用同一套 `src.ingestion.topic` 逻辑。

**实现约束（与 ingest_tool 对齐）**：

- 薄封装 + async 经 `asyncio.to_thread` 下沉同步 DB 调用
- 可空参数写 `typing.Optional[...]`（非 PEP 604 `X | None`，否则 FunctionTool schema 生成抛 `ValueError`）
- **模块级实例用 PEP 562 惰性导出**（`__getattr__` 首次访问才构造，import 模块本身零副作用——CI 无 `.env` 也能 collect）；`knowledge_search_tool` 首次访问时实体化 `GaokaoKnowledge` 懒单例——离线构造 `OpenAIEmbeddings` + Chroma `PersistentClient`（文件句柄），**需 `.env` 有 `DASHSCOPE_API_KEY`**（缺失则访问时 `RuntimeError`）；不发网络请求、不计费
- 严禁 `import src.store.*`

## 挂载矩阵（读侧）

| 子 Agent | 挂载工具 |
|----------|----------|
| 搜索信息 | `LangchainKnowledgeSearchTool` + 业务查询工具（`search_questions` / `search_knowledge_notes` / `search_topics` 等） |
| VLM 理解 | `VLMUnderstandTool`（见 [ingest_tool.md](ingest_tool.md)，理解检索到的题图） |
| 聚合数据 | 业务查询工具（`get_error_stats` / `get_attempt_stats` / `aggregate_*` / `get_report`） |
| 输出整理 | —（纯 LLM 格式化，可选 `get_question_detail`） |

## 与门面的边界

- 读：本文件工具封装 `src.retrieval` 门面函数（含 `knowledge.get_knowledge()` 注入的 `GaokaoKnowledge` 检索）；子 Agent 不直接碰存储
- 语义检索走框架工具；结构化业务查询走薄封装——二者都经门面，杜绝「入口直连存储」
- 严禁 `import src.store.*`；违规 import 由 CI lint 拒绝（机制堵死「入口直连存储」）
