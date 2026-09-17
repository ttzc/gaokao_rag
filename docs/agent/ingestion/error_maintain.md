# 错题管理 Agent（`src/agent/ingestion/error_maintain.py`）

> 对应代码：`src/agent/ingestion/error_maintain.py`（⏳ 未落地）。摄入侧子 Agent 之一，与**题目维护 Agent**（`question_maintain.py`）平级——一个管 `questions` 的写操作，一个管 `errors` 的写操作。**只调 `src/ingestion` 写门面暴露的函数，严禁 `import src.store.*`**。

## 定位

`errors` 表（第 6 张）的**写操作执行者**：错题本域内所有增 / 改 / 删都由本 Agent 落库。**它不挂 Skill、不做错因整理**——错因整理是 `error-organize` Skill 的活，由结构识别 Agent 执行（见 [../skills/error-organize.md](../skills/error-organize.md)）。

| 动作 | 门面函数 | 触发场景 | 可逆性 |
|------|----------|----------|--------|
| **记错因** | `ingest_error` | 摄入链路标为「错题」的题目写完题后写错因；也用于补写之前留空的错因 | 可逆（可再改） |
| **改错因** | `update_error` | 隔天补录、事后修正（用户改口 / 补充细节）；**`resolved` 标记同样走此路** | 可逆 |
| **删错题** | `delete_error` | 用户要把某道题移出错题本（≠ 删题目本身） | 不可逆 → 需 Leader 先回显确认 |
| **标记掌握** | `resolve_error` | 用户说「这题我搞懂了」 | 可逆 |

> `resolved` 的归属（2026-09-13 定）：**它是 `errors` 行的一个字段，本质是「修改」，归本 Agent**。至于做成独立的 `resolve_error` 还是并入 `update_error` 的一个参数，是函数粒度问题（倾向独立函数——FunctionTool 的 name / description 是 LLM 选工具的唯一依据），具体随门面落地时定。

## 为什么独立成成员（而不是并进题目维护 Agent）

结构识别与题目维护都是「对 `questions` 做事」，而本 Agent 只碰 `errors`：

| | 题目维护 Agent | 错题管理 Agent |
|---|---|---|
| 域 | 题库（`questions` / `question_topics`） | **个人错题本（`errors`）** |
| 数据性质 | 共享资产——所有学生看到的题一样 | **用户私有数据**——错因是学生自己的反思 |
| 级联行为 | 删题会级联清 `question_topics` + 向量 | 删错题不影响题目本身 |
| 现有负载 | 已背三件事：知识点归位 + 改题 + 删题 | 单一域，留出成长空间（复习计划 / 掌握度追踪） |

合并会让 `question_maintain` 的 instruction 同时讲两张表的语义，而两者的边界一旦混（比如"删"到底删题还是删错题）就是不可逆误操作。

## 委派契约（Leader → 本 Agent）

函数式委派下输入由 Leader 打包，分工：

| 环节 | 归属 | 理由 |
|------|------|------|
| **定位 `question_id` / `error_id`** | **Leader** | 子 Agent 上下文隔离（`share_member_interactions=False`），看不到对话历史；只有 Leader 知道用户指的是哪道题 |
| **回显确认**（删错题） | **Leader** | 回显归 Leader（2026-08-28 决策）；删除不可逆 |
| **与用户讨论、收集错因口述** | **Leader** | 只有 Leader 与用户对话（同一决策）；错因靠追问才拿得到 |
| **错因结构化** | **结构识别 Agent** | `error-organize` Skill 的执行方；本 Agent 只消费成品 |
| **调门面写库** | **本 Agent** | 写操作一律经子 Agent 的工具，Leader 不持写工具 |

Leader 打包给本 Agent 的输入（示意）：

```json
{
  "action": "update",                 // "ingest" | "update" | "delete" | "resolve"
  "question_id": 42,                  // 定位结果（action=ingest/update/resolve 时）
  "user_reflection": "我把 b/a 当成离心率了",   // 用户口述原文（照存，不改写）
  "error_summary": {...},             // 结构识别经 error-organize 产出的四键 JSON（可为空）
  "user_confirmed": true              // 仅 delete 需要
}
```

## 四个动作的细节

### 记错因（action = "ingest"）

- **先题后错**：题目必然已入库（`ingest_question` 先跑，拿回 `question_id`）——错题本体系依赖题目摄入体系，而非相反
- **幂等，不必先查**：`errors` 一题一行（`UNIQUE INDEX idx_errors_question`），同一题再次标为错题 = **更新已有记录**（2026-09-17 定，原 `error_count` 计数机制随之移除）。工具内部先查后写，Agent 直接调即可；更新时**传入的空值不覆盖已有错因**
- **再次错同一题自动重置 `resolved`**：更新路径把"已掌握"复位（又错了说明还没掌握），`last_seen` 刷新；此时若带来新错因按**覆盖**语义写入——多次错因的演化不用系统层追加字段，由 `error-organize` 在 `cause` 的自然语言里描述
- **允许空错因入库**（2026-09-13 定）：用户没交代错因时，`user_reflection` 与 `error_summary` 均传空 → 该行是「**错因待补**」状态（`user_reflection IS NULL AND error_summary IS NULL`），不新增状态字段
- **也允许入库时就交代错因**：用户在同一条消息里说了「这题我算错了，符号看漏了」→ 结构识别 Agent 一并过 `error-organize` 整理出 `error_summary` → 随本次写入落库
- `user_reflection` **照存原文**：它是"用户口述的原始依据"（见 [../../store/db/errors.md](../../store/db/errors.md)），Agent 不改写、不润色
- **写入范围**：SQLite `errors` 行 + Chroma `err_{id}` document（**错因非空时**才写向量；待补状态没有可嵌文本，不写）——两态一致由门面保证，Agent 不感知细节（见 [../../ingestion/error.md](../../ingestion/error.md)）

### 改错因（action = "update"）

**这是隔天补录的主路径**（2026-09-13 用户明确：错题增删改查的「改」是必做功能）：

**触发时机**（2026-09-17 定）：查询错题本 / 薄弱点时结果里含**待补**记录（`user_reflection IS NULL AND error_summary IS NULL`）
→ Leader 如实标注「（错因待补）」并邀请用户补充（**不催、不阻塞**）→ 用户口述 → 委派本 Agent 写入。

1. **补录空错因**：先前留空的记录，用户事后交代了 → 写入 `user_reflection` + `error_summary`
2. **修正已有错因**：用户改口（「我不是公式记混，是审题没看清」）→ 覆盖对应字段
3. **标记掌握**：`resolved` 置位 → 周报「掌握率」的数据源

**部分更新语义**：只传要改的字段，未传字段不动（与 `update_question` 一致）。
**错因重生成**：用户补充了新的口述时，`error_summary` 应由结构识别重新过 `error-organize` 生成，而非在本 Agent 内拼凑（本 Agent 不挂 Skill）。

### 删错题（action = "delete"）

**两段式（对齐删题）**：Leader 先回显（「把这道题移出错题本？题目本身保留」）→ 用户确认 → 委派执行。

注意区分三个「删」：

| 用户说 | 删什么 | 执行者 |
|---|---|---|
| 「把这道错题删了」 | `errors` 行 | **本 Agent** |
| 「把这道题删了」 | `questions` 主行 + 级联 | 题目维护 Agent |
| 「这题我搞懂了」 | 不删，`resolved=1` | **本 Agent** |

Leader 定位时要分清语义，拿不准先追问。

### 标记掌握（action = "resolve"）

`resolved` 置位是**可逆**的（用户可能又说「其实我还没懂」）→ 与改错因同级，不需要前置确认。

## 挂载工具

| Tool | 状态 | 签名 | 用途 |
|------|------|------|------|
| `ingest_error` | ⏳门面未落地 | (question_id, user_reflection="", error_summary=None) → {error_id} | 写错因（允许空） |
| `update_error` | ⏳门面未落地 | (question_id, user_reflection=None, error_summary=None, resolved=None) → {error_id, updated_fields} | 补录 / 修正 / 标记掌握 |
| `delete_error` | ⏳门面未落地 | (question_id) → {deleted} | 移出错题本 |
| `resolve_error` | ⏳待定 | (question_id, resolved=True) → {error_id} | 标记掌握（或并入 `update_error`） |

全部并入写侧 [`ingest_tool.py`](../tools/ingest_tool.md)，与 `ingest_question` / `update_question` / `delete_question` 同一文件。

## 输出（State 契约）

| 字段 | 内容 |
|------|------|
| `error_result` | 错题本写操作结果：`{action, question_id, error_id, updated_fields?}`；删除 → `{action: "delete", question_id, deleted}`；无法执行 → `{action, clarify: 原因}` |

数据流见 [README.md 摄入侧数据流契约](../README.md)。
