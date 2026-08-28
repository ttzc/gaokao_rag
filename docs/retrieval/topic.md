# topic — 知识点 tag 查询

知识点检索的轻量入口：按名字 / 别名查 tag、列出全部 tag、取单条 tag。不依赖向量，纯 `topics` 表查询 + 名字快照匹配。

## search_topics — 模糊查 tag

```python
def search_topics(keyword: str) -> list[TopicHit]:
```

**内部流程**：`src.store.db.topics.search_topic(keyword)`（按 `name` + `aliases` 并集模糊匹配），返回命中 tag 列表（含 name / aliases / 关联题数）。

## list_topics — 全量列出

```python
def list_topics() -> list[TopicHit]:
```

**内部流程**：`src.store.db.topics` 全表枚举，返回所有知识点 tag（用于"列出所有知识点"）。

## get_topic — 取单条

```python
def get_topic(name: str) -> TopicHit | None:
```

**内部流程**：精确按 `name` 取单条 tag；未命中返回 `None`。

> MVP 不做树展开：只查直接命中的 tag，无父→子孙上卷（树形升级路径见 [db/topics.md](../store/db/topics.md)）。
