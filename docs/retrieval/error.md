# error — 错题统计与薄弱知识点

把 `errors` 表聚合成对学生有用的错题画像（统计、明细、薄弱知识点）。只读，不直接写 `errors`。

## get_error_stats — 错题统计

```python
def get_error_stats(
    window: tuple[str, str] | None = None,
) -> ErrorStats:
```

**内部流程**：`errors` 表按可选时间窗（`first_seen BETWEEN`）聚合：

- 总错题数、已掌握数（`resolved`）、掌握率、**错因待补数**（`user_reflection IS NULL AND error_summary IS NULL` 的行数）
- 按知识点分布（经 `question_topics.topic_name` 匹配）
- 时间分布（窗口内新增趋势，按 `first_seen` 分桶）

**返回**：`ErrorStats`（总数 / 掌握率 / 知识点分布 / 时间趋势 / **`pending_count`：错因待补数**）。

## get_error_details — 错题明细

```python
def get_error_details(question_id: int) -> list[ErrorDetail]:
```

**内部流程**：取该题的错题记录（`errors` 一题一行，实际恒单条——签名保留 `list` 是为将来留口）：口述 `user_reflection` + LLM 结构化 `error_summary`。

**返回**：`ErrorDetail` 列表（含 **`pending` 标记**——错因待补时为 `True`），供输出整理 Agent 拼「这题为什么错」。

## 错因待补记录的处理（2026-09-17 定）

「待补」判定：`user_reflection IS NULL AND error_summary IS NULL`——**不新增状态字段**，空值即状态本身（不可能与数据不同步）。

**读到待补记录时，Leader 要提示用户补充**（用户明确要求）：

- 查询错题本 / 薄弱点，结果里含待补记录 → 如实标注「（错因待补）」并在回复末尾带一句补充邀请
  （如「其中 3 道题还没写错因，想补的话直接说『第 2 题我公式记混了』就行」）
- 聚合统计照常计数（它们仍是错题），只是**不参与错因分析**（没有错因可分析）——所以 `ErrorStats` 单独给出 `pending_count`
- 是否补、什么时候补由用户决定，**系统不催、不阻塞**

> 设计意图：错因是错题本的核心价值，但要求用户在录入时逐题口述会劝退。故采取「先记下来、后补」——
> 数据库里这行记录本身就是"错因待记录"的凭证（见 [store/db/errors.md](../store/db/errors.md)「一题一行」）。

## get_weak_topics — 薄弱知识点

```python
def get_weak_topics(top_n: int = 5) -> list[WeakTopic]:
```

**内部流程**：按知识点聚合错题数 + 正确率，取错得最多 / 正确率最低的前 `top_n` 个，作为复习建议的输入。

**返回**：`WeakTopic` 列表（topic / error_count / accuracy）。

> 这些数据是周报双源聚合的输入之一（见 [report.md](report.md)）。
