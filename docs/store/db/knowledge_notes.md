# knowledge_notes 表详解（知识点讲解）

## 功能定位

存储讲义/学习资料中的**知识点讲解段**（概念、公式、典型方法），本质是**纯文本 RAG**——比带图的题目简单，不需要 VLM，文本切块向量化即可。用户问"什么是分离参数法"→ 命中讲解 document → 返回讲解 + 关联例题。

## Schema

```sql
CREATE TABLE knowledge_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT UNIQUE NOT NULL,          -- 与 Chroma 讲解 document 对应
    subject         TEXT NOT NULL,                  -- 学科: "数学" / "物理" / ...（查询热维度，冗余列，同 questions）
    topic_id        INTEGER REFERENCES topics(id),  -- 关联知识点树节点（可空，识别不出先挂 NULL）
    file_id         INTEGER REFERENCES files(id),  -- 所属资料/试卷（files 表，可空=散资料无来源）
    title           TEXT,                           -- 讲解标题（如"分离参数法"）
    content         TEXT NOT NULL,                  -- 讲解文本
    examples        TEXT,                           -- 关联例题引用 JSON: [question_id]
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_notes_subject ON knowledge_notes(subject);
CREATE INDEX idx_notes_topic ON knowledge_notes(topic_id);
CREATE INDEX idx_notes_file ON knowledge_notes(file_id);
```

## 关键设计点

- **纯文本路线**：content 向量化 → 讲解 document（`doc_id` 桥接 SQLite ↔ Chroma，同 questions 模式）
- **`topic_id` 可空**：识别不出知识点时先挂 NULL，后续归位补挂（不给摄入流程添堵）
- **检索价值**：复习建议可链接到具体讲解（"先看圆锥曲线讲义：分离参数法"）

## 常见操作

- 插入：`INSERT` + 同步写 Chroma 讲解 document（同事务）
- 按 topic 查：`WHERE topic_id = ?` 或经树展开（父节点 → 全部子孙的讲解）
- 删除：删行 + 删对应 Chroma document（`doc_id` 关联）

## 与其他表的关系

| 关联 | 说明 |
| ---- | ---- |
| `topics.topic_id` | 讲解归属的知识点节点（可空） |
| `examples` 字段 | JSON 数组引用 `questions.id`（讲解配套的例题） |
| Chroma | `doc_id` ↔ 讲解 document |

> 摄入链路：结构识别 Agent 分出"讲解段" → 知识整理 Agent 标知识点（挂 topics）→ 入库（本表 + Chroma）。
