# 错因整理 Skill（error-organize）

## 功能定位

错题本链路中「**用户口述的错因 → 结构化错因**」的归一化环节。**不查库、不写库**，只做 LLM 语义整理。执行方是**错题管理 Agent**（2026-09-21 修订，原定结构识别 Agent）——LLM 之间传递自然语言、结构化收敛到离写库最近的 Agent（与 2026-08-27 question-organize「不强制 JSON」同一原则）。

> **与题目整理的本质差异**（决定了本 Skill 的红线）：
>
> | | question-organize | error-organize |
> |---|---|---|
> | 输入 | 题目原文（客观题面） | **用户对自己错误的反思**（主观、口语化、可能说不清） |
> | 整理的边界 | 允许补全省略措辞、统一符号、明确设问 | **只做归纳，不许补全**——补全等于替学生编造错因 |
> | 输出 | 「题目 / 答案 / 解析」三段 | **四段分节文本**（与 question-organize 同构；四键 JSON 由错题管理 Agent 调工具时经 function calling 生成） |
> | 幻觉风险 | 中（编造答案/解析） | **高**（模型天然倾向于"帮"学生想出合理原因） |

## 输入（无结构约束）

用户**用自己话说的错因**，可能来自：

- 学生即时输入：「我用了 b²=a²-c² 但算出来不对，感觉是公式记混了」
- 极短口述：「算错了」「没思路」「看漏了」
- 与题目口述混在同一条消息里（结构识别 Agent 需先把两者分开）
- 用户对已有错因的**修正**：「我不是公式记混，是审题没看清楚」

## 输出（四段分节文本，2026-09-21 定）

| 段 | 必填 | 对应四键 | 说明 |
|----|:----:|----------|------|
| `错因类型：` | ⬜ | `error_type` | 四选一：**计算错误 / 思路错误 / 知识盲区 / 审题错误**；判不出整段省略 |
| `错因：` | ✅ | `cause` | 错因归纳（**基于用户原话**，可用更准确的数学表述重述，但不添加用户没说的原因） |
| `知识点缺口：` | ⬜ | `knowledge_gap` | 暴露出的知识点缺口（如「离心率与 a/b/c 关系」）；用户未体现则整段省略 |
| `改进建议：` | ⬜ | `fix_suggestion` | 针对性改进建议（复习什么、练什么题型）；可由 LLM 给出 |

完整示例：

```text
错因类型：知识盲区
错因：把离心率 e = c/a 与 b² = a² - c² 两个关系记混，误将 b/a 当作离心率
知识点缺口：椭圆离心率定义与 a、b、c 关系
改进建议：复习「焦点三角形」模型，配套练习 3 道离心率计算题
```

**为什么是分节文本而不是 JSON**（2026-09-21 定，沿用 [question-organize](question-organize.md) 2026-08-27 的同一原则）：本 Skill 的消费方是**错题管理 Agent（LLM）**——LLM 之间传结构化分节文本（标签固定保证可靠抽取）；四键 JSON 由错题管理 Agent 调 `ingest_error` 时经 **function calling 按 `error_summary` 参数 schema 生成**，无需 Skill 强制输出 JSON。分节前缀与写门面 `_summary_to_text` 的 embedding 文本刻意一致（同一套标签），但**来源不同**：Skill 输出的是 LLM 归纳（供 Agent 读），embedding 文本是从落库 JSON 派生（供检索）。

> **不做的事**：不落库（写库是错题管理 Agent 的活）、不判断错题去向（Leader 收集用户意图）、不关联知识点 tag（那是 `question_maintain` 的职责）。

## 关键原则（红线）

1. **绝不脑补错因**——本 Skill 的命门。用户说「我算错了」，就只能归纳为"计算失误"，**不许**推断成"因粗心导致符号错误"、"对概念理解不清"这类用户没说的原因。用户的自我解释是数据，不是待完善的草稿。
2. **极短口述如实留短**：口述只有两三个字时，`cause` 就照实写短（如「算错了」），其余键留空——**宁可稀疏，不许填充**。稀疏的记录会在「错因待补」流程里被 Leader 追问补全（见 [../ingestion/error_maintain.md](../ingestion/error_maintain.md)）。
3. **可以重述，不可增加**：把口语（"我把那个公式搞混了"）改写成数学表述（"混淆了离心率公式"）是**重述**，允许；加入用户未提及的原因、动机、心理状态是**增加**，禁止。
4. **`fix_suggestion` 是唯一可以生成的内容**：复习建议属于善意补充，可由 LLM 基于题目知识点给出——但要与 `cause` 严格区分，不得让建议反过来"编造"错因。
5. **不做错因分类之外的判断**：不评价学生水平、不预测成绩、不给情绪反馈。

## 边界

- **挂载 / 执行**：由**错题管理 Agent**（`src/agent/ingestion/error_maintain.py`；2026-09-21 修订，原定结构识别 Agent）在写入错题本前 `skill_load` 执行——它的 `ALLOWED_SKILLS = ("error-organize",)`。与 question-organize（挂结构识别）不再是同一执行方：错因整理发生在「题已入库 + 用户已确认进错题本」之后，天然属于错题管理环节。
- **何时加载**：**仅当本次输入含用户口述的错因时加载**（摄入时交代了错因、事后补录、或修正错因）。纯题目输入不加载——与「讲解段不加载 question-organize」同理，按需披露。
- **消费**：错题管理 Agent **执行本 Skill**（读分节文本 → 转四键 dict）+ Leader 打包的 `question_id`，调 `ingest_error` 写库——错因整理与写库同 Agent（2026-09-21 修订），错因口述原文经 State 以自然语言传递。
- 上游：Leader 收集的用户口述错因
- 平行：题目整理（`question-organize`）——同一 Agent、同一批次、逐单元执行
- 下游门面：`src/ingestion/error.py` 的 `ingest_error` / `update_error`（见 [../../ingestion/error.md](../../ingestion/error.md)）。错题管理 Agent 把本 Skill 的分节文本转成四键 dict 传入（function calling 生成 JSON）——**JSON 到门面为止**：原样存 SQLite，写向量前再由纯函数 `_summary_to_text` 转回中文分节文本（JSON 不进向量库，见 [vector_store.md](../../store/vector/vector_store.md)）

## 落地

实际 `SKILL.md`（含 YAML frontmatter 与完整 prompt）由 Claude 在 `src/agent/skills/error-organize/SKILL.md` 实现；本文件仅为功能说明。
