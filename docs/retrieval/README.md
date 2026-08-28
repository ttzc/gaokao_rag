# 检索门面（读侧）

`src/retrieval/` 是 Gaokao RAG 的**查询与聚合门面**：所有「读」数据的行为（语义检索、结构化过滤、统计聚合、周报计算）都必须经由本包暴露的函数。它是摄入侧 `src/ingestion/` 的阅读镜像——一个负责写三态一致，一个负责读逻辑封装。

> 本目录是原 `docs/retrieval.md` 拆分后的检索门面文档。**目录结构与 `src/retrieval/` 一一对应：每个 `.py` 文件对应一个 `.md` 文件**。

---

## 定位：只读不写

- 本包**不允许修改**三层存储（不调用任何 store 原语的写方法）。
- 只允许组合：`src.store.db.*` 的查询方法、`src.store.vector.get_vector_store().search()`，以及 `src.store.vector.knowledge.get_knowledge()`（框架 `GaokaoKnowledge` 检索组件）。
- 返回给上层的是**业务语义对象**（题目详情、错题分布、薄弱点、周报聚合），而非裸 SQLite Row / Chroma Document。

---

## 模块划分

| 模块 | 关键函数 | 文档 |
| ---- | -------- | ---- |
| `question.py` | `search_questions` / `get_question_detail` / `browse_questions` | [question.md](question.md) |
| `knowledge_note.py` | `search_knowledge_notes` | [knowledge_note.md](knowledge_note.md) |
| `topic.py` | `search_topics` / `list_topics` / `get_topic` | [topic.md](topic.md) |
| `error.py` | `get_error_stats` / `get_error_details` / `get_weak_topics` | [error.md](error.md) |
| `exam_attempt.py` | `get_attempt_stats` | [exam_attempt.md](exam_attempt.md) |
| `report.py` | `aggregate_errors` / `aggregate_attempts` / `get_report` / `compute_trend` | [report.md](report.md) |
| `retriever.py` | `hybrid_search` | [retriever.md](retriever.md) |

---

## 混合检索语义

与架构约定一致——搜「离心率最值」可能同时命中题目与讲解，搜「分离参数法」以讲解为主。因此检索门面默认走 `hybrid_search` 联合召回，不按「题目 / 知识点」拆分检索入口。具体过滤翻译复用 `src.store.vector.knowledge.GaokaoKnowledge.build_search_extra_params`（将 `KnowledgeFilterExpr` 翻成 Chroma `where`）。

---

## 与 ingestion 的边界

- 写入永远走 `ingestion`；本包只 `import` store 的**查询**能力。
- Agent 的 tool（见 [Agent 编排设计](../agent/README.md)）只调用本包与 `ingestion` 的函数，不得在 tool 内直接 `import src.store.*`。

---

## 目录导航（与 src/retrieval/ 一一对应）

| 本文档 | 对应 `src/retrieval/` | 内容 |
|--------|------------------------|------|
| [README.md](README.md) | （包说明） | 总览、只读不写定位、混合检索语义、与 ingestion 边界、导航 |
| [question.md](question.md) | `question.py` | 题目混合召回 + 详情 + 浏览 |
| [knowledge_note.md](knowledge_note.md) | `knowledge_note.py` | 讲解段语义检索 |
| [topic.md](topic.md) | `topic.py` | 知识点 tag 查询 |
| [error.md](error.md) | `error.py` | 错题统计 + 薄弱知识点识别 |
| [exam_attempt.md](exam_attempt.md) | `exam_attempt.py` | 整卷作答统计 |
| [report.md](report.md) | `report.py` | 周报 / 月报双源聚合 + 趋势 |
| [retriever.md](retriever.md) | `retriever.py` | 不分子意图的联合召回 |
