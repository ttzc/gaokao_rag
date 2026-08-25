# 聚合数据 Agent（src/agent/retrieval/aggregate.py）

> 对应代码：`src/agent/retrieval/aggregate.py`。查询侧子 Agent 之一。**唯一例外：周报落库走 `src/ingestion` 写门面（periodic_reports），其余只读走 `src/retrieval` 读门面——严禁直接 `import src.store.*`**。

## 定位

数据聚合：错题/作答统计、薄弱点分析、周报/月报聚合。是查询侧中**唯一会写库**的成员（periodic_reports 落库），但写入一律走写门面的原子函数。

## 能力

| 能力 | 说明 |
|------|------|
| 错题分布 | errors 表按知识点分组统计（总数 / 已解决 / 掌握率） |
| 薄弱知识点 | 结合 `error_summary`（LLM 结构化错因）找 weak_topics |
| 整卷作答统计 | exam_attempts 表：均分 / 薄弱题型（按失分） |
| 周报/月报聚合 | 双源合并 → 趋势对比 → 落 periodic_reports（幂等） |

## 读写边界

| 操作 | 走向 | 门面 |
|------|------|------|
| 错题/作答/知识点**查询** | 读 | `src/retrieval`（error / exam_attempt / topic 查询） |
| 周报**落库** | 写 | `src/ingestion`（periodic_reports 原子函数） |

## 关键决策

- **统计快照落库**：报告生成时固化统计，历史报告不漂移
- **幂等**：同一用户同一周期重复唤起 → 返回缓存，不重复生成
- **趋势对比**：与上一周期对比，识别"恶化/改善/新增"的薄弱点（对"针对性练习"是关键信号）
- **练习建议带推荐题源**：结合知识点图谱 + Chroma 检索（可再委派搜索信息子 Agent），推荐具体题目（哪份试卷第几题）

详细聚合逻辑见 [graph_fallback.md REPORT_GEN 节点](../graph_fallback.md)。
