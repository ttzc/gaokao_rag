# 知识点查询 / tag 归位工具（src/agent/tools/knowledge_tool.py）

> 对应代码：`src/agent/tools/knowledge_tool.py`。知识点 FunctionTool，挂到 **搜索信息子 Agent**（查询侧，见 [../retrieval/search.md](../retrieval/search.md)）与 **知识整理子 Agent**（摄入侧，见 [../ingestion/knowledge_organize.md](../ingestion/knowledge_organize.md)）。

## 定位

摄入链路的"tag 管家"工具集。核心逻辑封装在 `src/ingestion/topic.py`（`resolve_or_create_topics` / `create_topic` / `add_topic_alias` / `delete_topic`，独立可测），本工具通过 FunctionTool 调用 ingestion 暴露的函数——**不直接 `import src.store.*`**。

## Tool 清单（3 个）

| Tool | 签名 | 用途 | 内建约束 |
| ---- | ---- | ---- | -------- |
| `search_topic` | (keyword) → [node] | 按名字/别名模糊查节点 | 归位第一步，防重复创建 |
| `create_topic` | (name, aliases=[]) → id | 新增 tag | 内部先 search 去重；name 全局 UNIQUE |
| `add_alias` | (topic_id, alias) | 同义表述归并 | 别名查重（防别名挂两个节点）|

## 归位流程（与 `topics` 表设计对应）

1. **开放式提取**：LLM 读取题目/讲解段，提取知识点名（不预定义候选集，允许新 tag）
2. **查表归位**：`search_topic` 按 name/aliases 查——命中复用已有节点；未命中 `create_topic` 新建
3. **别名归并**：同义表述（"离心率" vs "e=c/a"）`add_alias` 归并到同一节点

## 语义（名字即 tag）

- metadata 存名字快照（`topic_tags`），树演化（合并/移动/改名）不改 metadata
- 旧名归档 `aliases`，检索用 name + aliases 并集 + 树展开上卷
- 数据驱动非预定义 seed：树随摄入生长，动态演化

## MVP 不做的事

树形结构（父子关系 / 路径枚举 / 树展开上卷）、节点合并（merge）、节点移动（move）、软删（deactivate）。这些放在 MVP 后的正式版实现。
