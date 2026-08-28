# report — 周报 / 月报聚合

周期报告的聚合与读取门面。双源聚合（`errors` + `exam_attempts`）→ 对比上周期 → 落库快照（`periodic_reports`）。

> 注意：本模块的「落库」指写入 `periodic_reports` **快照**，属于报告缓存，不是三层存储的业务写入；其余统计只读。生成逻辑由 Agent 聚合数据 Agent 编排（[agent/retrieval/aggregate.md](../agent/retrieval/aggregate.md)）。

## aggregate_errors — 错题侧聚合

```python
def aggregate_errors(user_id: str, start: str, end: str) -> ErrorAggregate:
```

**内部流程**：窗口内 `errors` 聚合（新增数 / 已解决数 / 掌握率 / 薄弱知识点），封装为 `ErrorAggregate`。

## aggregate_attempts — 作答侧聚合

```python
def aggregate_attempts(user_id: str, start: str, end: str) -> AttemptAggregate:
```

**内部流程**：窗口内 `exam_attempts` 聚合（作答次数 / 平均分 / 失分题型），封装为 `AttemptAggregate`。

## get_report — 读 / 生成报告

```python
def get_report(
    user_id: str,
    period_type: str,        # "weekly" / "monthly"
    period_start: str,
    period_end: str,
    regenerate: bool = False,
) -> PeriodicReport:
```

**内部流程**：

1. 先查 `periodic_reports` 是否已有同周期快照（UNIQUE 幂等）；命中且非 `regenerate` 直接返回缓存
2. 未命中 / 强制重算：`aggregate_errors` + `aggregate_attempts` → `compute_trend` 对比上周期 → LLM 生成 `recommendation` → 写 `periodic_reports`（先查后写，冲突 UPDATE）
3. 返回 `PeriodicReport`（统计快照 + 趋势 + 建议）

## compute_trend — 周期趋势

```python
def compute_trend(user_id: str, period_type: str, current: PeriodicReport) -> Trend:
```

**内部流程**：取上一周期同类型报告，算 `total_errors_delta` / `resolve_rate_delta` / `top_topic_delta`，封装为 `Trend`。

> 快照固化：报告数字在生成时固化，后续错题更新不影响历史报告（见 [db/periodic_reports.md](../store/db/periodic_reports.md)）。
