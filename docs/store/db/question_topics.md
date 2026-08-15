# question_topics 表详解（题目-知识点关联）

## 功能定位

题目与知识点的**多对多关联表**——一道题可能涉及多个知识点（"椭圆离心率最值"同时挂"椭圆"和"离心率"）。是树（topics）与题目（questions）之间的枢纽，也是周报"薄弱知识点 Top 3"聚合的数据来源之一。

## Schema

```sql
CREATE TABLE question_topics (
    question_id  INTEGER NOT NULL REFERENCES questions(id),
    topic_id     INTEGER NOT NULL REFERENCES topics(id),
    is_primary   BOOLEAN DEFAULT 0,                 -- 是否是主要知识点
    created_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (question_id, topic_id)
);

CREATE INDEX idx_qt_question ON question_topics(question_id);
CREATE INDEX idxt_qt_topic ON question_topics(topic_id);
```

## 关键设计点

- **联合主键**（question_id, topic_id）：天然防重复关联
- **`is_primary`**：标记主要知识点（如"椭圆离心率"题，椭圆是主知识点、离心率是次知识点）——周报聚合薄弱点时可加权主知识点
- **标注流程**：摄取时 LLM 读取题目文本输出知识点**名字**列表（含同义表述）→ `search_topic` 归位获取 `topic_id` → 未命中则新建节点（pending）——**名字即 tag，不走编码**（见 `topics.md`）

## 常见操作

- 批量插入：一道题 N 个知识点，事务内插入
- 按题查知识点：`WHERE question_id = ?`
- 按知识点查题：`WHERE topic_id = ?`（配合树展开可查"某知识点及其全部子孙的题"）
- 聚合：`GROUP BY topic_id` 统计题目数/错题数（周报数据源）

## 与其他表的关系

| 关联 | 说明 |
| ---- | ---- |
| `questions.id` | 题目（被标注对象） |
| `topics.id` | 知识点节点（tag，经 `search_topic` 归位） |
| `topics.status` | 关联时跳过 merged 节点（已被合并，用 `merged_into` 指向的 target） |

> 与 Chroma metadata 的关系：本表存**结构化关联**（SQLite 精确过滤），`topic_tags`（名字快照）存**检索用快照**（语义过滤）——两者互补，摄入时同事务写入。
