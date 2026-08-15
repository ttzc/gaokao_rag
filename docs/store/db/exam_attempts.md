# exam_attempts 表详解（试卷作答记录）

## 功能定位

记录**一次完整模考/作业的整卷表现**（总分、正确率、逐题对错、用时），回答"这张卷整体考得怎样"（卷子粒度）。与 `errors` 分工互补，是周报的另一数据源。

## Schema

```sql
CREATE TABLE exam_attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,                  -- 用户标识（MVP 固定单一用户）
    source_file     TEXT NOT NULL,                  -- 关联试卷: "2026_南昌一模.pdf"
    attempt_date    TEXT NOT NULL,                  -- 作答日期
    total_score     REAL,                           -- 卷面得分
    max_score       REAL,                           -- 满分（如 150）
    time_spent      INTEGER,                        -- 用时（分钟）
    question_results TEXT,                          -- 逐题对错 JSON: [{question_id, score, correct}]
    answer_summary  TEXT,                           -- LLM 生成的整卷分析（薄弱题型/失分点/建议）
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_attempts_user_date ON exam_attempts(user_id, attempt_date);
CREATE INDEX idx_attempts_source ON exam_attempts(source_file);
```

## 关键设计点

### 作答录入（核心交互）：用户口述 + LLM 解析，不识别成绩单

与错题录入一致——**不依赖识别手写成绩单**（VLM 手写识别不可靠）：

- 用户口述："南昌一模做了，选择错 2 个、填空错 1 个，大题导数没写出来，总分 68"（可拍照成绩单作辅助输入）
- LLM 解析为 `question_results`（按题号匹配 `questions` 表）+ `total_score` + `answer_summary`

### 逐题对错 JSON

```json
[
  {"question_id": 12, "score": 5, "correct": true},
  {"question_id": 13, "score": 0, "correct": false}
]
```

- `question_id` 关联 `questions` 表（卷子已入库时）；未入库的题用题号占位
- 周报可算"失分题型"：按 question_type 分组错题

## 常见操作

- 录入：口述 → LLM 结构化 → 本表（事务）
- 按用户+时间窗查：`WHERE user_id = ? AND attempt_date BETWEEN ...`
- 聚合：平均分、正确率、失分题型（周报数据源）

## 与其他表的关系

| 关联 | 说明 |
| ---- | ---- |
| `questions.id` | 逐题对错引用的题目（`question_results` JSON） |
| `periodic_reports` | 周报按窗口聚合本表（作答次数、平均分、失分题型） |
| `errors` | 互补：errors 按题、本表按卷 |

> 与 errors 的双源聚合（周报）：`errors` 算薄弱知识点（题目粒度）+ 本表算整体表现（卷子粒度）→ 合并进 `periodic_reports` 快照（见 `periodic_reports.md`）。
