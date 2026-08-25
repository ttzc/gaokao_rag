# 错题分析工具（src/agent/tools/error_tool.py）

> 对应代码：`src/agent/tools/error_tool.py`。错题 FunctionTool，挂到 **聚合数据子 Agent**（查询侧，见 [../retrieval/aggregate.md](../retrieval/aggregate.md)）。

## 定位

把"错题统计与分析"封装成 FunctionTool，供聚合数据子 Agent 调用，产出错题分布、薄弱知识点与周报/月报聚合所需的数据。读写 SQLite 一律走 `src/retrieval`（读）与 `src/ingestion`（写）门面。

## Tool 清单

| Tool | 签名 | 用途 |
|------|------|------|
| `ErrorStatsTool` | `(user_id, window_start=None, window_end=None) → stats` | 错题统计：总数 / 已解决 / 掌握率 / 按知识点分组 |
| `ErrorDetailTool` | `(user_id, topic_name=None, limit=10) → [error]` | 错题明细（含 `error_summary` 错因），供复习建议引用具体错因 |
| `AttemptStatsTool` | `(user_id, window_start=None, window_end=None) → stats` | 整卷作答统计：卷数 / 均分 / 薄弱题型（按失分） |

## 数据来源（双源）

| 源 | 表 | 用途 |
|----|----|------|
| 错题本 | `errors`（含 LLM 结构化的 `error_summary`） | 错因聚合、掌握率、按知识点分组 |
| 整卷作答 | `exam_attempts`（用户口述录入） | 均分、薄弱题型、失分分析 |

两者互补合并 → `weak_topics` + `recommendation`，供周报/月报（REPORT_GEN）与复习建议（REVIEW）使用。

## 错因（error_summary）

不存手写解题过程（VLM 识别手写 CER 15-20% 不可靠），改**用户口述错因 + LLM 结构化 `error_summary`**（2026-08 决策）。统计与建议都基于"具体错因"而非仅"错题数量"。
