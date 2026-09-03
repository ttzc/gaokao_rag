# question_topics 表详解（题目-知识点关联）

## 功能定位

题目与知识点的**多对多关联表**——一道题可能涉及多个知识点（"椭圆离心率最值"同时挂"椭圆"和"离心率"）。MVP 版本为纯粹的关联表，`topic_name` 直接存知识点规范名，无树形展开。

> **MVP 与正式版的分界**：正式版会引入知识点树形结构，届时 `topic_name` 可升级为 `topic_id`，并支持树展开（父节点 → 全部子孙）的递归查询。MVP 阶段只做直接 tag 匹配。

## Schema

```sql
CREATE TABLE question_topics (
    question_id  INTEGER NOT NULL REFERENCES questions(id),
    topic_name   TEXT NOT NULL,                     -- 知识点规范名（tag）
    is_primary   BOOLEAN DEFAULT 0,                 -- 是否是主要知识点
    created_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (question_id, topic_name)
);

CREATE INDEX idx_qt_question ON question_topics(question_id);
CREATE INDEX idx_qt_topic ON question_topics(topic_name);
```

## 关键设计点

- **关联存名字而非 `topic_id`**：MVP 虽无树结构，但为正式版预留升级空间——名字是稳定 tag，即使后续 topics 表加树结构，名字也不会变
- **联合主键**（question_id, topic_name）：天然防重复关联
- **`is_primary`**：标记主要知识点（如"椭圆离心率"题，椭圆是主知识点、离心率是次知识点）——周报聚合薄弱点时可加权主知识点
- **标注流程**：摄取时 LLM 输出知识点名字列表 → 查 `topics` 表确认规范名 → 存该名字

## 常见操作

- 批量插入：一道题 N 个知识点（规范名），事务内插入
- 按题查知识点：`WHERE question_id = ?`
- 按知识点查题：`WHERE topic_name = ?` 或 `WHERE topic_name IN (?)`（MVP 直接匹配，无树展开）
- 聚合：`GROUP BY topic_name` 统计题目数/错题数（周报数据源）
- 删除：单条 `remove(question_id, topic_name)` 返回 bool；按题清空 `remove_by_question(question_id)` 返回删除条数（0 = 该题本无关联，非错误）——改题的全量替换（先清空再 `add_many`）与删题的级联都依赖后者；只清关联行，不动 `topics` 表节点

## 与其他表的关系

| 关联 | 说明 |
| ---- | ---- |
| `questions.id` | 题目（被标注对象，id 不变，正常外键） |
| `topics.name` | 知识点 tag（规范名，经 `search_topic` 归位确认） |

> 与 Chroma metadata 的关系：本表存**结构化关联**（SQLite 精确过滤），`topic_tags`（名字快照）存**检索用快照**（语义过滤，`$contains` 直接匹配，无树展开）——两者同构（都存名字），摄入时同事务写入。格式见 [vector_store.md「Metadata 格式与过滤语义」](../vector/vector_store.md)。
