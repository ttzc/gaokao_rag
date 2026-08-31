# 搜索信息 Agent（src/agent/retrieval/search.py）

> 对应代码：`src/agent/retrieval/search.py`（工厂 `create_search_agent()`）+ instruction 常量 `src/agent/retrieval/prompts.py`。**已落地（2026-08-29）**，注册进 Leader members（见 [leader.md](../leader.md)）。查询侧子 Agent 之一，**只调 `src/retrieval` 读门面（经 tools 层注入），严禁 `import src.store.*`**。

## 定位

查询链路的核心：混合检索（Chroma 语义 + SQLite 元数据），产出召回文档供下游聚合与生成。**不分子意图**——题目/知识点一起召回，由 LLM 综合组织。

## 挂载能力

**两个工具**（2026-08-31 起：`get_question_detail` 工具化后补挂）：

| 能力 | 说明 |
|------|------|
| `knowledge_search_tool`（工具名 `knowledge_search`） | `src/agent/tools/retrieve_tool.py` 的框架 `LangchainKnowledgeSearchTool`（top-10 纯相似度，不配过滤条件；`SIMILARITY_SCORE_THRESHOLD` 装配使 score 携带真实相关度——框架 SIMILARITY 分支 score 恒 0.0，见 [../tools/retrieve_tool.md](../tools/retrieve_tool.md)）；后续带条件检索时升级 `AgenticLangchainKnowledgeSearchTool`（LLM 自动构建 `KnowledgeFilterExpr`） |
| `get_question_detail_tool`（工具名 `get_question_detail`） | `src/agent/tools/retrieve_tool.py` 的业务查询 FunctionTool，薄封装 `src.retrieval.question.get_question_detail`——召回后**按需**按 `doc_id` 数字部分（`q_42` → 42）查单题完整详情（题干全文 / 答案 / 解析 / topic_names / 题号 / file_id / image_file_ids），单轮 ≤5 次、只查真实召回条目；dataclass 经 `asdict` 转 dict 返回 |

> `src/retrieval` 读门面其余业务查询函数（`browse_questions` 等）已有门面但**未工具化**——待封装为 FunctionTool 后逐个挂上（工具清单见 [../tools/retrieve_tool.md](../tools/retrieve_tool.md)）。
>
> **工具实体化纪律**（PEP 562 CI 教训）：`knowledge_search_tool` 是惰性导出，search.py 顶层只 `import retrieve_tool` 模块对象，工厂内才访问属性——防 import 期实体化 GaokaoKnowledge 崩 CI。回归测试见 `tests/test_agent_search.py::TestLazyToolMaterialization`。

## 混合检索设计说明

**不区分"搜题目"还是"搜知识点"**——题目 document 和讲解 document 在同一个 Chroma Collection，一起召回，由 LLM 综合组织答案：

- 搜"离心率最值怎么求" → 可能命中题目 + 讲解，LLM 既给解法又总结方法
- 搜"什么是分离参数法" → 命中讲解为主，LLM 自动带上相关例题
- 搜题目也能总结方法，搜方法也要配例题——**两者天然互补，无需按意图拆分检索**

> 现状注记：知识点讲解的摄取门面尚未落地，当前库里只有 `doc_type="question"` 的召回；检索语义按混合设计执行，note 类型随讲解摄取上线自动生效。

## 输入 / 输出契约

Leader 按「上下文隔离」打包委派（成员不回看对话）：

- **输入（task）**：用户问题原文 + Leader 提炼的关键词。
- **输出（纯文本，固定 Markdown 小标题）**：
  - `## search_results`：按 score 降序最多 10 条，每条含 `doc_id` / `doc_type` / `score` / `has_image` + 标题 / 知识点 / 来源 / 内容摘要（≤200 字，保留 LaTeX）；调用过 `get_question_detail` 的条目可放开摘要长度并追加 答案 / 解析 / 溯源 行（照实摘自详情返回，知识点以 topic_names 为准）
  - `## no_result`：全部检索无召回时输出，附尝试过的 query
- **详情补全（按需）**：摘要不够作答时按 doc_id 查 `get_question_detail`，**仅限 `doc_type="question"` 条目**（note 走 `kn_*` 前缀 doc_id，不适用），单轮累计 ≤5 次、禁止臆造 question_id。
- **弱召回改写**：空结果或不相关可换措辞重检，`knowledge_search` 累计调用 ≤3 次。
- 下游：Leader 依据结果综合作答（组织回答归 Leader，输出整理 Agent 未接入）；`has_image=True` 时作答注明图形信息暂不可用，后续触发 VLM 理解子 Agent（见 [vlm.md](vlm.md)）。

> **GaokaoState 注记**：原设计"检索结果写入 `GaokaoState.retrieved_docs`"依赖 State 层，TeamAgent 成员以文本返回值交接（同摄入侧 pending_questions 约定），State 落地时再收编。

## 红线（写进 instruction）

1. **只读**：工具仅 `knowledge_search` + `get_question_detail`（均只读），不写库、不调别的工具。
2. **不编造**：字段一律照实摘录工具返回；无召回报 `no_result`；详情只查真实召回的 doc_id，不臆造 question_id。
3. **不回答用户**：面向用户的解答由 Leader 组织，本 Agent 只交付召回清单。
4. **不改写内容**：摘要只截取不润色，不扁平化数学符号。
