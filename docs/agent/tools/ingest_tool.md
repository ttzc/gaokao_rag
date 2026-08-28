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
| `KnowledgeTool` | `knowledge_tool` | 知识点查询 / tag 归位（`search` / `create` / `add_alias`） | `src.ingestion.topic` | 搜索信息（查询侧）、知识整理（摄入侧） |
| `IngestQuestionTool` | `ingest_tool` | 题目 / 错题摄入（`ingest_question` → `ingest_error`，先题后错） | `src.ingestion.question` | 入库决策（摄入侧） |

## ExtractTool — PDF / 图像提取

对应原 `extract_tool.py`，挂**文档识别子 Agent**（摄入侧，见 [../ingestion/doc_recognition.md](../ingestion/doc_recognition.md)）。

- `PDFExtractTool`：PyMuPDF 提取文本块 + 嵌入图像列表；复杂版面降级 MinerU2.5-Pro
- `VLMImageTool`：Qwen3.7-Flash/Plus，理解照片中的题目内容 + 图形描述
- **只提取不切结构**：结构识别 Agent 负责语义切分（讲解段 / 题目段），提取工具不做内容判断

## VLMUnderstandTool — VLM 图形理解

对应原 `vlm_tool.py`，挂 **VLM 理解（查询侧）**与**文档识别（摄入侧）**（见 [../retrieval/vlm.md](../retrieval/vlm.md)）。

- VLM 描述入库，**查询不重复调用**（命中已存描述直接复用，见 [vlm_strategy.md](../../vlm_strategy.md)）
- 查询侧用于理解检索到的题目配图；摄入侧用于理解上传照片

## KnowledgeTool — 知识点查询 / tag 归位

对应原 `knowledge_tool.py`，挂**知识整理子 Agent**（摄入侧）与**搜索信息子 Agent**（查询侧，见 [../ingestion/knowledge_organize.md](../ingestion/knowledge_organize.md)）。

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
    topic_names=topic_names,        # 来自知识整理 topic_draft
)                                   # → {question_id, doc_id}

if decision == "error_book":        # 先题后错：错因单独写
    await ingest_error(
        question_id=result["question_id"],
        user_reflection=user_reflection,
    )
```

## 挂载矩阵（写侧）

| 子 Agent | 挂载工具 |
|----------|----------|
| 文档识别 | `ExtractTool` + `VLMUnderstandTool` |
| 知识整理 | `KnowledgeTool` |
| 入库决策 | `IngestQuestionTool` |
| VLM 理解 | `VLMUnderstandTool`（理解检索到的图，见 retrieve_tool 侧） |

## 与门面的边界

- 写：本文件工具封装 `src.ingestion` 原子函数；子 Agent 不直接碰存储
- 严禁 `import src.store.*`；违规 import 应由 CI lint 拒绝（机制堵死「入口直连存储」）
- 读取能力（如 `search_topic`）若查询侧也需，复用本文件的 `KnowledgeTool`，不另建读工具
