# 结构识别 Agent（src/agent/ingestion/structure_recognition.py）

> 对应代码：`src/agent/ingestion/structure_recognition.py`。摄入侧子 Agent 之一，**只做语义划分，不写库**。

## 定位

摄入链路的**第二棒**：把文档内容从"整篇文本"整理成「讲解段 + 题目段」的集合——讲解段原样保留，**每道题目（整篇切出的题目段或零散单题）加载 `question-organize` Skill 归一为「题目 / 答案 / 解析」三段**，并对每题生成一句话概括；**输入含用户口述错因时**，同时加载 `error-organize` Skill 把口述整理成四键结构化错因（2026-09-13 新增职责）。**不依赖关键词/正则**，全部由 LLM 语义判断。

## 职责

- **讲解段 vs 题目段**：由 LLM 语义判断（不依赖"题目"/"答案"等关键词）；讲解段**原样**保留、不过 Skill
- **逐题归一化**：每道题目单元 `skill_load question-organize`，归一为「题目 / 答案 / 解析」三段（允许补全省略 / 统一符号 / 明确设问，禁编造缺失条件）；零散单题同样处理
- **错因归一化（2026-09-13 新增）**：**仅当输入含用户口述的错因时** `skill_load error-organize`，把口述整理成四键 JSON（`error_type` / `cause` / `knowledge_gap` / `fix_suggestion`）。**红线：不脑补用户没说的错因**——题目整理允许"补全省略措辞"，错因整理**只许归纳、不许填充**（见 [skills/error-organize.md](../skills/error-organize.md)）
- **一句话概括**：每题生成简短描述，用于回显时学生快速判断

## 两种输入形态（2026-09-17 扩）

| 形态 | 输入内容 | 产出 |
|------|----------|------|
| **① 待清洗原文**（原有） | 题目原文（口述题意 / OCR 多题 / 粘贴文本）+ 图形描述，**必然含题面** | `pending_questions`（题目三段 + 一句话概括）+ 可选 `error_reflection` |
| **② 错因补录**（新增） | **只有错因口述、没有题面**——题目早已入库，Leader 打包 `[{question_id, 一句话概括, 口述原文}]` 列表 | 每条的 `error_reflection`；**不过 `question-organize`**（题目不重新归一化）、不产出 `pending_questions`（没有新题） |

形态 ② 的触发场景：用户事后补错因（「上次那几道错题，第 2 题我是公式记混」），
或事后修正已有错因。两种形态都**逐条** `skill_load error-organize`；一次委派可含多条
（不违反「每成员每任务最多委派一次」——同一次委派处理 N 条属于同一任务）。

## 决策原则

- OCR 文本格式杂乱、编号不规范，正则匹配命中率极低，**不做正则切分**
- 直接由 LLM 语义识别输出：讲解段列表 + 题目列表（位置、一句话概括、原文起止）
- 一题跨页 → 合并前后页文本后一起喂给 LLM
- 无编号（如专题讲义例题）→ LLM 按语义段落切分

## 技能挂载（2026-08-28）

- **Skill 白名单**：`ALLOWED_SKILLS = ("question-organize", "error-organize")`（后者 ⏳ 待实现）—— 本 agent 只用这两个整理 Skill；白名单外 skill 不进 prompt、`skill_load` 报错（框架层硬约束，见 [skills/README.md](../skills/README.md)）
- **工具面收紧**：`knowledge_only`（`before_agent_callback` 注入）——只暴露 load / select_docs / list_docs，不暴露 run/exec（question-organize 纯指令无 scripts）
- 共享构造：`create_skill_tool_set()` / `SKILLS_ROOT` 在 `src/agent/skills/__init__.py`，全员复用同一 skill 目录

## 输出（State 契约）

| 字段 | 内容 | 去向 |
|------|------|------|
| `lecture_segments` | 讲解段文本列表 | **自动入库**（knowledge_notes，无需用户确认） |
| `pending_questions` | 题目清单（每题：一句话概括 + 题目 / 答案 / 解析三段 + 关联图像 / 来源（若有，来源=Skill 的 source_hint 原样保留）+ **`error_reflection`（可选，2026-09-13 新增：用户对该题的口述错因经 `error-organize` 整理成的四键 JSON，用户没口述则省略）**；**不留原文块**） | 题目维护 Agent 标注知识点 → Leader 回显 → 入库决策写库（来源行拆解映射 exam_year/question_number/exam_regions）；有 `error_reflection` 的题再由 Leader 委派错题管理 Agent 写 errors |

**分流规则**：讲解段不进回显（自动入库）；只有题目进回显确认。这是「系统不替用户做主」与「讲解自动吸收」的边界（见 [README.md 摄入侧数据流契约](../README.md)）。
