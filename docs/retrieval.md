# 检索门面（读侧）

`src/retrieval/` 是 Gaokao RAG 的**查询与聚合门面**：所有「读」数据的行为（语义检索、结构化过滤、统计聚合、周报计算）都必须经由本包暴露的函数。它是摄入侧 `src/ingestion/` 的阅读镜像——一个负责写三态一致，一个负责读逻辑封装。

---

## 定位：只读不写

- 本包**不允许修改**三层存储（不调用任何 store 原语的写方法）。
- 只允许组合：`src.store.db.*` 的查询方法、`src.store.vector.get_vector_store().search()`，以及 `src.store.vector.knowledge.get_knowledge()`（框架 `GaokaoKnowledge` 检索组件）。
- 返回给上层的是**业务语义对象**（题目详情、错题分布、薄弱点、周报聚合），而非裸 SQLite Row / Chroma Document。

---

## 模块划分

| 模块 | 关键函数 | 说明 |
| ---- | -------- | ---- |
| `question.py` | `search_questions(query, k, where)`、`get_question_detail(question_id)`、`browse_questions(filters)` | 混合召回（Chroma 语义 + SQLite 过滤）后回查 SQLite 补全结构化字段；`get_question_detail` 返回题目 + 关联知识点 + 图片 file_id 列表 |
| `knowledge_note.py` | `search_knowledge_notes(query, k, where)` | 讲解段语义检索（`doc_type=knowledge`） |
| `topic.py` | `search_topics(keyword)`、`list_topics()`、`get_topic(name)` | 知识点 tag 查询（name + aliases 并集） |
| `error.py` | `get_error_stats(user_id)`、`get_error_details(question_id)`、`get_weak_topics(user_id)` | 错题统计 + 薄弱知识点识别 |
| `exam_attempt.py` | `get_attempt_stats(user_id, start, end)` | 整卷作答统计（失分题型等） |
| `report.py` | `aggregate_errors(...)`、`aggregate_attempts(...)`、`get_report(...)`、`compute_trend(...)` | 周报/月报双源聚合 + 趋势；`get_report` 读 `periodic_reports` 缓存 |
| `retriever.py` | `hybrid_search(query, k, where)` | **不分子意图**：题目 + 讲解同一 Collection 一起召回，由 Agent 综合 |

---

## 混合检索语义

与架构约定一致——搜「离心率最值」可能同时命中题目与讲解，搜「分离参数法」以讲解为主。因此检索门面默认走 `hybrid_search` 联合召回，不按「题目 / 知识点」拆分检索入口。具体过滤翻译复用 `src.store.vector.knowledge.GaokaoKnowledge.build_search_extra_params`（将 `KnowledgeFilterExpr` 翻成 Chroma `where`）。

---

## 与 ingestion 的边界

- 写入永远走 `ingestion`；本包只 `import` store 的**查询**能力。
- Agent 的 tool（见 [Agent 设计](agent.md)）只调用本包与 `ingestion` 的函数，不得在 tool 内直接 `import src.store.*`。
