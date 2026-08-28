# 题目摄入工具（src/agent/tools/ingest_tool.py）

> 对应代码：`src/agent/tools/ingest_tool.py`。写库 FunctionTool，挂到**入库决策子 Agent**（见 [../ingestion/storage_decision.md](../ingestion/storage_decision.md)）。封装 `src/ingestion` 写门面的原子函数，**只接收结构化数据，不含 LLM 决策**。

## 定位

把"写入三层存储"封装成 FunctionTool：`ingest_question` 将一道题写进文件层 + SQLite（questions / question_topics）+ Chroma，返回业务 ID；标记「错题」的题再走 `ingest_error` 写错因。**先题后错**——两个独立原子工具，杜绝 `ingest_question ↔ errors` 循环依赖（2026-08-25 决议，见 [ingestion.md 原子化](../../ingestion/README.md)）。

## Tool 清单

| Tool | 签名 | 用途 | 内建约束 |
|------|------|------|---------|
| `ingest_question` ✅已实现 | (question_text, answer_text="", analysis_text="", topic_names=None, raw_file_path=None, question_type="", source_type="exam", subject="数学", exam_year=None, exam_month=None, question_number=None) → {question_id, doc_id} | 一道题入库：文件 + SQLite（questions + question_topics）+ Chroma（doc_id = `q_{id}`） | **不接收 errors 参数**；门面的复杂列表参数（exam_regions / image_file_ids / vlm_descriptions）不暴露给 LLM，走默认值 |
| `ingest_error` ⏳门面未落地 | (question_id, user_reflection) → error_id | 错题写错因：LLM 结构化 `error_summary` + 关联题目 | errors.question_id FK → questions；**先题后错** |
| `ingest_image` ⏳门面未落地 | (image_path, source) → file_id | 图片入库（文件 + files 表） | sha256 UNIQUE 去重 |
| `ingest_exam_paper` ⏳门面未落地 | (pdf_path, title="") → file_id | 试卷文件注册（文件 + files 表） | sha256 UNIQUE 去重 |

## 实现说明（ingest_question，2026-08-28）

- **交付物只有一个 FunctionTool 实例**：模块导出 `ingest_question_tool = FunctionTool(ingest_question)`，`__all__` 仅此一项；工具如何组合成 `tools=[...]` 归 agent 层（入库决策子 Agent 构造时决定），包装函数不导出（其 `__name__` 即 LLM 可见工具名，故定义处名字保持 `ingest_question`）。模块级实例安全：FunctionTool 构造只读函数名 + docstring，schema 懒生成，import 零副作用。
- **薄封装而非直接 `FunctionTool(门面)`**：门面 14 个 keyword-only 参数含多个列表结构，LLM 易传错形状；工具只暴露上表的 LLM 友好子集，其余参数走门面默认值。
- **async 包装**：门面是同步实现（文件 IO + SQLite + Chroma 嵌入调用），工具内经 `asyncio.to_thread` 下沉工作线程，防阻塞 Agent 事件循环。
- **异常不吞**：任一层写入失败原样抛出，由框架转成 error 告知子 Agent（本题未入库，不重试猜测）。
- **注解写法硬约束**：可空参数必须写 `typing.Optional[...]`，不能写 `X | None`——FunctionTool 的 schema 生成器只识别 typing 写法，PEP 604 UnionType 直接抛 `ValueError`（实测）。
- **必填校验**：仅 `question_text` 无默认值（必填）；缺失时 FunctionTool 返回 error 提示 LLM 补参重试，不触门面。
- 错题分支（`ingest_error`）待其门面落地后再补，本轮只封装 `ingest_question`。

## 调用链（入库决策子 Agent）

```python
# 学生确认「入库」或「错题」后：
result = await ingest_question(
    raw_file_path=raw_file_path,
    question_text=question_text,
    answer_text=answer_text,
    analysis_text=analysis_text,
    topic_names=topic_names,        # 来自知识整理 topic_draft
)                                   # → {question_id, doc_id}

if decision == "error_book":        # 先题后错：错因单独写
    await ingest_error(
        question_id=result["question_id"],
        user_reflection=user_reflection,   # 用户口述错因
    )
```

## 相关

- 原子函数定义：`src/ingestion/`（写门面，见 [ingestion.md 实现工具集](../../ingestion/README.md)）
- 子 Agent 交互：回显 → 收集决策 → 分流（见 [storage_decision.md](../ingestion/storage_decision.md)）
- 挂载矩阵与边界：见 [tools/README.md](README.md)
