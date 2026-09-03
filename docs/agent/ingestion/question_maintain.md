# 题目维护 Agent（`src/agent/ingestion/question_maintain.py`）

> 对应代码：`src/agent/ingestion/question_maintain.py`。摄入侧子 Agent 之一。**只调 `src/ingestion` 写门面暴露的函数，严禁 `import src.store.*`**。
>
> **改名与扩职责（2026-09-03）**：原名「知识整理 Agent」（`knowledge_organize.py`），定位是摄入链路的「tag 管家」。知识点树治理（节点合并 / 移动 / 软删）按 roadmap 后置后，Agent 一度只剩 tag 归位一件薄活——**现把题目数据的维护（改题 / 删题）并入本 Agent**，与 tag 归位同属「对已入库题目做写操作」，故更名 **题目维护 Agent / `question_maintain.py`**。改名时代码尚未落地，无逻辑迁移成本，仅引用替换（已同步 `SKILL.md` / `prompts.py` / `tests/` / `CLAUDE.md` / 全部 docs）。

## 定位

**两块职责，一个共同点：都是对已存在（或即将存在）的题目做写操作。**

| 职责 | 触发意图 | 输入 | 输出 |
|------|----------|------|------|
| **知识点归位**（原职责） | `ingest` | `pending_questions` / `lecture_segments` | `topic_draft`（每题知识点草案） |
| **题目维护**（新增） | `manage` | Leader 打包的 `question_id` + 用户改动描述 / 删除确认 | 改动字段清单 / 级联删除统计 |

核心逻辑封装在 `src/ingestion/topic.py`（`resolve_or_create_topics` / `create_topic` / `add_topic_alias` / `delete_topic`）与 `src/ingestion/question.py`（`update_question` / `delete_question`，独立可测），本 Agent 通过 FunctionTool 调用（见 [../tools/ingest_tool.md](../tools/ingest_tool.md)）。

**为什么改 / 删不放在 Leader**：Leader 是纯编排者——`create_gaokao_leader()`（`src/agent/leader.py:132`）构造时**不传 `tools=`**，只有 `name` / `model` / `members` / `instruction` / `share_member_interactions`。给它挂写工具等于把「只委派」改成「既委派又执行」，破坏现有架构一致性。且改题有实打实的 LLM 编排活（口述 → 字段结构化、来源行拆解映射 `exam_year` / `exam_regions` / `question_number`、补解析要 LLM 生成），全塞 Leader 必然臃肿。

## 双路由知识提取

知识点标注同时覆盖**题目段**与**讲解段**（2026-08 决策——两个来源都处理）：

| 输入 | 标注去向 | 写入动作 |
|------|----------|----------|
| 题目段（`pending_questions`） | `question_topics` 关联（question_id + topic_name 列表） | 随 `ingest_question` 写入 |
| 讲解段（`lecture_segments`） | `knowledge_notes.topic_tags`（名字快照 + topic_id 关联） | 讲解自动入库时写入 |

两条路由都走同一套 tag 归位原语，不重复实现。

## 归位流程（与 `topics` 表设计对应）

1. **开放式提取**：LLM 读取题目/讲解段，提取知识点名（不预定义候选集，允许新 tag）
2. **查表归位**：`search_topic` 按 name/aliases 查——命中复用已有节点；未命中 `create_topic` 新建
3. **别名归并**：同义表述（"离心率" vs "e=c/a"）`add_alias` 归并到同一节点

## 挂载工具

| Tool | 签名 | 用途 | 职责 |
| ---- | ---- | ---- | ---- |
| `search_topic` | (keyword) → [node] | 按名字/别名模糊查节点 | 知识点归位 |
| `create_topic` | (name, aliases=[]) → id | 新增 tag（内部先 search 去重） | 知识点归位 |
| `add_alias` | (topic_id, alias) | 同义表述归并（别名查重） | 知识点归位 |
| `update_question` | (question_id, ...) → {question_id, doc_id, updated_fields} | 改题目内容 / 答案 / 解析 / 元数据 / 知识点 | 题目维护 ⏳ |
| `delete_question` | (question_id) → {deleted, cascade:{...}} | 级联删题目 | 题目维护 ⏳ |

> ⏳ = 门面未落地，随 `update_question` / `delete_question` 实现后补。

---

## 题目维护：改 / 删（2026-09-03 新增职责）

### 委派契约（Leader → 本 Agent）

函数式委派下输入由 Leader 打包，分工是：

| 环节 | 归属 | 理由 |
|------|------|------|
| **定位 question_id** | **Leader** | 子 Agent 上下文隔离（`share_member_interactions=False`），看不到上轮检索结果；只有 Leader 持全量对话，知道用户指的是哪道题 |
| **删前回显确认** | **Leader** | 回显归 Leader（2026-08-28 决策）；删除不可逆，确认与用户对话必须 Leader 做 |
| **字段结构化 / 来源拆解 / 补解析** | **本 Agent** | 纯 LLM 编排活：口述「答案改成 B」→ `answer_text="B"`；「来源改成 2026 南昌一模」→ 拆解 `exam_year=2026` / `exam_regions=["南昌"]`；「解析补一下」→ LLM 生成 `analysis_text` |
| **调门面写库** | **本 Agent** | 写操作一律经子 Agent 的工具，Leader 不持写工具 |

Leader 打包给本 Agent 的输入（示意）：

```json
{
  "action": "update",              // "update" | "delete"
  "question_id": 42,
  "user_request": "答案改成 B，来源改成 2026 南昌一模第 15 题",
  "question_snapshot": {...}       // 可选：Leader 上下文里已有的题目信息，省一轮回查
}
```

### 改题（action = "update"）

1. **解析改动**：从 `user_request` 拆出字段变更——内容 / 答案 / 解析 / 题号 / 题型 / 考区 / 年月；带图题目若改动涉及图形，需先走 VLM（复用 `VLMUnderstandTool`）
2. **来源拆解**：来源行（如「2026 南昌一模第 15 题」）映射为 `exam_year` / `exam_month` / `exam_regions` / `question_number`——与入库决策 Agent 拆 `source_hint` 是同一类活，规则一致
3. **知识点重标**：若改动影响知识点判断，重新走 tag 归位（`topic_names` 全量替换 `question_topics`）
4. **调 `update_question`** → 返回 `updated_fields`

**决策原则**：
- **不编造**：用户没说清改哪个字段就返回 `clarify`，交 Leader 追问（与摄入侧「决策缺失标 pending 交还 Leader」一致）
- **部分更新语义**：只想改答案就只传 `answer_text`，其余字段传 `None`（门面层 `None` = 不修改）
- **内容变更必然重嵌向量**：由门面负责，Agent 不感知（见 [ingestion/question.md](../../ingestion/question.md)「update_question」）

### 删题（action = "delete"）

**这一支几乎没有 LLM 成分**——定位与确认都在 Leader 做完了，本 Agent 就是薄薄一层：调 `delete_question` → 把 `cascade` 统计回传给 Leader 汇报。

保留在子 Agent 而非 Leader 直挂的唯一理由：**Leader 不持写工具**（见上文「为什么改 / 删不放在 Leader」）。一行调用也要走工具，不能开特例。

**两段式流程（2026-09-03 定）**：

- **阶段 1（门面落地时）**：单次委派——Leader 回显（列删除范围：题目 + 知识点关联 + 向量）→ 用户确认 → 委派执行
- **阶段 2（errors / exam_attempts 模块落地后）**：两段式——
  1. **首次委派（预检）**：Agent 查该题在错题本 / 作答记录中的引用，返回回显素材（引用计数），**不删**
  2. Leader 回显（「该题还有 N 条错题记录，会一并删除」）→ 用户确认
  3. **二次委派（执行，`user_confirmed=true`）**：Agent 调 `delete_question` 级联删除 → 回传 `cascade` 统计
  4. 两段式跨会话轮次，预检（首轮）与执行（用户确认后的新一轮）**每轮只委派一次**——与「每成员每任务最多委派一次」铁律天然不冲突（铁律按轮次计，防同一轮内重复委派刷结果），instruction 无需写明例外，只需写清「未收到用户确认前不得执行删除」

---

## 决策原则（知识点归位）

- 开放式提取：LLM 读取题目文本，提取知识点名（不预定义候选集）
- 归位优先：先查 `topics` 表（含 aliases），命中复用，未命中新建
- 同义合并：语义等价时写入 aliases 而非新建节点
- 知识树动态演化：树随数据摄入生长，不预定义

## MVP 不做的事

树形结构（父子关系 / 路径枚举 / 树展开上卷）、节点合并（merge）、节点移动（move）、软删（deactivate）。这些放在 MVP 后的正式版实现。

## 输出（State 契约）

| 字段 | 内容 |
|------|------|
| `topic_draft` | 每题知识点草案（topic_name 列表，待归位）——`ingest` 意图 |
| `manage_result` | 改 / 删结果：`{"action", "question_id", "updated_fields"}` 或 `{"action":"delete", "cascade":{...}}`——`manage` 意图（⏳ 随门面落地） |

数据流见 [README.md 摄入侧数据流契约](../README.md)。
