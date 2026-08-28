# 入库决策 Agent（src/agent/ingestion/storage_decision.py）

> 对应代码：`src/agent/ingestion/storage_decision.py`。摄入侧子 Agent 之一，摄入链路**写库的执行者**。**只调 `src/ingestion` 写门面的原子函数，严禁 `import src.store.*`**。

## 定位

摄入链路**写库的执行者**：消费「已确认的结构化题目清单 + 用户的去向意图」，逐题分流写入。**回显题目清单、与用户的多轮对话由 Leader Agent 管理**（2026-08-28 用户明确）——本 Agent 不发起对话、不做回显、不收集决策，只执行写库。

## 输入（结构化，由 Leader 传入）

| 字段 | 内容 |
|------|------|
| `pending_questions` | 题目清单（每题：一句话概括 + 题目/答案/解析三段 + 关联图像 / 来源）——有「来源」行则拆解映射：年份→`exam_year`、题号→`question_number`、卷型/考区→`exam_regions`、性质→`source_type` |
| `ingest_decisions` | 每题去向（入库 / 错题 / 跳过）——Leader 已收集好的用户意图 |

回显示意（**Leader 侧**，非本 Agent 职责）：

```
Bot: 已识别到 3 道题目：
     1.【圆锥曲线】椭圆焦点三角形面积最值
     2.【导数应用】恒成立参数取值范围
     3.【立体几何】二面角余弦值计算
     另识别到 1 段知识点讲解（已入库）

     每道题怎么处理？
     回复格式：题号 + 操作
       a = 全部入库
       b = 全部进错题本
       c = 跳过
```

## 分流逻辑

| 决策 | 写入内容 |
|------|----------|
| **a 入库** | 调用 `ingest_question` → questions + question_topics + Chroma |
| **b 错题** | 先 `ingest_question` 入库题目（与 a 完全相同），再由错题本体系调用 `ingest_error(question_id, user_reflection)` 写入错因（见 [error.md](../../ingestion/error.md)） |
| **c 跳过** | 不调用 `ingest_question`（题目不写入任何表） |

## 原子化：先题后错（2026-08-25 决议）

> 入库决策子 Agent 通过 **ingest_tool**（FunctionTool，见 [../tools/ingest_tool.md](../tools/ingest_tool.md)）调用写门面原子函数。

`ingest_question` 与 `ingest_error` 是两个**独立原子工具**：

- `ingest_question` 不接收任何 errors 参数，只把题写进三层存储，返回 `{question_id, doc_id}`——完全不感知 `errors`
- 标记「错题」的题**先入库**，再由错题本体系调 `ingest_error(question_id, user_reflection)` 写错因
- 依赖方向：`errors.question_id` FK → questions，错题本体系依赖题目摄入体系，**杜绝 `ingest_question ↔ errors` 循环依赖**

```python
# 示例：入库决策 Agent 的 FunctionTool 调用链
result = await ingest_question(
    raw_file_path=raw_file_path,
    question_text=question_text,
    answer_text=answer_text,
    analysis_text=analysis_text,
    topic_names=topic_names,        # 来自 topic_draft
)                                   # → {question_id, doc_id}
if decision == "error_book":
    await ingest_error(
        question_id=result["question_id"],
        user_reflection=user_reflection,  # 用户口述错因
    )
```

## 决策原则

- 只消费传入的题目与意图，写库后返回结果，不发起对话 / 不回显 / 不收集决策
- 决策缺失或模糊（某题无对应意图）→ 标记 `pending` 交还 Leader 补充，不擅自猜测去向
- **错题不在题目摄入时内联写 `errors`**：先题后错，保持 `ingest_question` 原子化

## 输出（State 契约）

| 字段 | 内容 |
|------|------|
| `ingest_decisions` | 用户对每题的取舍（入库 / 错题 / 跳过）——Leader 收集后传入 |
| `ingest_results` | 写入结果（question_id / doc_id） |

数据流见 [README.md 摄入侧数据流契约](../README.md)。
