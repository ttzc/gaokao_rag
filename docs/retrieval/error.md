# error — 错题统计与明细

把 `errors` 表聚合成对学生有用的错题画像（统计、明细）。只读，不直接写 `errors`。

> **落地状态（2026-09-18）**：✅ `get_error_stats`（基础口径）/ `get_error_details` 已落地；
> ⏳ 知识点分布、时间趋势、`get_weak_topics` 属周报口径，随周报设计实现（见下「本期范围」）。

## 本期范围（2026-09-18 定）

| 内容 | 状态 | 说明 |
|------|------|------|
| `get_error_stats` 基础口径 | ✅ 已落地 | 三个计数原语 + 掌握率，**无时间窗参数** |
| `get_error_details` | ✅ 已落地 | 单题错因明细（一题一行，实际恒单条） |
| 统计的知识点分布 `by_topic` | ⏳ | 需 join `question_topics`，属周报口径 |
| 统计的时间趋势 / 时间窗参数 | ⏳ | 属周报口径 |
| `get_weak_topics` | ⏳ | **`WeakTopic.accuracy` 数据源未定**——errors 只记错题，"正确率"的分母应是"做过的题数"，唯一来源 `exam_attempts` 未落地；随周报设计定稿 |

## get_error_stats — 错题统计

```python
def get_error_stats() -> ErrorStats:
```

**内部流程**：三个 `ErrorsDB` 计数原语组合——

- `count()` → 总错题数
- `count_resolved()` → 已掌握数；掌握率 = `resolved / total`（`total = 0` 时取 `0.0`，除零安全）
- `count_pending()` → 错因待补数（判定见 [store/db/errors.md](../store/db/errors.md)）——**仍计入 total**，但不参与错因分析

**返回**：`ErrorStats`（`total` / `resolved` / `resolve_rate` / `pending_count`）。空库也是合法返回，不抛异常。

## get_error_details — 错题明细

```python
def get_error_details(question_id: int) -> list[ErrorDetail]:
```

**内部流程**：取该题的错题记录（`errors` 一题一行，实际恒单条——签名保留 `list` 是为将来留口）：口述 `user_reflection` + LLM 结构化 `error_summary`。

**返回**：`ErrorDetail` 列表（含 **`pending` 标记**——错因待补时为 `True`），供输出整理 Agent 拼「这题为什么错」。

## 错因待补记录的处理（2026-09-17 定）

「待补」的判定规则见 [store/db/errors.md](../store/db/errors.md)——**不新增状态字段**，空值即状态本身（不可能与数据不同步）。

**读到待补记录时，Leader 要提示用户补充**（用户明确要求）：

- 查询错题本 / 薄弱点，结果里含待补记录 → 如实标注「（错因待补）」并在回复末尾带一句补充邀请
  （如「其中 3 道题还没写错因，想补的话直接说『第 2 题我公式记混了』就行」）
- 聚合统计照常计数（它们仍是错题），只是**不参与错因分析**（没有错因可分析）——所以 `ErrorStats` 单独给出 `pending_count`
- 是否补、什么时候补由用户决定，**系统不催、不阻塞**

> 设计意图：错因是错题本的核心价值，但要求用户在录入时逐题口述会劝退。故采取「先记下来、后补」——
> 数据库里这行记录本身就是"错因待记录"的凭证（见 [store/db/errors.md](../store/db/errors.md)「一题一行」）。

## get_weak_topics — 薄弱知识点 ⏳

> **未落地**（2026-09-18）：随周报设计实现。卡点是 `accuracy` 的口径——errors 表只记错题，
> "正确率"的分母应是"该知识点做过的题数"，唯一来源 `exam_attempts` 尚未落地；
> 若拿"该知识点全库题数"当分母，算出的是错题覆盖率而非正确率，语义会误导。
> 候选方向：改用 `ratio`（该知识点错题数 ÷ 总错题数，与周报表格已有的「占比」列同口径），
> 真正的正确率待 `exam_attempts` 落地后作为独立字段补。**设计周报时定稿。**

```python
def get_weak_topics(top_n: int = 5) -> list[WeakTopic]:
```

**内部流程**：按知识点聚合错题数 + 正确率，取错得最多 / 正确率最低的前 `top_n` 个，作为复习建议的输入。

**返回**：`WeakTopic` 列表（topic / error_count / accuracy）。

> 这些数据是周报双源聚合的输入之一（见 [report.md](report.md)）。
