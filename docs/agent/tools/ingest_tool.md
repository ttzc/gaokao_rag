# 题目摄入工具（src/agent/tools/ingest_tool.py）

> 对应代码：`src/agent/tools/ingest_tool.py`。写库 FunctionTool，挂到**入库决策子 Agent**（见 [../ingestion/storage_decision.md](../ingestion/storage_decision.md)）。封装 `src/ingestion` 写门面的原子函数，**只接收结构化数据，不含 LLM 决策**。

## 定位

把"写入三层存储"封装成 FunctionTool：`ingest_question` 将一道题写进文件层 + SQLite（questions / question_topics）+ Chroma，返回业务 ID；标记「错题」的题再走 `ingest_error` 写错因。**先题后错**——两个独立原子工具，杜绝 `ingest_question ↔ errors` 循环依赖（2026-08-25 决议，见 [ingestion.md 原子化](../../ingestion/README.md)）。

## Tool 清单

| Tool | 签名 | 用途 | 内建约束 |
|------|------|------|---------|
| `ingest_question` | (raw_file_path, question_text, answer_text="", analysis_text="", topic_names=None) → {question_id, doc_id} | 一道题入库：文件 + SQLite（questions + question_topics）+ Chroma（doc_id = `q_{id}`） | **不接收 errors 参数**；同 file_id + 题号幂等 |
| `ingest_error` | (question_id, user_reflection) → error_id | 错题写错因：LLM 结构化 `error_summary` + 关联题目 | errors.question_id FK → questions；**先题后错** |
| `ingest_image` | (image_path, source) → file_id | 图片入库（文件 + files 表） | sha256 UNIQUE 去重 |
| `ingest_exam_paper` | (pdf_path, title="") → file_id | 试卷文件注册（文件 + files 表） | sha256 UNIQUE 去重 |

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
