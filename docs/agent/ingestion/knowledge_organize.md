# 知识整理 Agent（src/agent/ingestion/knowledge_organize.py）

> 对应代码：`src/agent/ingestion/knowledge_organize.py`。摄入侧子 Agent 之一，摄入链路的"tag 管家"。**只调 `src/ingestion` 写门面暴露的归位函数，严禁 `import src.store.*`**。

## 定位

对题目与讲解段做知识点标注，与 `topics` 表交互实现 tag 归位。核心逻辑封装在 `src/ingestion/topic.py`（`resolve_or_create_topics` / `create_topic` / `add_topic_alias` / `delete_topic`，独立可测），本 Agent 通过 knowledge_tool（FunctionTool）调用（见 [../tools/knowledge_tool.md](../tools/knowledge_tool.md)）。

## 双路由知识提取

知识整理同时覆盖**题目段**与**讲解段**（2026-08 决策——两个来源都处理）：

| 输入 | 标注去向 | 写入动作 |
|------|----------|----------|
| 题目段（`pending_questions`） | `question_topics` 关联（question_id + topic_name 列表） | 随 `ingest_question` 写入 |
| 讲解段（`lecture_segments`） | `knowledge_notes.topic_tags`（名字快照 + topic_id 关联） | 讲解自动入库时写入 |

两条路由都走同一套 tag 归位原语，不重复实现。

## 归位流程（与 `topics` 表设计对应）

1. **开放式提取**：LLM 读取题目/讲解段，提取知识点名（不预定义候选集，允许新 tag）
2. **查表归位**：`search_topic` 按 name/aliases 查——命中复用已有节点；未命中 `create_topic` 新建
3. **别名归并**：同义表述（"离心率" vs "e=c/a"）`add_alias` 归并到同一节点

## 挂载工具

| Tool | 签名 | 用途 |
| ---- | ---- | ---- |
| `search_topic` | (keyword) → [node] | 按名字/别名模糊查节点 |
| `create_topic` | (name, aliases=[]) → id | 新增 tag（内部先 search 去重） |
| `add_alias` | (topic_id, alias) | 同义表述归并（别名查重） |

## 决策原则

- 开放式提取：LLM 读取题目文本，提取知识点名（不预定义候选集）
- 归位优先：先查 `topics` 表（含 aliases），命中复用，未命中新建
- 同义合并：语义等价时写入 aliases 而非新建节点
- 知识树动态演化：树随数据摄入生长，不预定义

## MVP 不做的事

树形结构（父子关系 / 路径枚举 / 树展开上卷）、节点合并（merge）、节点移动（move）、软删（deactivate）。这些放在 MVP 后的正式版实现。

## 输出（State 契约）

| 字段 | 内容 |
|------|------|
| `topic_draft` | 每题知识点草案（topic_name 列表，待归位） |

数据流见 [README.md 摄入侧数据流契约](../README.md)。
