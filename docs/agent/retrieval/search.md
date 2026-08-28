# 搜索信息 Agent（src/agent/retrieval/search.py）

> 对应代码：`src/agent/retrieval/search.py`。查询侧子 Agent 之一，**只调 `src/retrieval` 读门面（retriever / question / knowledge_note / topic 查询），严禁 `import src.store.*`**。

## 定位

查询链路的核心：混合检索（Chroma 语义 + SQLite 元数据），产出召回文档供下游聚合与生成。**不分子意图**——题目/知识点一起召回，由 LLM 综合组织。

## 挂载能力

| 能力 | 说明 |
|------|------|
| `LangchainKnowledgeSearchTool` | tRPC-Agent 内置，纯向量相似度检索（MVP 不配过滤条件）；后续带条件检索时升级 `AgenticLangchainKnowledgeSearchTool`（LLM 自动构建 `KnowledgeFilterExpr`） |
| `src/retrieval` 读门面 | retriever / question / knowledge_note / topic 的查询函数（回查 SQLite 权威数据、文件层） |

## 混合检索设计说明

**不区分"搜题目"还是"搜知识点"**——题目 document 和讲解 document 在同一个 Chroma Collection，一起召回，由 LLM 综合组织答案：

- 搜"离心率最值怎么求" → 可能命中题目 + 讲解，LLM 既给解法又总结方法
- 搜"什么是分离参数法" → 命中讲解为主，LLM 自动带上相关例题
- 搜题目也能总结方法，搜方法也要配例题——**两者天然互补，无需按意图拆分检索**

## 召回后处理

- 检索结果写入 `GaokaoState.retrieved_docs`（含 doc_id、content、has_image 标志）
- 下游：聚合数据/输出整理消费；`has_image=True` 时触发 VLM 理解子 Agent（见 [vlm.md](vlm.md)）
