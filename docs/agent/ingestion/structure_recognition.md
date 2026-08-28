# 结构识别 Agent（src/agent/ingestion/structure_recognition.py）

> 对应代码：`src/agent/ingestion/structure_recognition.py`。摄入侧子 Agent 之一，**只做语义划分，不写库**。

## 定位

摄入链路的**第二棒**：把文档内容从"整篇文本"整理成「讲解段 + 题目段」的集合——讲解段原样保留，**每道题目（整篇切出的题目段或零散单题）加载 `question-organize` Skill 归一为「题目 / 答案 / 解析」三段**，并对每题生成一句话概括。**不依赖关键词/正则**，全部由 LLM 语义判断。

## 职责

- **讲解段 vs 题目段**：由 LLM 语义判断（不依赖"题目"/"答案"等关键词）；讲解段**原样**保留、不过 Skill
- **逐题归一化**：每道题目单元 `skill_load question-organize`，归一为「题目 / 答案 / 解析」三段（允许补全省略 / 统一符号 / 明确设问，禁编造缺失条件）；零散单题同样处理
- **一句话概括**：每题生成简短描述，用于回显时学生快速判断

## 决策原则

- OCR 文本格式杂乱、编号不规范，正则匹配命中率极低，**不做正则切分**
- 直接由 LLM 语义识别输出：讲解段列表 + 题目列表（位置、一句话概括、原文起止）
- 一题跨页 → 合并前后页文本后一起喂给 LLM
- 无编号（如专题讲义例题）→ LLM 按语义段落切分

## 技能挂载（2026-08-28）

- **Skill 白名单**：`ALLOWED_SKILLS = ("question-organize",)` —— 本 agent 只该用题目整理；白名单外 skill 不进 prompt、`skill_load` 报错（框架层硬约束，见 [skills/README.md](../skills/README.md)）
- **工具面收紧**：`knowledge_only`（`before_agent_callback` 注入）——只暴露 load / select_docs / list_docs，不暴露 run/exec（question-organize 纯指令无 scripts）
- 共享构造：`create_skill_tool_set()` / `SKILLS_ROOT` 在 `src/agent/skills/__init__.py`，全员复用同一 skill 目录

## 输出（State 契约）

| 字段 | 内容 | 去向 |
|------|------|------|
| `lecture_segments` | 讲解段文本列表 | **自动入库**（knowledge_notes，无需用户确认） |
| `pending_questions` | 题目清单（每题：一句话概括 + 题目 / 答案 / 解析三段 + 关联图像 / 来源（若有，来源=Skill 的 source_hint 原样保留）；**不留原文块**） | 知识整理标注 → Leader 回显 → 入库决策写库（来源行拆解映射 exam_year/question_number/exam_regions） |

**分流规则**：讲解段不进回显（自动入库）；只有题目进回显确认。这是「系统不替用户做主」与「讲解自动吸收」的边界（见 [README.md 摄入侧数据流契约](../README.md)）。
