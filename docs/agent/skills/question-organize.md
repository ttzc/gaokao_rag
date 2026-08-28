# 题目整理 Skill（question-organize）

## 功能定位

入库链路中「单个题目单元 → 结构完整题目」的归一化环节。**不查库、不写库**，只做 LLM 语义整理。它是 `structure_recognition`（整篇切段）的下游互补：`structure_recognition` 切出题目段（或对零散输入识别出题目单元）后，**每道题目单元**都交本 Skill 归一成标准三段；纯讲解段不进本 Skill。

## 输入（无结构约束）

一个**题目单元**——`structure_recognition` 从整篇切出的题目段（试卷 / 讲义例题），或下列零散即时输入：

- 整篇切出的题目段（题干 / 答案 / 解析可能混排，需拆为三段）
- 学生口述题意（自然语言，可能缺措辞、缺条件、口语化）
- 粘贴 / OCR 文本（标题混乱、缺「参考答案」字样、编号不规范）
- VLM 对图片的描述（`vlm_descriptions`，可能含图形 / 表格 / 手写体转写）
- 聊天片段（问答交错、夹杂吐槽）

## 输出（固定分节标签，供入库决策 LLM 解析）

| 字段 | 必填 | 说明 |
|------|------|------|
| `question` | ✅ | 规范、可作答的数学题面（含已知条件、设问） |
| `answer` | ✅ | 参考答案（含要点）；缺失时在该字段内直接写明「缺标准答案：原因」 |
| `explanation` | ✅ | 分步解析，每步标注知识点；缺失时在该字段内直接写明「缺解析：原因」 |
| `image_desc` | ⬜ | VLM 图形 / 表格描述快照（含图必留痕） |
| `source_hint` | ⬜ | 来源推测（「学生口述」「2026南昌一模」），不确定留空 |

> 输出为**固定分节标签的结构化文本**（如 Markdown 分节：`题目：… / 答案：… / 解析：…`），**不强制 JSON**——下游消费方是入库决策 Agent（LLM）解析字段，不是代码解析；标签固定即可保证 LLM 可靠抽取。一次输出一道题。

> 与 `knowledge_notes`（讲解纯文本）区分：本 Skill 产出的是**题目**，不是知识点讲解；知识点关联由 `knowledge_organize` 后续处理。

## 关键原则

- **不编造**：缺失的答案 / 解析绝不硬编，在对应字段内直接写清「缺什么、为何缺」（如「缺标准答案：原题只有题面」），交由回显环节（Leader）让学生补全。
- **保留原意**：题干改写以「可作答、无歧义」为目标，不擅自改变数学含义。
- **图描必留痕**：凡输入含图片，把 VLM 描述原样纳入 `image_desc`，避免图文脱节。
- **不做切分**：多题混排的整篇文本属于 `structure_recognition` 职责；本 Skill 一次处理**一个**题目单元。
- **与知识点解耦**：不在此处标注知识点 tag，归入 `knowledge_organize` 环节。

## 边界

- **挂载 / 执行**：由结构识别 Agent `structure_recognition.py` 对每道题目单元逐题 `skill_load` 执行（或其下游归一化步骤），**不挂在入库决策 Agent**。结构识别 Agent 经 `ALLOWED_SKILLS=("question-organize",)` 白名单挂载本 Skill（白名单外不可见、不可加载，见 [README.md](README.md)）。
- **消费**：入库决策 Agent（`storage_decision.py`）只接收本 Skill 的输出 + 用户对每题的「入库 / 错题 / 跳过」意图，执行写库——不执行归一化。
- 上游：文档识别（`raw_blocks`）/ 学生即时输入（口述 + 图）
- 平行：知识整理（`knowledge_organize`）负责知识点标注，不在本 Skill 内

## 落地

实际 `SKILL.md`（含 YAML frontmatter 与完整 prompt）由 Claude 在 `src/agent/skills/question-organize/SKILL.md` 实现；本文件仅为功能说明。
