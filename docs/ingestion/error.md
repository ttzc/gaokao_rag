# ingest_error — 存储一条错题

将错题记录写入 DB，与已有题目关联。

```python
def ingest_error(
    question_id: int,             # 关联题目 ID
    user_reflection: str,         # 用户口述错因
    error_summary: str = "",      # LLM 生成的结构化总结（可选）
) -> dict:
```

**内部自动完成**：

1. **DB 层**：`insert_error` → 写入 errors 表（关联 `question_id`）

**返回**：`{"error_id": int}`

## 错因来源

| 来源 | 说明 |
|------|------|
| 用户口述错因 | 学生用自然语言描述为什么做错，LLM 结构化为 `error_summary` |
| 整卷作答 | `exam_attempts` 中逐题对错后，自动生成 error 记录 |

**设计原则**：不存手写解题过程（VLM 识别手写 CER 15-20% 不可靠），只存用户口述 + LLM 结构化总结。

**依赖方向**：`ingest_error` 接收「已入库的 `question_id`」，因此必须先经 `ingest_question` 录入题目——**错题本体系依赖题目摄入体系**，而非相反。题目摄入与错题记录是两个原子工具，由入库决策 Agent 按顺序串联（先题后错），互不耦合。
