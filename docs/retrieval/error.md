# error — 错题统计与薄弱知识点

把 `errors` 表聚合成对学生有用的错题画像（统计、明细、薄弱知识点）。只读，不直接写 `errors`。

## get_error_stats — 错题统计

```python
def get_error_stats(
    user_id: str,
    window: tuple[str, str] | None = None,
) -> ErrorStats:
```

**内部流程**：`errors` 表按 `user_id`（+ 可选时间窗 `first_seen BETWEEN`）聚合：

- 总错题数、已掌握数（`resolved`）、掌握率
- 按 `error_type` 分布（计算 / 思路 / 知识盲区 / 审题）
- 按知识点分布（经 `question_topics.topic_name` 匹配）

**返回**：`ErrorStats`（总数 / 掌握率 / 类型分布 / 知识点分布）。

## get_error_details — 错题明细

```python
def get_error_details(question_id: int) -> list[ErrorDetail]:
```

**内部流程**：取某题的全部错题记录（口述 `user_reflection` + LLM 结构化 `error_summary`）。

**返回**：`ErrorDetail` 列表，供输出整理 Agent 拼"这题为什么错"。

## get_weak_topics — 薄弱知识点

```python
def get_weak_topics(user_id: str, top_n: int = 5) -> list[WeakTopic]:
```

**内部流程**：按知识点聚合错题数 + 正确率，取错得最多 / 正确率最低的前 `top_n` 个，作为复习建议的输入。

**返回**：`WeakTopic` 列表（topic / error_count / accuracy）。

> 这些数据是周报双源聚合的输入之一（见 [report.md](report.md)）。
