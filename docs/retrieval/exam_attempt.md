# exam_attempt — 整卷作答统计

把 `exam_attempts` 表聚合成整卷表现画像（卷子粒度）。只读。

## get_attempt_stats — 作答统计

```python
def get_attempt_stats(
    user_id: str,
    start: str,
    end: str,
) -> AttemptStats:
```

**内部流程**：`exam_attempts` 表按 `user_id AND attempt_date BETWEEN start AND end` 聚合：

- 作答次数、平均分、平均正确率
- 失分题型分布（按 `question_results[].correct` + 关联 `questions.question_type` 分组）
- 用时趋势

**返回**：`AttemptStats`（作答次数 / 平均分 / 失分题型 / 用时）。

> 与 `errors` 互补：errors 按题（为什么错）、本表按卷（整体考得怎样），两者合并进周报（见 [report.md](report.md)）。
