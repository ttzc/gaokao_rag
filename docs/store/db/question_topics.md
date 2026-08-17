# question_topics 表详解（题目-知识点关联）

## 功能定位

题目与知识点的**多对多关联表**——一道题可能涉及多个知识点（"椭圆离心率最值"同时挂"椭圆"和"离心率"）。是树（topics）与题目（questions）之间的枢纽，也是周报"薄弱知识点 Top 3"聚合的数据来源之一。

## Schema

```sql
CREATE TABLE question_topics (
    question_id  INTEGER NOT NULL REFERENCES questions(id),
    topic_name   TEXT NOT NULL,                     -- 知识点名字（tag，不存 id——树结构可调整，名字稳定）
    is_primary   BOOLEAN DEFAULT 0,                 -- 是否是主要知识点
    created_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (question_id, topic_name)
);

CREATE INDEX idx_qt_question ON question_topics(question_id);
CREATE INDEX idx_qt_topic ON question_topics(topic_name);
```

## 关键设计点

- **关联存名字而非 `topic_id`（核心设计）**：知识树会演化（合并/移动/改名），id 不稳定——节点被合并后 id 失效，关联就断了。**名字是稳定 tag**（合并时旧名归档进 `aliases`），树怎么调整关联都不受影响，与"名字即 tag"（见 `topics.md`）完全一致
- **联合主键**（question_id, topic_name）：天然防重复关联
- **`is_primary`**：标记主要知识点（如"椭圆离心率"题，椭圆是主知识点、离心率是次知识点）——周报聚合薄弱点时可加权主知识点
- **标注流程**：LLM 输出知识点名字列表（含同义表述）→ `search_topic` 归位**确认规范名字**（命中取规范名 / 未命中新建 pending 后取其名）→ 存该名字
- **树合并/改名后无需更新本表**：旧名留在 aliases 里，检索按"name + aliases"并集匹配依然命中——零维护成本

## 常见操作

- 批量插入：一道题 N 个知识点（规范名），事务内插入
- 按题查知识点：`WHERE question_id = ?`
- 按知识点查题：`WHERE topic_name IN (树展开的名字集)`——经 `expand_tag_names` 取该节点及全部子孙的 name+aliases 并集
- 聚合：`GROUP BY topic_name` 统计题目数/错题数（周报数据源）

## 与其他表的关系

| 关联 | 说明 |
| ---- | ---- |
| `questions.id` | 题目（被标注对象，id 不变，正常外键） |
| `topics.name` / `aliases` | 知识点 tag（名字，经 `search_topic` 归位确认；树演化后按名字匹配） |

> 与 Chroma metadata 的关系：本表存**结构化关联**（SQLite 精确过滤），`topic_tags`（名字快照）存**检索用快照**（语义过滤）——两者同构（都存名字），摄入时同事务写入。
