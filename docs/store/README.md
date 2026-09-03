# 存储层设计（三层存储）

> 对应代码 `src/store/`（原语层，依赖图最低层）。本目录是存储层文档统一入口；系统架构见 [../architecture.md](../architecture.md)。

## 一句话模型

> **Gaokao RAG 的数据 = 题目 + 知识点，两者分开处理**：
>
> - **知识点**（资料/专题/作业中的讲解段）→ 只需挂到知识树（`knowledge_notes.topic_id` → `topics`），纯文本 RAG
> - **题目**（试卷/作业中的题）→ 记录**题目内容（含图片描述）+ 答案解析**（questions 表）；**知识点关联存 `question_topics` 表**（经 `question_id`）；**错题错因单独存 `errors` 表**（经 `question_id` 关联）
> - **考试试卷**额外记录整卷作答情况（`exam_attempts`）；作业试卷不需要
> - 所有输入走**统一摄入范式**：结构识别 → 讲解/题目分流 → 回显确认 → 用户决定去向

**数据组织**：SQLite 关系层（图谱/题目/错因/作答）与 Chroma 向量层（语义索引）通过 `doc_id` 关联——SQLite 负责"精确过滤"（按知识点、年份、考区），Chroma 负责"语义相似"（按问题含义检索）。Document 策略与 doc_id 生成规则见 [vector/vector_store.md](vector/vector_store.md)。

## 三层总览

继承 AlgoNotes RAG 的三层存储设计，schema 针对高考场景重新设计：

| 层 | 职责 | 代码 | 详细文档 |
| ------ | ------ | ------ | ------ |
| **Layer 1 文件存储** | 原始文件（PDF / 图片，sha256 哈希命名，raw 只读不可变）+ 处理中间产物（可重建） | `src/store/file_store.py` | [files/raw.md](files/raw.md) · [files/processed.md](files/processed.md) |
| **Layer 2 SQLite** | 结构化查询 + 知识点标签管理（9 张表：files / topics / knowledge_notes / questions / question_topics / errors / review_plans / exam_attempts / periodic_reports） | `src/store/db/`（逐表模块 + schema.py） | [db/](db/)（逐表 DDL + 关键设计点，单一来源） |
| **Layer 3 Chroma** | 语义检索；document 携带**检索快照 metadata**（学科/考区/年份/题型/知识点 tag/含图标记），内容以 SQLite 为权威源 | `src/store/vector/vector_store.py` | [vector/vector_store.md](vector/vector_store.md)（**Metadata 格式与过滤语义**单一来源） |

## 各层要点

### Layer 1: 文件存储

原始文件不可变（raw），处理后中间产物可重建（processed）。详见 [files/raw.md](files/raw.md)。

### Layer 2: SQLite 索引

负责结构化查询和知识点标签管理。每张表的功能定位 / Schema / 关键设计点见 [db/](db/)（逐表文档，DDL 与设计要点单一来源）。

全部表类继承 `db/__init__.py` 的 **`SQLiteTableDB` 基类**：共享连接、幂等 schema 初始化、`close()` 占位三件套由基类统一提供，各表模块只声明 `table_name` / `ddl` 两个类属性 + 业务 CRUD；schema 追踪记录集中在基类，测试隔离经 `reset_schema_tracking()` 一处重置。新表接入 = 一个模块文件 + 一次 conftest 单例重置。

### Layer 3: Chroma 向量库

负责语义检索。每个 document 携带检索快照 metadata——只存过滤/展示需要的字段，**字段规范与过滤语义见 [vector/vector_store.md「Metadata 格式与过滤语义」](vector/vector_store.md)**（单一来源，此处不重复）。

## 分层边界

`src/store/` 是原语层：只提供单表 / 单文件 / 单向量的原子 CRUD，只被 `src/ingestion`（写门面）与 `src/retrieval`（读门面）依赖，不向上依赖（铁律见 [../architecture.md 分层边界契约](../architecture.md)）。
