# 知识检索对象（Layer 3b：Knowledge 构建侧）

> 对应 `src/store/vector/knowledge.py`（`GaokaoKnowledge(LangchainKnowledge)`）。负责**语义检索的查询侧**：把 embedder（`src/api/embedding.py`）+ vectorstore（`src/store/vector/vector_store.py`）注入框架 `LangchainKnowledge`，重写过滤翻译，供查询子 Agent 检索。存储读写侧见 [vector_store.md](./vector_store.md)。

## 框架集成：tRPC-Agent Knowledge 模块接入

> 本项目的语义检索**不重造轮子**：embedding（已落地 `src/api/embedding.py`）+ Chroma 封装（待落地 `src/store/vector/vector_store.py`）作为"可注入组件"，直接喂给 tRPC-Agent 的 `knowledge` 模块；检索能力（LLM 自动构建过滤、图节点编排）由框架提供。

### 模块分层（对照框架源码，非文档臆测）

tRPC-Agent 的 `knowledge` 模块分四层，彼此解耦：

1. **公开接口层 `knowledge/`**（框架无关）：定义契约 `KnowledgeBase`（ABC，抽象方法 `async search(ctx, req) -> SearchResult`）、`SearchRequest` / `SearchParams` / `SearchResult` / `SearchDocument`，以及统一过滤表达式 `KnowledgeFilterExpr`。**不含任何 LangChain 依赖**。
2. **默认实现 `LangchainKnowledge`**（`server/knowledge/langchain_knowledge.py`）：唯一真东西，构造函数 7 个可选参数全是 LangChain 接口类型——`embedder`(Embeddings) / `vectorstore`(VectorStore) / `retriever` / `document_loader` / `document_transformer` / `prompt_template` / `chain`。**框架不碰 embedding/向量库，纯等你注入**。`search()` 流程：拼 history → 校验 vectorstore/retriever → 取 query → `vectorstore.asearch(query, search_type, k=rank_top_k, **kwargs)` 或 retriever 重排。
3. **工具封装层 `tools/`**：`LangchainKnowledgeSearchTool`（静态 `knowledge_filter`）→ 升级版 `AgenticLangchainKnowledgeSearchTool`（额外暴露 `dynamic_filter`，LLM 运行时自生成，`and` 合并静态 + 动态后搜）。
4. **过滤系统 `KnowledgeFilterExpr`**（`knowledge/_filter_expr.py`）：支持 `eq/ne/gt/gte/lt/lte/in/not in/like/not like/between/and/or`，结构校验严格。

### 我们的接入映射

| 我们的组件 | 注入到 | 说明 |
| ---- | ---- | ---- |
| `get_embedding_model()` → OpenAIEmbeddings | `LangchainKnowledge.embedder` | 查询侧 Chroma 自带 embedding_function，search 路径不强制要 embedder，但显式传更清晰 |
| `get_vector_store().vectorstore` → Chroma("gaokao") | `LangchainKnowledge.vectorstore` | 持久化单例直接喂入（见 [vector_store.md](./vector_store.md)） |
| `AgenticLangchainKnowledgeSearchTool` | 查询侧子 Agent 工具 | 直接拿到"LLM 动态生成过滤条件"能力（MEMORY.md 既定方案） |
| `KnowledgeNodeAction` | TeamAgent 图节点 | 查询子 Agent 挂此节点即可，框架归一化结果写回图状态，不用自造工具编排 |

rag 层装配：

```python
knowledge = LangchainKnowledge(
    embedder=get_embedding_model(),
    vectorstore=get_vector_store().vectorstore,
)
```

### ⚠️ 必须自写 `GaokaoKnowledge` 子类

**关键发现**：`LangchainKnowledge` **没有重写** `build_search_extra_params`，默认返回 `{}`——即框架给了漂亮的 `KnowledgeFilterExpr` 模型和工具层管道，但**默认实现并不会把表达式翻译成向量库的 `where` 过滤**，只是把 `extra_params["langchain"]` 透传给 `vectorstore.asearch(**kwargs)`。

而我们的 metadata 过滤核心是数组语义（`topic_tags` `$contains`、`exam_regions` `$contains`）和标量比较（`exam_year` `$gte`），`$contains` 是 Chroma 专有、langchain Filter 语法不支持（见 [vector_store.md「Metadata 格式与过滤语义 · 实现注意」](./vector_store.md)）。**因此必须自写一个子类**，重写 `build_search_extra_params()` 把 `KnowledgeFilterExpr` 翻译成 Chroma 原生 `where`（含 `$contains`），否则 metadata 过滤等于没接上。

```python
# src/store/vector/knowledge.py（待落地）
class GaokaoKnowledge(LangchainKnowledge):
    def build_search_extra_params(self, filter_expr: KnowledgeFilterExpr) -> Dict[str, Any]:
        # 递归翻译 KnowledgeFilterExpr → chromadb where（含 $contains / $and / $or）
        ...
        return {"where": chroma_where}
```

### 工具：LangchainKnowledgeSearchTool vs Agentic

- `LangchainKnowledgeSearchTool`：包成名为 `knowledge_search` 的工具，只声明 `query` 一个参数；支持静态 `knowledge_filter` + `top_k` + `min_score`。
- **`AgenticLangchainKnowledgeSearchTool`（采用）**：在父类基础上额外暴露 `dynamic_filter`（`KnowledgeFilterExpr` JSON），LLM 可在运行时自生成过滤条件；动态 filter 与静态 `knowledge_filter` 用 `and` 合并后再搜。这正是"LLM 自动构建过滤条件"的官方实现——用户问"2026 年南昌一模椭圆题"，LLM 自动生成 `topic_tags contains "椭圆"` 等过滤条件。

```json
{
    "operator": "and",
    "value": [
        {"field": "metadata.subject", "operator": "eq", "value": "数学"},
        {"field": "metadata.exam_regions", "operator": "contains", "value": "南昌"},
        {"field": "metadata.exam_year", "operator": "eq", "value": 2026},
        {"field": "metadata.topic_tags", "operator": "contains", "value": "椭圆"}
    ]
}
```

### 图节点：KnowledgeNodeAction（TeamAgent 接入）

`dsl/graph/_node_action/_knowledge.py`：给 TeamAgent 图用的节点执行器。构造时给 `(query, tool)`，执行时跑 `tool.run_async(args={"query": ...})`，归一化结果成 `{documents:[{text,score,metadata}]}` 写回图状态（`STATE_KEY_LAST_RESPONSE` / `STATE_KEY_NODE_RESPONSES`）。我们主架构是 TeamAgent 多 Agent 编排，查询侧子 Agent 挂这个节点即可，不用自己写工具编排。

### 落地注意点（源码核实）

1. **别走 `create_vectorstore_from_document`**：它是 `afrom_documents` 便捷路径（classmethod，返回**新实例**，丢持久化目录）。我们的摄入走自己的 `src/store/vector/vector_store.py` 单例 + `add_documents`，`LangchainKnowledge` 只当查询检索器用（详见 [vector_store.md 坑 2](./vector_store.md)）。
2. **`prompt_template` 不要设**：它会在 embedding 前把 query 包一层 context/history 文本，反而污染检索向量。查询侧保持 `prompt_template=None`，原始 query 直接进 embedding。
3. **`SearchParams` 的 `top_p` / `rerank_threshold` / `generator_*` 字段声明了但基类未使用**（`LangchainParams` 仍是 TODO 空壳）——不要依赖它们，真正生效的只有 `search_type` + `rank_top_k` + `extra_params`。
4. **embedding 约束（查询侧同样生效）**：检索路径复用 `get_embedding_model()` 同一实例，故 [vector_store.md 坑 8](./vector_store.md) 的 `check_embedding_ctx_length=False` 必设约束对查询侧同样适用，不可删。

## 测试要点

- `tests/test_knowledge.py`：mock `get_vector_store()` 返回带 FakeEmbedder 的假 Chroma，验证 `GaokaoKnowledge.build_search_extra_params()` 正确翻译——`topic_tags` 数组字段生成 `$contains` + `$or`、`exam_year` 生成 `$gte/$lte`、嵌套 `and/or` 递归正确；并验证 `search()` 把 `where` 透传给底层 `vectorstore.asearch`。

## 与其他文档的关系

- 存储读写侧：[vector_store.md](./vector_store.md)（Chroma 封装、Metadata 格式与过滤语义、嵌入模型与维度决策、坑清单）
- 数据模型：[data_model.md](../../data_model.md)（Collection / doc_id / Document 策略）
- 知识树：[db/topics.md](../db/topics.md)（扁平 tag 注册表，MVP 无树形结构）
- 检索架构：[architecture.md](../../architecture.md)（Layer 3 语义检索总览）
- 配置：`config.toml` `[embedding]` / `[store]` 段（见 [vector_store.md](./vector_store.md)）
