# Team Leader 编排（src/agent/leader.py）

> 对应代码：`src/agent/leader.py`。本文档描述 Leader 的委派策略、实现要点与实测验证结论。

## 定位

Leader 是 TeamAgent 的编排核心：接收用户请求，**自由委派**给查询侧 5 个 / 摄入侧 4 个子 Agent，再综合成员结果输出最终答案。Leader 看问题灵活决定调谁、调几个、什么顺序——不是固定流程模板。

## 上下文隔离策略（函数式委派，2026-08-28 决策）

**子 Agent 默认「函数式隔离」**：像函数一样只拿 leader 打包好的格式化输入，处理后返回结构化输出，**不共享全量对话记录**。

**框架机制**（无需额外实现，默认即此行为）：
- `TeamAgent.share_team_history` / `share_member_interactions` **默认 `False`**（`trpc_agent_sdk/teams/_team_agent.py:127,133`）
- `delegate_to_member` 用 `override_messages` 精确构造成员输入（`_team_agent.py:736`）——成员只看到 leader 委派时打包的消息，不翻历史（`agents/core/_request_processor.py:143-147`：有 override_messages 时直接使用，不走历史过滤）

**分工**：
- **Leader**：唯一看全量对话的节点——回显、确认、追问、上下文打包都集中在 Leader（与「回显/对话归 Leader」是同一件事的两面）
- **子 Agent**：纯函数——摄入侧（结构识别吃口述/原文 → 出 pending_questions；入库决策吃题目+意图 → 出 ingest_results）、查询侧（搜索吃 query → 出结果）都不需要全局视野

**理由**：token 省（摄入侧吃长文本，全量历史放大成本）；职责硬边界（子 Agent 只碰该碰的）；可测试（单元测试直接喂输入断言输出）；上下文「翻译层」集中 Leader 一处。

**例外**：个别成员确需上下文时，二选一——Leader 在 task 里显式打包（推荐，如追问时把上轮题目塞进 task），或对特定成员开 `share_member_interactions=True`。

## MVP 临时版（2026-08-28 落地，`create_gaokao_leader()`）

`src/agent/leader.py` 当前只串摄入侧两个已落地成员，跑通「待清洗题目文本 → 入库」单闭环：

- **输入泛化**（2026-08-28 用户修正）：入口不假定题目来源——口述题意、OCR 识别的多题原文、粘贴/抄写文本都是**待清洗信息**，来源形式无本质区别；Leader 只转不洗，清洗切分归结构识别
- **members** = `structure_recognition`（结构识别）+ `storage_decision`（入库决策），其余 6 个成员（查询侧 4 + 摄入侧 2）后续按 roadmap 补齐
- **不做意图分流**——用户发来题目相关内容一律按摄入处理；错题意图降级为「错因记录暂不支持」提示；`topic_names` 本轮不传
- **流程**：收原文 → 委派结构识别 → 回显题目清单问去向（入库/跳过）→ 打包 `pending_questions` + `ingest_decisions` 委派入库决策 → 汇总 `ingest_results` 返回用户
- `share_member_interactions=False` 显式写出（框架默认即 False），把「函数式隔离」钉进构造
- `LEADER_INSTRUCTION` 直接定义在 `leader.py` 内——leader 层只有这一个 Agent，不抽独立 prompts 模块
- 3 条铁律（完成标准 / 每成员每任务最多委派一次 / 不自相矛盾）写死在 instruction 里
- 入口（CLI/runner）与真实 LLM 端到端验证留后续任务；测试见 `tests/test_agent_leader.py`（全 mock，不计费）

## 意图路由（内联 Leader 系统提示词，2026-08-28 决策）

**意图识别不是独立子 Agent**，而是 `LEADER_INSTRUCTION` 内的一项能力：系统提示词里列出**当前已实现的子 Agent 清单（含各自能力一句话说明）**，Leader 收到用户请求后自行匹配意图、直接委派对应成员——意图分类本质就是路由决策，本就是 Leader 的职责，单独开一个 LLM Agent 纯属浪费一次调用 + 多维护一个 prompt。

**LEADER_INSTRUCTION 内置内容**：

1. **子 Agent 能力清单**：每个成员一句话说明「擅长什么」；**成员未实现则不列出**——Leader 只能匹配到已实现能力，避免委派不存在的成员（清单随实现进度增删）
2. **意图集合表**（语义同原意图识别 Agent 的决策表，2026-08-28 起由 Leader 直接执行）：

| 意图 | 用户输入示例 | 委派链 |
|------|-------------|--------|
| `question` | "帮我看看这道椭圆题怎么做" | 搜索信息 →（有图）VLM 理解 → 输出整理 |
| `review` | "我的错题主要集中在哪些知识点" | 聚合数据 →（找推荐题）搜索信息 → 输出整理 |
| `report` | "帮我生成这周的周报" | 聚合数据 → 输出整理 |
| `browse` | "列出2026年南昌一模的所有题目" | 搜索信息 → 输出整理 |
| `ingest` | "帮我存这道题/这道题我不会" | 文档识别 → 结构识别 → 知识整理 → 入库决策 |

3. **匹配原则**：
- `query_type="report"` 时，同时把请求解析为 `"weekly"` / `"monthly"` 写入 `GaokaoState.period_type`（2026-08-20 决策，供 REPORT_GEN 使用；原由意图识别子 Agent 解析，现由 Leader 直接解析）
- 判断不确定时默认按 `question` 走（多数场景是题目查询）
- MVP 学科固定"数学"，subject 由 Leader 静态注入，不做学科意图分类

**State 契约**（原意图识别 Agent 的输出字段，现由 Leader 写入）：

| 字段 | 内容 |
|------|------|
| `query_type` | question / review / report / browse / ingest |
| `period_type` | （仅 report）weekly / monthly |

## 委派策略（Leader 自由决定）

Leader 根据用户请求内容，自主决定：

- **调谁**：题目查询 → 搜索+(VLM)+输出；周报 → 聚合+输出；复习 → 聚合+搜索+输出；**发题入库 → 文档识别+结构识别+(知识整理)+入库决策**（意图匹配由 Leader 系统提示词完成，见上节）
- **调几个**：简单问题可只走 1-2 个成员；复杂问题多成员协作
- **什么顺序**：无固定模板，Leader 动态编排

**示例**：

- "椭圆离心率最值怎么求" → Leader 匹配 `question` → 搜索信息 →（检测到图）VLM 理解 → 输出整理
- "生成周报" → Leader 匹配 `report` → 聚合数据 → 输出整理
- "我的薄弱知识点" → Leader 匹配 `review` → 聚合数据 →（找推荐题）搜索信息 → 输出整理
- "帮我存这道题/这道题我不会" → Leader 匹配 `ingest` → 文档识别 → 结构识别 → 回显清单 → 入库决策 → 输出整理

## 实现要点

```python
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.agents import LlmAgent

# 查询侧：4 个专业子 Agent（意图路由内联 Leader 系统提示词，无独立 intent Agent）
search_agent = LlmAgent(name="search", model=model, tools=[KnowledgeSearchTool()], ...)
vlm_agent = LlmAgent(name="vlm", model=model, tools=[VLMUnderstandTool()], ...)
aggregate_agent = LlmAgent(name="aggregate", model=model, tools=[ErrorStatsTool()], ...)
format_agent = LlmAgent(name="format", model=model, instruction=FORMAT_PROMPT)

# 摄入侧：4 个专业子 Agent
doc_agent = LlmAgent(name="doc_ingest", model=model, tools=[PDFExtractTool(), VLMImageTool()], ...)
struct_agent = LlmAgent(name="struct", model=model, instruction=STRUCT_PROMPT)
knowledge_agent = LlmAgent(name="knowledge_mgr", model=model, tools=[TopicWriteTool()], ...)
store_agent = LlmAgent(name="store", model=model, tools=[QuestionWriteTool(), ErrorWriteTool()], ...)

# Team Leader 自由委派（查询 + 摄入共用）
gaokao_team = TeamAgent(
    name="gaokao_team",
    leader=LlmAgent(model=model, instruction=LEADER_PROMPT),
    members=[search_agent, vlm_agent, aggregate_agent, format_agent,
             doc_agent, struct_agent, knowledge_agent, store_agent],
)
```

## 实现注意事项

1. **Leader 委派机制依赖 tRPC-Agent 的 TeamAgent 原生能力**：`trpc_agent_sdk.teams.TeamAgent` 的 leader 自动具备自由委派能力，不需要手写路由逻辑
2. **子 Agent 间通过 TeamAgent 内部消息传递共享结果**：每个子 Agent 的返回值自动汇入 Leader 的上下文，Leader 综合后决定下一步委派
3. **State 设计沿用 `GaokaoState`**：字段不变（subject / query_type / retrieved_docs / vlm_descriptions / answer / review_suggestion + 摄入侧契约字段 raw_blocks / pending_questions / lecture_segments / topic_draft / ingest_results / ingest_decisions），reducer 字段 `execution_history` 记录子 Agent 委派链
4. **TeamAgent 可用性已确认，无备用方案**：trpc-agent 源码确认 `trpc_agent_sdk.teams` 的 Leader 委派 API 可用，且 2026-08-12 官方 `examples/team` 实测跑通（见下）；GraphAgent 备用方案已移除（2026-08-25）
5. **子 Agent 的 tools 是 FunctionTool**：VLM、知识点查询、错题统计等业务工具以 FunctionTool 形式挂到对应子 Agent，不走 MCP（MCP 仅对外暴露接口）
6. **分层边界**：子 Agent 只调 `src/ingestion`（写）/ `src/retrieval`（读）门面暴露的函数，严禁 `import src.store.*`（见 [architecture.md](../architecture.md)）

## 实测验证（2026-08-12，基于官方 `examples/team` + DeepSeek V4-Flash）

官方 team 示例（Leader + researcher/writer 协作）跑通，验证了 TeamAgent 三个关键能力：

| 验证项 | 观察结果 | 对设计的影响 |
|--------|---------|-------------|
| **Leader 自由委派** | Leader 自主决定"先取日期 → 派 researcher → 派 writer"，`delegate_to_member` 动态调用 | ✅ 主架构成立，"自由委派"是 Leader 自我状态检查 + 指令遵守 + 历史记忆的结合 |
| **多轮上下文记忆** | Turn 2 记得"上轮任务已完成/派过谁/日期已取过"（share_team_history 生效） | ✅ 学生追问"第二问呢"时 Leader 能记住题目上下文 |
| **指令约束执行** | Leader 引用 instruction 的 "delegate once per user request" 并遵守 | ✅ **Leader prompt 里的约束会被可靠执行** |

**Leader prompt 设计要点（实测得出的 3 条铁律）**：

1. **必须写清"任务完成的判断标准"**——实测 Leader 每一步会检查"还差什么"（"task is NOT finished, I need to..."）。不写清完成标准，Leader 可能提前收工或无限续派。示例："题目查询必须在给出带溯源的完整解题步骤后结束"
2. **必须写清"每个意图的成员调用上限"**——实测 Leader 会引用并遵守 "delegate to researcher only once"。示例："周报生成只派聚合+输出，不派 VLM；每个成员最多调用一次"
3. **子 Agent 的 prompt 必须自洽**——实测 researcher 的 instruction 写了 "keep reply within 50 characters"，与任务要求"详细总结"冲突，Agent 花大量推理纠结矛盾。**不要给子 Agent 互相矛盾的要求**

## 可观测性（Langfuse，V0.5 接入）

框架内置 OpenTelemetry（`invocation` span → `agent_run` / `call_llm` / `execute_tool` 子 span），官方提供 `server/langfuse/` 模块可对接 **Langfuse（LangSmith 的开源替代品，可自托管）**。V0.5 接入——TeamAgent 多 Agent 委派链用终端事件流看不清楚，Langfuse 的 trace 视图是调试刚需；自托管保证学生数据不出服务器。
