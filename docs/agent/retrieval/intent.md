# 意图识别 Agent（src/agent/retrieval/intent.py）

> 对应代码：`src/agent/retrieval/intent.py`。查询侧子 Agent 之一，**只调 `src/retrieval` 读门面，严禁 `import src.store.*`**。

## 定位

查询链路的第一棒：判断用户意图，决定后续委派哪些成员。纯 LLM 分类，不查库。

## 意图集合

| 意图 | 用户输入示例 | 后续委派 |
|------|-------------|----------|
| `question` | "帮我看看这道椭圆题怎么做" | 搜索信息 →（有图）VLM 理解 → 输出整理 |
| `review` | "我的错题主要集中在哪些知识点" | 聚合数据 →（找推荐题）搜索信息 → 输出整理 |
| `report` | "帮我生成这周的周报" | 聚合数据 → 输出整理 |
| `browse` | "列出2026年南昌一模的所有题目" | 搜索信息 → 输出整理 |
| `ingest` | "帮我存这道题/这道题我不会" | 摄入侧：文档识别 → 结构识别 → 知识整理 → 入库决策 |

## 决策原则

- `query_type="report"` 时，同时把用户请求解析为 `"weekly"` / `"monthly"`，写入 `GaokaoState.period_type`（2026-08-20 决策，供 REPORT_GEN 使用）
- 判断不确定时，默认按 `question` 走（多数场景是题目查询）
- MVP 学科固定"数学"，subject 由 Leader 静态注入，不做学科意图分类

## 输出（State 契约）

| 字段 | 内容 |
|------|------|
| `query_type` | question / review / report / browse / ingest |
| `period_type` | （仅 report）weekly / monthly |

> 备用方案（GraphAgent）中该职责由 ROUTER 节点承担，见 [graph_fallback.md](../graph_fallback.md)。
