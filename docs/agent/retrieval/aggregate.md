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
- **双源聚合**：错题（errors）+ 整卷作答（exam_attempts）互补，弱项更全面

## 周报 / 月报生成（REPORT_GEN 逻辑）

**用户需求**：新高三的朋友最想体验的功能——"通过指令唤起周报/月报，给出建议针对性练习的知识点"。

**执行流程**（`query_type="report"` 时由意图识别子 Agent 解析 `period_type`，本 Agent 聚合 + 落库）：

```python
async def report_generate(state: GaokaoState) -> dict:
    """
    按周期（周/月）聚合错题，生成复习报告。
    指令示例："生成周报" / "这个月的月报" / "上周的学习报告"
    """
    user_id = state.get("user_id", "default")
    period_type = state["period_type"]      # "weekly" | "monthly"，由意图识别解析
    period_start, period_end = resolve_period_window(period_type)
    
    # ① 幂等检查：同周期已生成过，直接返回缓存
    cached = get_report(user_id, period_type, period_start, period_end)
    if cached:
        return {"report": cached}
    
    # ② 聚合窗口内错题统计（errors 表）
    stats = aggregate_errors(user_id, period_start, period_end)
    # {total_errors, resolved_errors, resolve_rate, by_topic: [{topic, error_count}]}
    
    # ②b 聚合窗口内整卷作答（exam_attempts 表）
    attempt_stats = aggregate_attempts(user_id, period_start, period_end)
    # {attempt_count, avg_score, weak_question_types: [{qtype, lost_score}]}
    
    # ③ 对比上一周期 → 趋势
    prev_stats = aggregate_errors(user_id, prev_period(period_start, period_end))
    trend = compute_trend(stats, prev_stats)
    
    # ④ LLM 生成针对性练习建议（结合知识点图谱 + 作答失分分析）
    recommendation = await llm_generate_report_recommendation(stats, attempt_stats, trend)
    
    # ⑤ 写入 periodic_reports 表（UNIQUE 幂等）
    report = save_report(user_id, period_type, period_start, period_end,
                         stats, trend, recommendation)
    
    return {"report": report}
```

**报告结构**（Markdown 渲染）：

```markdown
## 📊 数学学习周报（8.4 - 8.10）

### 本周概况
- 新增错题：12 道 | 已掌握：4 道 | 掌握率：33%
- 较上周：错题 +3 道（↑33%），掌握率持平

### 薄弱知识点 Top 3
| 知识点 | 错题数 | 占比 | 趋势 |
|--------|-------|------|------|
| 导数应用（恒成立） | 4 | 33% | ↑ 恶化 |
| 圆锥曲线（离心率） | 3 | 25% | 持平 |
| 立体几何（二面角） | 2 | 17% | 新增 |

### 针对性练习建议
1. **导数恒成立问题**（重点）：本周错 4 道，正确率 0%。
   → 建议先复习「分离参数法」专题（data/files/raw/专题/导数_1.pdf）
   → 推荐练习：2026南昌一模 第15题、2026深圳调研 第20题
2. **圆锥曲线离心率**：错 3 道。
   → 建议复习「焦点三角形」模型，推荐同类题 3 道
3. **立体几何二面角**：本周新增薄弱点。
   → 建议从「建系求法向量」基础开始

### 下周期待
- 聚焦 1-2 个薄弱点，不要贪多
- 本周未掌握的 8 道题建议重新做一遍
```

> 设计要点：统计快照落库（历史报告不漂移）、幂等（`--force` 强制刷新）、趋势对比、练习建议带推荐题源——见上方「关键决策」。
