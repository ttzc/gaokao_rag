# 数据模型与知识点图谱

## 概述

### 一句话模型

> **Gaokao RAG 的数据 = 题目 + 知识点，两者分开处理**：
>
> - **知识点**（资料/专题/作业中的讲解段）→ 只需挂到知识树（`knowledge_notes.topic_id` → `topics`），纯文本 RAG
> - **题目**（试卷/作业中的题）→ 记录完整四要素：**题目内容（含图片描述）+ 答案解析 + 错题错因 + 知识点关联**
> - **考试试卷**额外记录整卷作答情况（`exam_attempts`）；作业试卷不需要
> - 所有输入走**统一摄入范式**：结构识别 → 讲解/题目分流 → 回显确认 → 用户决定去向

### 存储分层

Gaokao RAG 的数据模型分为两部分：

1. **SQLite 关系层** —— 存储知识点图谱、知识点讲解、题目元数据、错题记录、作答记录、题目-知识点关联
2. **Chroma 向量层** —— 存储文本块（题目、解析、知识点描述）的向量嵌入

两层通过 `doc_id` 关联。SQLite 负责"精确过滤"（按知识点、年份、难度），Chroma 负责"语义相似"（按问题含义检索）。

## SQLite Schema

### 1. 知识点树形表 `topics`

采用邻接表模型，支持树形层级。**树是数据驱动的动态结构**（非预定义写死，见下方"动态构建"说明）：

```sql
CREATE TABLE topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER REFERENCES topics(id),   -- NULL = 根节点
    name        TEXT NOT NULL,                    -- 知识点名称（LLM 提取）
    code        TEXT,                             -- 知识点编码，如 "math.conics.eccentricity"（自动生成或可空）
    subject     TEXT NOT NULL,                    -- 学科: "数学" / "物理" / ...
    level       INTEGER NOT NULL,                 -- 层级: 0=根, 1=一级, 2=二级, 3=叶子
    description TEXT,                             -- 知识点描述（跨题目聚合）
    -- 动态构建字段
    aliases     TEXT,                             -- 同义表述 JSON: ["离心率", "e=c/a"]
    source_count INTEGER DEFAULT 0,               -- 关联题目数（树生长统计）
    confidence  REAL,                             -- 节点可信度（LLM 挂载置信度）
    status      TEXT DEFAULT 'active',            -- active / merged / pending（待归位）
    merged_into INTEGER REFERENCES topics(id),    -- 合并后指向的节点
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_topics_parent ON topics(parent_id);
CREATE INDEX idx_topics_subject ON topics(subject);
CREATE INDEX idx_topics_code ON topics(code);
CREATE INDEX idx_topics_status ON topics(status);
```

**动态构建（数据驱动，MVP 单用户一棵树）**：

> 树不是 V0.2 手工 seed 的固定分类，而是随用户数据摄入不断演化。摄取时四步：
> ① **LLM 开放式提取**知识点名（不预定义候选集，允许树外新节点，如"切线放缩""端点效应"）
> ② **查树归位**：命中复用已有节点；未命中新增（先挂根，status=pending）
> ③ **语义合并**：同义/近义表述（"离心率" vs "e=c/a"）合并到同一节点（aliases），防树膨胀
> ④ **挂载父节点**：LLM 依据语义判定层级挂载（status=pending → active）

**设计决策**：动态构建而非预定义。理由：

- 树是"用户数据在知识空间的投影"，无知识天花板；树外知识点自动长出新节点
- 反映真实薄弱分布（周报"薄弱知识点 Top 3"直接从树 × errors 聚合）
- 扩科（理化生）无需重造树，数据喂进来树自己长
- **MVP 单用户**：不存在多用户隔离，树就是这一个用户的知识树

### 2. 知识点讲解表 `knowledge_notes`

存储讲义/学习资料中的**知识点讲解段**（概念、公式、典型方法）。本质是**纯文本 RAG**——比带图的题目还简单，不需要 VLM，文本切块向量化即可。

```sql
CREATE TABLE knowledge_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT UNIQUE NOT NULL,          -- 与 Chroma knowledge_point chunk 对应
    topic_id        INTEGER REFERENCES topics(id),  -- 关联知识点树节点（可空，识别不出先挂 NULL）
    source_file     TEXT NOT NULL,                  -- 来源: "专题/圆锥曲线_1.pdf" / "homework:2026-08-11"
    title           TEXT,                           -- 讲解标题（如"分离参数法"）
    content         TEXT NOT NULL,                  -- 讲解文本
    examples        TEXT,                           -- 关联例题引用 JSON: [question_id]
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_notes_topic ON knowledge_notes(topic_id);
CREATE INDEX idx_notes_source ON knowledge_notes(source_file);
```

**与 Chroma 的关系**：content 向量化 → `knowledge_point` chunk，`doc_id` 桥接（同 questions 模式）。

**检索价值**：用户问"什么是分离参数法" → 命中 knowledge_point chunk → 返回讲解内容 + 关联例题。复习建议可链接到具体讲解（"先看圆锥曲线讲义：分离参数法"）。

### 3. 题目表 `questions`

```sql
CREATE TABLE questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT UNIQUE NOT NULL,          -- 与 Chroma chunk 的 doc_id 对应
    source_type     TEXT NOT NULL,                  -- "exam" / "special_topic" / "homework" / "error_book"
    source_file     TEXT NOT NULL,                  -- 源文件名（作业可标 "homework:2026-08-11"）
    exam_region     TEXT,                            -- 考区: "南昌" / "深圳" / "全国卷I" ...
    exam_year       INTEGER,                         -- 年份
    exam_month      TEXT,                            -- 月份: "二月" / "三月" ...
    question_number TEXT,                            -- 题号: "第15题" / "选择题3"
    question_type   TEXT NOT NULL,                  -- "选择题" / "填空题" / "解答题"
    difficulty      INTEGER,                         -- 1-5 难度等级
    content_text    TEXT NOT NULL,                  -- 题目文本（VLM 处理后含图形描述）
    answer_text     TEXT,                            -- 标准答案
    analysis_text   TEXT,                            -- 解析
    has_image       BOOLEAN DEFAULT 0,              -- 是否含图
    image_paths     TEXT,                            -- 图像路径 JSON 数组
    vlm_descriptions TEXT,                           -- VLM 生成的图形描述 JSON 数组
    raw_text        TEXT,                            -- 原始提取文本（备份）
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_questions_source ON questions(source_type, source_file);
CREATE INDEX idx_questions_exam ON questions(exam_region, exam_year);
CREATE INDEX idx_questions_type ON questions(question_type);
CREATE INDEX idx_questions_difficulty ON questions(difficulty);
```

### 4. 题目-知识点关联表 `question_topics`

多对多关系——一道题可能涉及多个知识点：

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

> **标注流程**：摄取时由 LLM 读取题目文本，输出知识点编码列表，再通过 `code` 查表获取 `topic_id`。

### 5. 错题记录表 `errors`

```sql
CREATE TABLE errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,                  -- 用户标识（MVP 固定单一用户，字段预留未来多用户）
    question_id     INTEGER REFERENCES questions(id),
    source_text     TEXT,                            -- 错题原始文本（如果未关联到题目）
    error_type      TEXT,                            -- "计算错误" / "思路错误" / "知识盲区" / "审题错误"
    user_reflection TEXT,                            -- 用户口述的原始错因描述（自由文本）
    error_summary   TEXT,                            -- LLM 生成的结构化错因总结（JSON: {cause, knowledge_gap, fix_suggestion}）
    error_count     INTEGER DEFAULT 1,              -- 同一题错了几次
    first_seen      TEXT DEFAULT (datetime('now')),
    last_seen       TEXT DEFAULT (datetime('now')),
    resolved        BOOLEAN DEFAULT 0               -- 是否已掌握
);

CREATE INDEX idx_errors_user ON errors(user_id);
CREATE INDEX idx_errors_question ON errors(question_id);
CREATE INDEX idx_errors_type ON errors(error_type);
```

> **错因总结（关键设计）**：错题录入时**不存学生手写解题过程**（VLM 识别手写准确率低且存储成本高），改为**用户口述错因 + LLM 生成结构化总结**：
>
> - `user_reflection`：用户用自己的话描述"我当时怎么错的"（QQ 文字/语音输入）
> - `error_summary`：LLM 基于口述 + 题目上下文生成的结构化总结（错因归类、知识缺口、改进建议）
> - 周报/复习建议优先消费 `error_summary`（结构化、可比对），`user_reflection` 作为原始依据保留

### 6. 复习计划表 `review_plans`

```sql
CREATE TABLE review_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,                  -- 用户标识（MVP 固定单一用户）
    plan_type       TEXT NOT NULL,                  -- "knowledge_gap" / "exam_review" / "custom"
    target_topics   TEXT,                            -- 目标知识点 JSON 数组
    description     TEXT,                            -- 建议内容
    priority       INTEGER DEFAULT 3,               -- 1-5 优先级
    created_at     TEXT DEFAULT (datetime('now')),
    completed_at   TEXT
);

CREATE INDEX idx_review_user ON review_plans(user_id);
```

### 7. 试卷作答记录表 `exam_attempts`

支撑「整卷作答情况」——记录一次完整模考/作业的总体表现（总分、正确率、逐题对错、用时）。与 `errors` 分工：errors 回答"这题为什么错"（题目粒度），exam_attempts 回答"这张卷整体考得怎样"（卷子粒度）。

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

> **作答录入（关键交互）**：与错题录入一致——**用户口述 + LLM 解析**，不依赖识别手写成绩单：
>
> - 用户口述："南昌一模做了，选择错 2 个、填空错 1 个，大题导数没写出来，总分 68"（可拍照成绩单作为辅助输入）
> - LLM 解析为 `question_results`（按题号匹配 questions 表）+ `total_score` + `answer_summary`
> - 周报可聚合：`exam_attempts` 算整体正确率/失分题型，`errors` 算薄弱知识点，两者互补

### 8. 周期报告表 `periodic_reports`

支撑「周报 / 月报」功能。报告生成后落库，可回溯、可对比、可缓存（同一周期不重复生成）：

```sql
CREATE TABLE periodic_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,                  -- 用户标识（MVP 固定单一用户）
    period_type     TEXT NOT NULL,                  -- "weekly" / "monthly"
    period_start    TEXT NOT NULL,                  -- 周期起始日期
    period_end      TEXT NOT NULL,                  -- 周期结束日期
    -- 统计快照（生成时固化，防后续错题更新导致历史报告漂移）
    total_errors    INTEGER NOT NULL,               -- 周期内新增错题数
    resolved_errors INTEGER DEFAULT 0,              -- 周期内已掌握错题数
    resolve_rate    REAL,                            -- 掌握率 = resolved / total
    weak_topics     TEXT,                            -- 薄弱知识点 JSON: [{topic, error_count, accuracy}]
    trend_vs_prev   TEXT,                            -- 对比上一周期 JSON: {total_delta, top_topic_delta}
    recommendation  TEXT,                            -- LLM 生成的针对性练习建议
    raw_stats       TEXT,                            -- 完整统计原始数据（JSON，供重新生成/调试）
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, period_type, period_start, period_end)   -- 同周期幂等
);

CREATE INDEX idx_reports_user_period ON periodic_reports(user_id, period_type, period_start);
```

**生成流程**（详见 [Agent 设计](agent.md) 的 REPORT_GEN 节点）：

```mermaid
flowchart TD
    A["指令: 生成周报/月报"] --> B[确定周期窗口<br/>（本周一~今天 / 本月1号~今天）]
    B --> C[查 errors 表聚合统计<br/>（窗口内新增、已解决、按知识点分组）]
    B --> C2[查 exam_attempts 聚合<br/>（窗口内作答次数、平均分、失分题型）]
    C --> D[对比上一周期<br/>→ 趋势]
    C2 --> D
    D --> E[LLM 生成针对性练习建议<br/>（结合知识点图谱）]
    E --> F[写入 periodic_reports 表<br/>（UNIQUE 保证幂等）]
    F --> G[返回报告]
```

**数据流**：`errors` 表（错题明细）+ `exam_attempts` 表（作答明细）→ 周期聚合 → `periodic_reports`（快照）→ LLM 建议。错题持续累积、作答按次记录，报告按需生成，三者解耦。

## Chroma 向量层

### Collection 设计

单 Collection，通过 metadata 过滤区分内容类型。

```python
collection = chroma_client.get_or_create_collection(
    name="gaokao",                     # 通用名：全科共用，按 metadata.subject 过滤学科
    metadata={"description": "高考全科知识库"}
)
```

### Chunk 策略

一道题拆分为 3 种 chunk，独立入库：

| chunk_type | 内容 | 检索场景 |
| ----------- | ------ | --------- |
| `question` | 题目文本 + VLM 图形描述 | 用户搜"椭圆离心率最值" |
| `answer` | 标准答案 + 解析 | 用户看解析、对比解法 |
| `knowledge_point` | 知识点描述 + 公式 + 典型方法 | 用户问"什么是分离参数法" |

### Metadata 设计

每个 chunk 的 metadata（与 SQLite 字段对齐）：

```python
{
    "doc_id": "q_001_question",     # 与 SQLite questions.doc_id 对应
    "source_type": "exam",
    "source_file": "2026_南昌一模.pdf",
    "subject": "数学",
    "exam_region": "南昌",
    "exam_year": 2026,
    "question_type": "解答题",
    "difficulty": 4,
    "topic_code": "math.conics.eccentricity",   # 一级编码用于粗过滤
    "topic_codes": "math.conics,math.conics.eccentricity",  # 全部相关编码，逗号分隔
    "chunk_type": "question",
    "has_image": True,
}
```

## tRPC-Agent Knowledge 集成

利用 tRPC-Agent 的 `LangchainKnowledge` + `AgenticLangchainKnowledgeSearchTool`，Agent 可以根据用户问题自动构建 metadata 过滤条件：

```python
# 用户问 "帮我找2026年南昌一模的圆锥曲线题"
# LLM 自动生成 dynamic_filter:
{
    "operator": "and",
    "value": [
        {"field": "metadata.exam_region", "operator": "eq", "value": "南昌"},
        {"field": "metadata.exam_year", "operator": "eq", "value": 2026},
        {"field": "metadata.topic_code", "operator": "like", "value": "math.conics%"}
    ]
}
```

这就是 tRPC-Agent 的 `AgenticLangchainKnowledgeSearchTool` 的核心能力——LLM 根据用户语义自动构建 `KnowledgeFilterExpr`，不需要手写路由逻辑。
