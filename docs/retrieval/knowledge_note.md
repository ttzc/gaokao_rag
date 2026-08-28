# knowledge_note — 讲解段语义检索

检索 `doc_type == "note"` 的讲解段（概念、公式、典型方法），是「分离参数法」这类以讲解为主的查询入口。

## search_knowledge_notes — 讲解语义召回

```python
def search_knowledge_notes(
    query: str,
    k: int = 6,
    filter_expr: KnowledgeFilterExpr | dict | None = None,
) -> list[KnowledgeNoteHit]:
```

**内部流程**：

1. `get_knowledge().search(query, k, filter_expr)` 返回框架 `SearchResult`（见 [knowledge.md](knowledge.md)）
2. 遍历 `result.documents`，仅保留 `doc.document.metadata["doc_type"] == "note"` 的命中
3. 用 `doc.document.metadata["doc_id"]` 回查 `knowledge_notes` 表补全（关联 topic_id、来源 source）

**返回**：`KnowledgeNoteHit` 列表（讲解文本摘要 + 关联知识点 + 来源）。

> 与 `question.search_questions` 共用 `get_knowledge().search` 底层，只是后过滤的 `doc_type` 不同——题目与讲解在同一 Collection 联合召回，由 Agent 综合。
