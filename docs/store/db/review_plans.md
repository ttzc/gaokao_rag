# review_plans 表详解（复习计划）

## 功能定位

存储生成的**复习建议/计划**（如"本周重点：离心率相关 3 题 + 导数单调性"），支撑"复习"意图（review）——用户问"我该复习什么"时，聚合数据 Agent 生成计划落库，可回溯、可对比。

## Schema

```sql
CREATE TABLE review_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,                  -- 用户标识（MVP 固定单一用户）
    plan_type       TEXT NOT NULL,                  -- "knowledge_gap" / "exam_review" / "custom"
    target_topics   TEXT,                            -- 目标知识点 JSON 数组
    description     TEXT,                            -- 建议内容
    priority        INTEGER DEFAULT 3,               -- 1-5 优先级
    created_at      TEXT DEFAULT (datetime('now')),
    completed_at    TEXT
);

CREATE INDEX idx_review_user ON review_plans(user_id);
```

## 关键设计点

- **`plan_type` 三来源**：
  - `knowledge_gap`：错题聚合发现的知识缺口（来自 errors × topics）
  - `exam_review`：考后试卷分析（来自 exam_attempts.answer_summary）
  - `custom`：用户自定义/其他
- **`target_topics`**：目标知识点名字数组（`["椭圆", "离心率"]`）——配合 `topics` 树的展开做"推荐题检索"
- **`completed_at`**：计划完成闭环标记（空 = 未完成；MVP 由用户/聚合 Agent 标记）

## 常见操作

- 生成：聚合 Agent 分析 errors/exam_attempts → 本表
- 查未完成：`WHERE user_id = ? AND completed_at IS NULL ORDER BY priority DESC`
- 完成：`UPDATE ... SET completed_at = datetime('now')`

## 与其他表的关系

| 关联 | 说明 |
| ---- | ---- |
| `topics` | `target_topics` 存知识点名字，直接匹配检索推荐题 |
| `errors` / `exam_attempts` | 计划生成的数据来源 |
| `periodic_reports` | 周报的 recommendation 字段与 review_plans 可互相引用 |

> MVP 定位轻量：先记录"计划什么"，不做过重的计划执行/进度追踪（那是 V1.1+ 的事）。
