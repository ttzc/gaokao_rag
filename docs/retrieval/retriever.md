# retriever — 联合召回（不分子意图）

`hybrid_search` 是检索门面的**底层召回原语**：题目与讲解在同一 Chroma Collection 一起召回，不按「题目 / 知识点」拆分入口。上层 `question` / `knowledge_note` 模块再用 `doc_type` 后过滤。

## hybrid_search — 语义 + 过滤联合召回

```python
from trpc_agent_sdk.knowledge import SearchResult

def hybrid_search(
    query: str,
    k: int = 8,
    where: KnowledgeFilterExpr | dict | None = None,
) -> SearchResult:
```

**内部流程**：

1. `get_knowledge()` 拿 `GaokaoKnowledge` 检索组件
2. 把 `where` 翻成 Chroma `where`：复用 `GaokaoKnowledge.build_search_extra_params`（`KnowledgeFilterExpr` → 原生 `where`，含 `$contains` / `$gte` 等）
3. 调用 `search()` 联合召回题目（`doc_type=question`）+ 讲解（`doc_type=note`），**原样返回框架 `SearchResult`**

**为什么不分子意图**：搜「离心率最值」可能同时命中题目与讲解，搜「分离参数法」以讲解为主——统一召回后由 Agent 综合，更符合真实查询分布（见 [README.md](README.md)「混合检索语义」）。

**返回与消费（不包装）**：直接复用框架 `SearchResult`（`documents: list[SearchDocument]`），**不另定义 `SearchHit` 之类包装层**（2026-08-28 决策：纯增代码量，`SearchDocument.document.metadata` 已能直取 `doc_type` / `topic_tags` / `doc_id` 等）。上层按 `doc.document.metadata[...]` 过滤：
- `question.search_questions` → 筛 `doc.document.metadata["doc_type"] == "question"`，用 `doc.document.metadata["doc_id"]` 回查 SQLite
- `knowledge_note.search_knowledge_notes` → 筛 `doc.document.metadata["doc_type"] == "note"`
