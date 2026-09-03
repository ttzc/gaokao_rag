# 摄入工具（写门面适配层）

> 对应代码：`src/agent/tools/ingest_tool.py`（合并自原 `extract_tool` / `vlm_tool` / `knowledge_tool` / `ingest_tool` 四个文件）。
> 写侧 FunctionTool 集合，封装 `src.ingestion` 门面，**严禁 `import src.store.*`**（见 [architecture.md 分层边界契约](../../architecture.md)）。

## 定位

子 Agent 的写能力全部通过 FunctionTool 注入：PDF / 图像提取、VLM 图形理解、知识点 tag 归位、题目 / 错题摄入。工具是子 Agent 与摄入门面之间的适配层——内部封装函数调用 + 参数整形，**不含 LLM 决策**（LLM 只负责判断与生成结构化数据，调用由工具执行）。

> 工具包按「写 / 读」拆为两个文件：`ingest_tool.py`（本文件，写侧）+ `retrieve_tool.py`（读侧，见 [retrieve_tool.md](retrieve_tool.md)）。

## 工具清单

| 工具 | 原名 | 能力 | 封装门面 | 挂载子 Agent |
|------|------|------|----------|--------------|
| `ExtractTool` | `extract_tool` | PDF / 图像提取（PyMuPDF + VLM，只提取不切结构） | （文件 IO + VLM） | 文档识别（摄入侧） |
| `VLMUnderstandTool` | `vlm_tool` | VLM 图形理解（描述入库，查询不重复调用） | VLM | VLM 理解（查询侧）、文档识别（摄入侧） |
| `KnowledgeTool` | `knowledge_tool` | 知识点查询 / tag 归位（`search` / `create` / `add_alias`） | `src.ingestion.topic` | 搜索信息（查询侧）、题目维护（摄入侧） |
| `IngestQuestionTool` | `ingest_tool` | 题目 / 错题摄入（`ingest_question` → `ingest_error`，先题后错） | `src.ingestion.question` | 入库决策（摄入侧） |
| `UpdateQuestionTool` | — | 修改题目信息（内容 / 答案 / 解析 / 元数据 / 知识点） | `src.ingestion.question` | **题目维护**（2026-09-03） |
| `DeleteQuestionTool` | — | 删除题目（级联 question_topics / errors / exam_attempts + Chroma） | `src.ingestion.question` | **题目维护**（2026-09-03） |

> **实现现状（2026-08-28）**：代码侧当前仅落地 `IngestQuestionTool`（`src/agent/tools/ingest_tool.py`，导出 `ingest_question_tool`）；`ExtractTool` / `VLMUnderstandTool` / `KnowledgeTool` 及读侧工具**逐个按链路需要实现中，不急于归并**——写齐后再对齐本文件与 `retrieve_tool.md` 的两文件结构。本表为规划目标，不代表已全部实现。
>
> **改 / 删为新增规划（2026-09-03）**：`UpdateQuestionTool` / `DeleteQuestionTool` 的设计见 [ingestion/question.md](../../ingestion/question.md)，门面函数 `update_question` / `delete_question` 尚未落地，工具随门面后补。

## ExtractTool — PDF / 图像提取

对应原 `extract_tool.py`（PDF 文本提取 + 照片理解两个子能力合一，旧类名 `PDFExtractTool` / `VLMImageTool` 已于 2026-09-03 统一废弃），挂**文档识别子 Agent**（摄入侧，见 [../ingestion/doc_recognition.md](../ingestion/doc_recognition.md)）。

- **PDF 路**：PyMuPDF 提取文本块 + 嵌入图像列表；复杂版面降级 MinerU2.5-Pro
- **图像路**：Qwen3.7-Flash/Plus，理解照片中的题目内容 + 图形描述
- **只提取不切结构**：结构识别 Agent 负责语义切分（讲解段 / 题目段），提取工具不做内容判断

## VLMUnderstandTool — VLM 图形理解

对应原 `vlm_tool.py`，挂 **VLM 理解（查询侧）**与**文档识别（摄入侧）**（见 [../retrieval/vlm.md](../retrieval/vlm.md)）。

- VLM 描述入库，**查询不重复调用**（命中已存描述直接复用，见 [vlm_strategy.md](../../vlm_strategy.md)）
- 查询侧用于理解检索到的题目配图；摄入侧用于理解上传照片

## KnowledgeTool — 知识点查询 / tag 归位

对应原 `knowledge_tool.py`，挂**题目维护子 Agent**（摄入侧）与**搜索信息子 Agent**（查询侧，见 [../ingestion/question_maintain.md](../ingestion/question_maintain.md)）。

**3 个能力**：

| 能力 | 签名 | 用途 | 内建约束 |
| ---- | ---- | ---- | -------- |
| `search_topic` | (keyword) → [node] | 按名字/别名模糊查节点 | 归位第一步，防重复创建 |
| `create_topic` | (name, aliases=[]) → id | 新增 tag | 内部先 search 去重；name 全局 UNIQUE |
| `add_alias` | (topic_id, alias) | 同义表述归并 | 别名查重（防别名挂两个节点）|

- 核心逻辑封装在 `src.ingestion/topic.py`（`resolve_or_create_topics` / `create_topic` / `add_topic_alias` / `delete_topic`，独立可测），本工具通过 FunctionTool 调用——不直接 `import src.store.*`
- **归位流程**：开放式提取（LLM 读题目/讲解段提取知识点名）→ 查表归位（命中复用 / 未命中新建）→ 别名归并（"离心率" vs "e=c/a"）
- **语义（名字即 tag）**：metadata 存名字快照（`topic_tags`）；MVP 不做树形结构（父子 / 路径枚举 / 树展开上卷）

## IngestQuestionTool — 题目 / 错题摄入

对应原 `ingest_tool.py`，挂**入库决策子 Agent**（摄入侧，见 [../ingestion/storage_decision.md](../ingestion/storage_decision.md)）。封装 `src.ingestion` 写门面的原子函数，**只接收结构化数据，不含 LLM 决策**。

**Tool 清单**：

| Tool | 状态 | 签名 | 用途 |
|------|------|------|------|
| `ingest_question` | ✅已实现 | (question_text, answer_text="", analysis_text="", topic_names=None, raw_file_path=None, question_type="", source_type="exam", subject="数学", exam_year=None, exam_month=None, question_number=None, exam_regions=None) → {question_id, doc_id} | 一道题入库：文件 + SQLite（questions + question_topics）+ Chroma（`doc_id = q_{id}`） |
| `ingest_error` | ⏳门面未落地 | (question_id, user_reflection) → error_id | 错题写错因：LLM 结构化 `error_summary` + 关联题目 |
| `ingest_image` | ⏳门面未落地 | (image_path, source) → file_id | 图片入库（文件 + files 表） |
| `ingest_exam_paper` | ⏳门面未落地 | (pdf_path, title="") → file_id | 试卷文件注册（文件 + files 表） |

**实现说明（ingest_question，2026-08-28）**：

- **交付物只有一个 FunctionTool 实例**：模块导出 `ingest_question_tool = FunctionTool(ingest_question)`；包装函数 `__name__` 即 LLM 可见工具名，故定义处名字保持 `ingest_question`。模块级实例安全（构造只读函数名 + docstring，schema 懒生成，import 零副作用）
- **薄封装而非直接 `FunctionTool(门面)`**：门面 14 个 keyword-only 参数含多个列表结构，LLM 易传错形状；工具只暴露 LLM 友好子集，复杂参数（image_file_ids / vlm_descriptions）走门面默认值
- **async 包装**：门面同步实现（文件 IO + SQLite + Chroma 嵌入），工具内经 `asyncio.to_thread` 下沉工作线程，防阻塞 Agent 事件循环
- **异常不吞**：任一层写入失败原样抛出，由框架转 error 告知子 Agent（本题未入库，不重试猜测）
- **注解写法硬约束**：可空参数必须 `typing.Optional[...]`，不能 `X | None`——FunctionTool schema 生成器只识别 typing 写法，PEP 604 直接抛 `ValueError`（实测）
- **必填校验**：仅 `question_text` 无默认值（必填）；缺失时 FunctionTool 返回 error 提示 LLM 补参重试，不触门面
- **exam_regions 透传**：工具暴露 `exam_regions: Optional[list[str]]`（考区/卷型层级，从小到大），由入库决策 Agent instruction 负责从结构识别下传的「来源」行拆解映射（如「2026高考全国1卷」→ `exam_year=2026` / `exam_regions=["全国1卷"]`）；工具只透传不做解析
- 错题分支（`ingest_error`）待其门面落地后再补，本轮只封装 `ingest_question`（**先题后错**，杜绝 `ingest_question ↔ errors` 循环依赖）

**调用链（入库决策子 Agent）**：

```python
result = await ingest_question(
    raw_file_path=raw_file_path, question_text=question_text,
    answer_text=answer_text, analysis_text=analysis_text,
    topic_names=topic_names,        # 来自题目维护 Agent 的 topic_draft
)                                   # → {question_id, doc_id}

if decision == "error_book":        # 先题后错：错因单独写
    await ingest_error(
        question_id=result["question_id"],
        user_reflection=user_reflection,
    )
```

## 题目维护工具（改 / 删）

> ⏳ **门面未落地，本节为设计**（2026-09-03）。门面设计与实现前置缺口见 [ingestion/question.md](../../ingestion/question.md)。

**为什么挂题目维护 Agent，而不是 Leader 直挂**（2026-09-03 定）：

1. **Leader 不持写工具**：`create_gaokao_leader()`（`src/agent/leader.py:132`）构造时**不传 `tools=`**，只有 `name` / `model` / `members` / `instruction` / `share_member_interactions`。它是纯编排者，给它挂写工具等于把「只委派」改成「既委派又执行」，破坏现有架构一致性——删题那一行调用也不能开特例
2. **改题有实打实的 LLM 编排活**，不该塞 Leader：口述 → 字段结构化（「答案改成 B」→ `answer_text="B"`）、来源行拆解（「2026 南昌一模第 15 题」→ `exam_year` / `exam_regions` / `question_number`）、补解析要 LLM 生成。全塞 `LEADER_INSTRUCTION` 必然臃肿
3. **上下文隔离不是障碍而是分工**：定位 `question_id` 需要全量对话（只有 Leader 有）→ 归 Leader；Agent 拿 Leader 打包好的 id + 改动描述干活。函数式委派本就是「Leader 打包输入」的约定
4. **职责收拢**：题目维护 Agent 原本就管「已入库题目的知识点改动」（树治理后置后只剩薄活），改 / 删是同一类写操作，并入一处比分散两处好

**Leader 与本 Agent 的分工**：

| 环节 | 归属 |
|------|------|
| 定位 `question_id`（对话上下文 / 用户给题号） | **Leader** |
| 删前回显确认（回显归 Leader，2026-08-28 决策） | **Leader** |
| 字段结构化 / 来源拆解 / 补解析 / 知识点重标 | **题目维护 Agent** |
| 调门面写库 | **题目维护 Agent**（经本文件工具） |

**两个工具而不是「一个工具 + action 参数」**：FunctionTool 的 name / description 是 LLM 选工具的唯一依据，把两个语义塞进一个 description 会变长变模糊；更关键的是**删需要确认前置、改不需要**，合成一个容易漏掉确认这一步。

| Tool | 状态 | 签名 | 用途 |
|------|------|------|------|
| `update_question` | ⏳门面未落地 | (question_id, content_text=None, answer_text=None, analysis_text=None, topic_names=None, question_number=None, question_type=None, exam_year=None, exam_month=None, exam_regions=None, image_file_ids=None) → {question_id, doc_id, updated_fields} | 改题目：**可逆**，直接执行 + 事后报告改动字段 |
| `delete_question` | ⏳门面未落地 | (question_id) → {question_id, doc_id, deleted, cascade:{...}} | 删题目：**不可逆**，Leader 先回显确认再调 |

**交互约定**：

- **改**：Leader 打包 `question_id` + 用户改动描述 → 委派本 Agent → 执行后 Leader 汇报改动字段（如「已更新 Q42：答案、知识点」）
- **删（阶段 1，现在）**：Leader 回显（列删除范围：题目 + 知识点关联 + 向量）→ 用户确认 → 单次委派执行 → `cascade` 统计回传汇报
- **删（阶段 2，errors / exam_attempts 模块落地后）**：两段式——**首次委派为预检**（Agent 查该题在错题本 / 作答记录中的引用，返回回显素材、不删）→ Leader 回显连带影响后结束本轮 → 用户确认（新一轮消息）→ **二次委派执行**。两段式跨会话轮次，每轮只委派一次，与「每成员每任务最多委派一次」铁律天然不冲突（铁律按轮次计），无需例外

```
Bot: 确认删除第 3 题【导数应用】恒成立参数取值范围（Q42）？
     该题还有 2 条错题记录、3 条作答记录，会一并删除。
     回复「确认删除」执行，其他内容取消。
```

**定位 question_id 的三条路径**（MVP 取舍）：

| 路径 | 场景 | MVP |
|------|------|-----|
| ① 对话上下文 | 上一轮刚检索 / 刚入库，Leader 直接持有 id | ✅ 做 |
| ② 用户给题号 / 文档名 | 「把 2026 南昌一模第 15 题删了」 | ✅ 做 |
| ③ 口述特征 → 检索反查 | 「删掉那道圆锥曲线的题」 | ⏸ 暂缓 |

路径 ③ 缓做的理由：语义检索可能命中多道，还要再确认一轮「是第 1 道还是第 3 道」，交互变长；先等真实场景遇到再补。

## 挂载矩阵（写侧）

| 子 Agent | 挂载工具 |
|----------|----------|
| 文档识别 | `ExtractTool` + `VLMUnderstandTool` |
| 题目维护 | `KnowledgeTool` + `UpdateQuestionTool` + `DeleteQuestionTool`（`manage` 意图由 Leader 委派本 Agent） |
| 入库决策 | `IngestQuestionTool` |
| VLM 理解 | `VLMUnderstandTool`（理解检索到的图，见 retrieve_tool 侧） |

## 与门面的边界

- 写：本文件工具封装 `src.ingestion` 原子函数；子 Agent 不直接碰存储
- 严禁 `import src.store.*`；违规 import 应由 CI lint 拒绝（机制堵死「入口直连存储」）
- 读取能力（如 `search_topic`）若查询侧也需，复用本文件的 `KnowledgeTool`，不另建读工具
