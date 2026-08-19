# 向量存储（Layer 3：Chroma）

> 对应 `src/store/vector_store.py`（Chroma 封装）+ `src/api/embedding.py`（嵌入模型客户端）。负责**语义检索**：文本 → 向量 → Chroma collection，供搜索子 Agent 按语义召回（配合 SQLite 精确过滤形成混合检索，见 [architecture.md](../architecture.md)）。

## 功能定位

三层存储的**最顶层索引**——SQLite 负责"精确过滤"（知识点/年份/考区），Chroma 负责"语义相似"（按问题含义检索），两层通过 `doc_id` 桥接。设计细节（Collection / doc_id / Metadata 规范）见 [data_model.md](../data_model.md)「Chroma 向量层」一节，本文档只记录**实现层面的开发细节与踩坑**。

## 技术选型：langchain-chroma（不直接用原生 chromadb）

| 方案 | 说明 | 结论 |
| ---- | ---- | ---- |
| 原生 `chromadb.Client` | data_model.md 早期示例的写法 | ✗ 不满足 A 方案 |
| **`langchain_chroma.Chroma`** | 实现 langchain `VectorStore` 接口（`asearch` / `afrom_documents` / `similarity_search_with_relevance_scores`） | ✓ **采用** |

**原因**：`LangchainKnowledge.search()` 内部调用 `vectorstore.asearch()`（见 [tRPC-Agent 源码](../../../../learn/trpc-agent-python/trpc_agent_sdk/server/knowledge/langchain_knowledge.py)），要求 vectorstore 是 langchain `VectorStore` 接口实现。原生 chromadb API 满足不了，故统一走 langchain Chroma，**不要混用两种 API 操作同一个 `chroma_db`**（避免双写不一致）。

## 向量维度（核心决策，必须显式指定）

### 为什么必须规定维度

AlgoNotes 踩过的坑（`algonotes_rag/issues/IJVLRZ.md`）：**同一个模型 `Qwen/Qwen3-Embedding-4B`，不传维度参数时不同平台返回不同维度**——

| 平台 | 返回维度 |
| ---- | -------- |
| Gitee.AI | 1024 |
| SiliconFlow | 2560 |

而 Chroma collection 一旦建好维度就固定（AlgoNotes `docs/STORE.md` 教训：**更换 Embedding 模型时必须先删除旧 collection 再重建，否则维度冲突报错**）。连锁后果：不显式固定维度，将来换平台/换模型名，库里已有向量与新向量维度不一致，整个检索直接崩。

**决策：`config.toml` 的 `[embedding]` 段规定 `dimension`，请求显式传 `dimensions` 参数，不依赖模型/平台默认值。**

### 官方 API 确认（platform.qianwenai.com text-embedding 文档）

| 端点 | 参数名 | 默认值 | 可选值 |
| ---- | ------ | ------ | ------ |
| OpenAI 兼容（`/compatible-mode/v1/embeddings`） | **`dimensions`**（复数） | 1024 | 2048 / 1536 / 1024 / 768 / 512 / 256 / 128 / 64（2048/1536/256/128/64 仅 v4） |
| DashScope 原生（`/api/v1/services/embeddings/...`） | **`dimension`**（单数） | 1024 | 同上 |

本走 **OpenAI 兼容端点 + openai SDK**，SDK 里传 `dimensions=config.embedding.dimension`。

> ⚠️ 官方文档模型表只列了 `text-embedding-v3/v4`，`qwen3-embedding-4b` 对 `dimensions` 的可选值范围**未写明**——实现时先冒烟测试（传 `dimensions=1024` 看是否接受、返回是否 1024 维）再定白名单。若实测不支持该参数，改为"请求不传、校验返回维度 == config.dimension"。

### 配置

```toml
# config.toml
[embedding]
model = "qwen3-embedding-4b"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key = "${DASHSCOPE_API_KEY}"
timeout = 60.0
dimension = 1024          # 向量维度，显式指定；换值需重建 collection

[store]
data_dir = "data"
collection_name = "gaokao"   # Chroma collection 名（全科共用，metadata.subject 过滤学科）
```

`src/config.py`：`EmbeddingConfig.dimension`（默认 1024）+ `StoreConfig.collection_name`（默认 "gaokao"）。

### 维度一致性防呆（vector_store.py）

```python
def __init__(self, collection_name: str, persist_dir: str, expected_dim: int) -> None:
    self.vectorstore = Chroma(
        collection_name=collection_name,
        embedding_function=get_embedding_model(),
        persist_directory=persist_dir,   # 必须显式传，见「坑 2」
    )
    existing = self.vectorstore.get(limit=1)
    if existing["ids"] and len(existing["embeddings"][0]) != expected_dim:
        raise RuntimeError(
            f"Chroma collection '{collection_name}' 现有向量维度 "
            f"{len(existing['embeddings'][0])} 与 config embedding.dimension={expected_dim} 不一致。"
            "更换维度需先删除 data/chroma_db 重建。"
        )
```

**Chroma 不预指定维度**（对比腾讯云向量库的 `IndexParams(dimension=768)`）：以第一批入库向量的维度为准，因此防呆校验放在初始化时。

## 组件装配

```mermaid
flowchart LR
    subgraph api层
        EMB[src/api/embedding.py<br/>QwenEmbeddingModel : Embeddings 接口<br/>embed_query / embed_documents + async]
    end
    subgraph store层
        VS[src/store/vector_store.py<br/>VectorStore : langchain Chroma<br/>collection "gaokao" · doc_id 幂等 upsert]
    end
    subgraph 框架层
        KB[LangchainKnowledge<br/>embedder + vectorstore 注入]
    end
    EMB -->|embedding_function=| VS
    EMB -->|embedder=| KB
    VS -->|vectorstore=| KB
    EMB -->|dimensions=1024| API[DashScope /embeddings<br/>Qwen3-Embedding-4B]
    VS -->|persist_directory| DB[(data/chroma_db)]
```

### src/api/embedding.py —— embedder（实现 Embeddings 接口）

- `QwenEmbeddingModel(Embeddings)`：继承 `langchain_core.embeddings.Embeddings`，实现 `embed_query` / `embed_documents` + async 版本。**这是 A 方案的关键**——该接口恰好就是 Chroma 的 `embedding_function` 和 `LangchainKnowledge.embedder` 需要的形状，api 层定义能力、store/rag 层消费
- 内部用 openai SDK（`OpenAI` + `AsyncOpenAI` 双客户端，sync/async 各一份）直调 DashScope 兼容端点 `/embeddings`
- 调用时**显式传 `dimensions=cfg.dimension`**
- `get_embedding_model()` 懒初始化单例，风格对齐 llm.py/vlm.py：白名单（`_SUPPORTED_MODELS`）+ `${VAR}` 占位符检查 + 初始化日志
- **`_BATCH_SIZE = 10`**：DashScope `/embeddings` 单次 `input` 字符串数组上限 **10 条**（官方文档确认；比 Gitee.AI 的 25 条更严）

### src/store/vector_store.py —— Chroma 封装

| 方法 | 用途 |
| ---- | ---- |
| `upsert(doc_id, text, metadata)` / `upsert_many(...)` | 写入/更新，**doc_id 幂等**（同 id 覆盖，重复摄入不产生重复 document） |
| `search(query, k, where)` | 语义检索 + metadata 过滤（langchain Filter 语法，与 AgenticLangchainKnowledgeSearchTool 一致）→ `(Document, score)` |
| `delete(doc_ids)` | 删除题目/讲解时同步删对应 document |
| `get(doc_id)` | 按 id 取 document（更新前查重、一致性校验） |
| `count()` | collection 内 document 总数 |
| `vectorstore` 属性 | 底层 Chroma 实例，**供 LangchainKnowledge 注入** |

`get_vector_store()` 懒初始化单例。

### rag 层装配

```python
knowledge = LangchainKnowledge(
    embedder=get_embedding_model(),
    vectorstore=get_vector_store().vectorstore,
)
```

## 坑清单（实现时必看）

1. **doc_id 格式**：权威格式是两段式 `q_42` / `kn_7`（data_model.md「doc_id 生成规则」+ `questions.py::_make_doc_id`）；曾有文档示例写成 `q_42_question` / `q_001_question`，已修正
2. **`afrom_documents` 会重建实例**：`LangchainKnowledge.create_vectorstore_from_document()` 内部调 `vectorstore.afrom_documents(...)`（classmethod，返回**新实例**）——注入的 Chroma 必须带 `persist_directory` + `embedding_function`，否则新实例落到默认临时目录，**数据直接丢**
3. **维度不写死、但 collection 建后固定**：维度由 config 规定（见上）；换维度 = 删 `data/chroma_db` 重建（AlgoNotes STORE.md 教训）
4. **批量上限**：DashScope 单次 ≤10 条（`_BATCH_SIZE=10`）；Gitee.AI ≤25（历史踩坑，当前不用）
5. **不混用原生 chromadb API 与 langchain Chroma**（同库双写会不一致）

## 测试要点

- `tests/test_embedding.py`：monkeypatch `src.api.embedding.OpenAI` 为 fake client——验证 `embed_query` 单条、`embed_documents` 超 10 条拆两批、`dimensions` 透传、`${VAR}` 占位符报错、白名单校验
- `tests/test_vector_store.py`：`tmp_path` 持久化 + FakeEmbedder（定长向量，不真调 DashScope）——验证 upsert 幂等（同 doc_id 覆盖不重复）、`search` 的 where 过滤、`delete` 后 `get` 为空、**维度防呆报错**

## 与其他文档的关系

- 数据模型：[data_model.md](../data_model.md)（Collection / doc_id / Metadata 规范）
- 题目表：[db/questions.md](db/questions.md)（`doc_id` 桥接、`has_image` 过滤快照）
- 讲解表：[db/knowledge_notes.md](db/knowledge_notes.md)（`kn_*` document）
- 知识树：[db/topics.md](db/topics.md)（`topic_tags` 名字快照 + 树展开上卷）
- 框架集成：[architecture.md](../architecture.md)（LangchainKnowledge + AgenticLangchainKnowledgeSearchTool）
- 配置：`config.toml` `[embedding]` / `[store]` 段（`dimension` / `collection_name`）
