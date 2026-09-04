# ingest_question — 存储一道题

将一道题及其关联数据完整写入三层存储 + 知识点归位，是 ingestion 层最核心的工具。

```python
def ingest_question(
    raw_file_path: str | None = None,  # 源文件路径（files 表）；单题拍照等无源文件时传 None
    question_text: str,           # 题干
    answer_text: str = "",        # 答案（可空）
    analysis_text: str = "",      # 解析（可空）
    subject: str = "数学",        # 学科
    source_type: str = "exam",    # exam / homework / special_topic / reference / error_book（error_book = 错题本来源，预留）
    question_type: str = "",      # 选择题/填空题/解答题
    image_file_ids: list[int] = None,  # 关联图像 ID 列表
    topic_names: list[str] = None,     # 知识点名字列表（Agent 提取）
    vlm_descriptions: list[str] = None, # VLM 图形描述列表
) -> dict:
```

**内部自动完成（四层封装，纯题目摄入，不触碰 errors）**：

1. **文件层**：`save_processed` → 落盘处理后文本
2. **DB 层**：`insert_question` → `insert_question_topics`
3. **向量层**：`upsert_question_doc` → Chroma 向量化
4. **知识点层**：`search_topic` / `create_topic` → 知识点归位（复用 `store/db/topics.py`）

> **原子化约定**：`ingest_question` 只负责把「一道题」写进三层存储，**不接收任何 errors 参数**。
> 标记为「错题」的题同样先经本函数入库，错因记录由错题本体系的 `ingest_error(question_id)`
> 在题目入库后单独写入（见 error.md）。这样 `ingest_question` 与 `errors` 无循环依赖。

**返回**：`{"question_id": int, "doc_id": str}`

## 与 Agent 的协作

摄入侧 Agent 通过 FunctionTool 调用：

```python
class IngestQuestionTool(FunctionTool):
    name = "ingest_question"
    description = "将一道题写入三层存储（SQLite + Chroma）"
    
    async def execute(self, raw_file_path, question_text, answer_text="",
                     analysis_text="", topic_names=None, ...):
        return ingestion.question.ingest_question(
            raw_file_path=raw_file_path,
            question_text=question_text,
            ...
        )
        # 标记「错题」的题：拿到 question_id 后，错题本体系再调 ingest_error(question_id) 写错因
```

Agent 只需要提供结构化数据，调用单个函数即可完成入库，不需要知道 `insert_question` → `insert_question_topics` → `upsert_question_doc` 三个步骤。

---

## update_question — 修改题目信息

> ⏳ **设计已定、代码未落地**（2026-09-03）。当前 `src/ingestion/` 只有 `ingest_question` 一个函数。

```python
def update_question(
    *,
    question_id: int,
    content_text: str | None = None,
    answer_text: str | None = None,
    analysis_text: str | None = None,
    question_number: str | None = None,
    question_type: str | None = None,
    exam_regions: list[str] | None = None,
    exam_year: int | None = None,
    exam_month: int | None = None,
    image_file_ids: list[int] | None = None,
    topic_names: list[str] | None = None,   # None = 不动，[] = 清空关联
) -> dict:
```

**参数语义**（与 store 层 `QuestionsDB.update()` 对齐）：`None` = 不修改该字段；`""` / `[]` = 清空该字段。可变字段覆盖 store 层支持的全部 9 个。

**不可变字段**（`id` / `doc_id` / `source_type` / `subject` / `file_id` / `created_at`）不提供修改入口——改学科或来源等于换一道题，应走「删除 + 重新入库」，而不是原地改。

**四层顺序**（与 `ingest_question` 对称，任一环节失败直接抛出，不吞异常）：

1. **校验**：`get_by_id` 取现存记录，不存在 → `ValueError`（先失败，避免 DB 写完了才发现改了个空气）
2. **DB 层**：`QuestionsDB.update()` 更新可变字段
3. **知识点层**：`topic_names` 非 `None` 时**全量替换** `question_topics`——先清空该题全部旧关联，再按 `ingest_question` 同款归位逻辑（search 命中复用 / 未命中 create）重建
4. **向量层**：重建 embedding_text → `upsert` 同一个 `doc_id`（`q_{id}`）；metadata 全量重建

**关键细节（踩坑点）**：

- **VLM 描述不在 questions 表里**，改题重嵌必须回读：`image_file_ids` → `files.sha256` → `data/files/processed/vlm_desc/{sha256}.json`（见 [store/db/questions.md](../store/db/questions.md)「关键设计点」）。**门面内部自动回读，不向调用方暴露 `vlm_descriptions` 参数**——否则调用方漏传就静默丢掉图形描述、向量质量退化。回读失败仅 warning 跳过、不阻断（与 `ingest_question` 中 `save_processed` 的降级策略对称）
- **改内容必须重嵌，只改元数据则不必**：`content_text` / `answer_text` / `analysis_text` 任一变更 → embedding_text 重建；只改题号 / 年份 / 考区 → 文本不用重嵌，**但 Chroma metadata 要同步**（`exam_year` / `question_type` / `topic_tags` 都是过滤维度）
  - **实现偏差注记（2026-09-04 落地确认）**：`VectorStore.upsert()` 是先删后加 + `add_documents`，每次强制重算 embedding，框架未暴露 metadata-only 通道（chromadb 底层 `collection.update()` 存在，但伸手 `vectorstore._collection` 属 hack 框架内部）。故当前实现**对一切变更统一走重嵌 upsert**，接受元数据改动多付一次 embedding 调用，换取实现一致、不破坏分层——设计意图保留，待框架暴露 metadata-only 通道或引入自维护 metadata 层后再优化
- **`has_image` 快照跟着 `image_file_ids` 变**：Chroma metadata 存布尔快照用于标量过滤，SQLite 侧不存该字段（以 `image_file_ids` 非空为准），两边别维护反了
- **`title` 规则复用 `ingest_question`**：`files.title` 优先，无 `file_id` 时取 `content_text[:40]`——两处逻辑必须一致，否则改完题标题会跳变
- **文件层不动**：update 不重写 `processed/` 文本（中间产物可重建，重写无收益），只改 SQLite + Chroma

**返回**：`{"question_id": int, "doc_id": str, "updated_fields": [...]}`

---

## delete_question — 删除题目

> ⏳ **设计已定、代码未落地**（2026-09-03）。

```python
def delete_question(*, question_id: int) -> dict:
    # → {"question_id": 42, "doc_id": "q_42", "deleted": True,
    #    "cascade": {"question_topics": 2, "errors": 1, "exam_attempts": 3, "vector": True}}
```

**级联范围（分阶段）**：`question_topics` → `errors` → `exam_attempts` → `questions` 行，外加 Chroma document。孤儿关联没有业务意义（题没了，错题 / 作答留着也查不动），一并清掉。但 errors / exam_attempts 的 DB 模块未落地，级联分两阶段实现：

- **阶段 1（门面落地时）**：级联三处——`question_topics` + Chroma document + `questions` 主行。当前库里 errors / exam_attempts 为 0 行（模块都没有，写不进数据），三处即全量。返回的 `cascade` **恒含四键**（question_topics / errors / exam_attempts / vector）——契约形状从第一天定死（见上返回示例），阶段 1 中 errors / exam_attempts 恒为 0，阶段 2 只让计数变非零、不新增键
- **阶段 2（errors / exam_attempts DB 模块落地时，随错题本 / 作答功能）**：级联扩展到五处 + 删除预检（见下「删除预检」）

**执行顺序（重要）**：

1. `get_by_id` 校验存在 + 取 `doc_id`
2. **先删 Chroma document**
3. 再删 `question_topics` / `errors` / `exam_attempts`
4. 最后删 `questions` 行

> **为什么先删 Chroma**：跨 SQLite / Chroma 没有分布式事务，中断必然留下不一致。两种残留二选一——
>
> | 顺序 | 中断后的残留 | 性质 |
> |------|-------------|------|
> | 先 DB 后 Chroma | 主行没了、向量还在 | **孤儿向量**：检索能命中，回查 SQLite 拿不到内容（脏结果，用户直接可见） |
> | 先 Chroma 后 DB | 向量没了、数据还在 | 只是检索不到，**数据完整、重跑向量化即可恢复** |
>
> 后者是可重建的残留，前者是不可恢复的脏数据，所以选后者。

**边界**：

- **raw 永不删**：删题不动 `files` 表、不动 `data/files/raw/`（源数据不可再生原则，见 [store/files/raw.md](../store/files/raw.md)）。即使某题是该文件唯一引用，`files` 登记行也保留——注册表是事实记录
- **processed 不主动清理**：中间产物可重建，随后续清理策略统一走
- **幂等**：`question_id` 不存在 → 返回 `{"deleted": False}`，**不抛异常**（删一个不存在的题不是错误，与 store 层 `delete()` 返回 `False` 的语义一致）
- **不可逆**：MVP 不做软删除 / 回收站（单用户低频操作）。因此 **Agent 侧必须先回显确认再调用**，见 [agent/tools/ingest_tool.md](../agent/tools/ingest_tool.md)「题目维护工具」

---

## 实现前置：store 层还缺的原语

| 缺口 | 现状 | 影响 |
|------|------|------|
| `errors` / `exam_attempts` 的 DB 模块 | `src/store/db/` 当前只有 `files` / `questions` / `question_topics` / `topics` 四个模块 | 级联删无原语可调，门面不能自己写 SQL |
| 全库无 `ON DELETE CASCADE` | 共享连接开了 `PRAGMA foreign_keys=ON`，但 DDL 未定义级联动作 | 级联一律由门面手工完成，指望不上数据库 |

**落地节奏（2026-09-03 用户拍板，不做「删除时拒绝」）**：

- **阶段 1（现在写门面）**：门面只级联三处（`question_topics` + Chroma + 主行），不为删题倒推建 errors / exam_attempts 模块
- **阶段 2（错题本功能落地时）**：同步修改删除代码——① 门面级联扩展到 errors；② 新增**删除预检**：查该题是否在错题本中，命中则在 **Leader 回显阶段提示用户**（「该题还有 N 条错题记录」），用户确认后**一起删除**；③ 作答（exam_attempts）同理，随其模块落地
- 设计哲学：检查结果**进回显**而非**当闸门**——删除本来就要回显确认，连带影响是确认信息的一部分，不需要单独的「拒绝」分支

---

## Agent 侧入口（改 / 删）

改 / 删**不是摄入流水线的一环**（B1–B4 是「非结构化输入 → 结构化数据」的单向流水线，入库决策 Agent 消费 `pending_questions + ingest_decisions`，改 / 删没有 pending 形态），但**也不由 Leader 直接调工具**——`create_gaokao_leader()`（`src/agent/leader.py:132`）构造时不传 `tools=`，Leader 是纯编排者，挂写工具等于把它改成「既委派又执行」。

**执行者：题目维护 Agent**（2026-09-03 扩职责，见 [agent/ingestion/question_maintain.md](../agent/ingestion/question_maintain.md)）：

| 环节 | 归属 |
|------|------|
| 定位 `question_id` | **Leader**（子 Agent 上下文隔离，看不到上轮检索结果） |
| 删前回显确认 | **Leader**（回显归 Leader，2026-08-28 决策） |
| 字段结构化 / 来源拆解 / 补解析 / 知识点重标 | **题目维护 Agent**（LLM 编排活） |
| 调本文件的门面函数写库 | **题目维护 Agent**（经 FunctionTool） |

**两个动作不对称**：

- **改**：可逆 → 委派执行，Leader 汇报改动字段
- **删**：不可逆 → Leader 先回显确认（含连带影响：该题还有 N 条错题记录 / M 条作答记录会一并删除），确认后才委派执行

**删除预检的两段式委派**（阶段 2 起，errors / exam_attempts 模块落地后生效）：

1. Leader 定位 `question_id` → **首次委派**题目维护 Agent（action=delete，未带确认）→ Agent 查该题在 errors / exam_attempts 中的引用 → 返回回显素材，**不删**
2. Leader 回显确认（含连带影响：「该题还有 2 条错题记录，会一并删除」）→ 结束本轮，等用户
3. 用户确认 = **新一轮消息** → Leader **二次委派**（带 `user_confirmed=true`）→ Agent 执行删除 → `cascade` 统计回传汇报

> **与「每成员每任务最多委派一次」铁律的关系**：两段式跨会话轮次，预检与执行分属两轮，**每轮只委派一次**——铁律按轮次计（防的是同一轮内重复委派同一能力刷结果），天然不冲突，无需例外。

阶段 1（现在）没有引用可查，回显只列删除范围（题目 + 知识点关联 + 向量），同样走确认。

意图表新增 `manage`（数据维护），MVP 只识别「改题 / 删题」两个动作。详见 [agent/tools/ingest_tool.md](../agent/tools/ingest_tool.md)「题目维护工具」。
