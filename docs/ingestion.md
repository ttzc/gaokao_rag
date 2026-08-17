# 多模态摄取管线

## 概述

摄取管线是 Gaokao RAG 的核心模块，负责把任意学习资料转化为可检索的结构化数据。这是和 AlgoNotes RAG（纯 Markdown 输入）拉开差距的地方——必须处理 PDF 文本、嵌入图像、数学公式三种模态。

### 一句话模型

> **所有输入（资料/专题/作业/试卷）本质都是"题目 + 知识点"的集合**，摄入时分开处理：
> - **知识点文本**（LLM 总结的讲解段）→ 只需挂到知识树（`knowledge_notes`）
> - **题目** → 记录题目内容（含图片描述）、答案解析、知识点关联（`questions` 表）；错题错因单独存 `errors` 表（经 `question_id` 关联）
> - **考试试卷**额外记录作答情况（`exam_attempts`），作业试卷不需要

### 核心范式（2026-08 设计）

**任意文档上传 → 统一处理**。不管来源是试卷 PDF、专题讲义、作业照片、还是学习笔记，都走同一条逻辑：提取内容 → 识别「讲解段」和「题目段」→ 回显题目清单给用户确认 → 按用户选择分流入库。用户决定什么入库、什么进错题本，系统不替用户做主。

## 管线总览

```mermaid
flowchart LR
    A[任意文档<br/>试卷/专题/作业/笔记] --> B[1. 内容提取<br/>PDF→PyMuPDF/MinerU · 照片→VLM · 文本→直接]
    B --> C[2. 结构识别<br/>LLM 区分讲解段 vs 题目段]
    C --> D[3a. 讲解段 → 知识点讲解<br/>纯文本 RAG → knowledge_notes]
    C --> E[3b. 题目段 → 题目清单<br/>VLM/LLM 提取 + 每题一句话概括]
    E --> F[4. 回显确认<br/>"识别到 x 道题: 1.【概括】2.【概括】"<br/>用户决定入库/错题/跳过]
    F --> G[5. 分流入库<br/>questions / errors / knowledge_notes / exam_attempts]
```

## 统一摄入范式（核心交互）

**任何文档上传，第一件事是"提取题目并回显给用户确认"**——用户决定每道题的去向，系统不替用户做主：

```
学生: [上传一份文档（试卷/专题/作业/笔记）]
Bot: 已识别到 3 道题目：
     1.【圆锥曲线】椭圆焦点三角形面积最值
     2.【导数应用】恒成立参数取值范围
     3.【立体几何】二面角余弦值计算
     另识别到 1 段知识点讲解（已入库）
     
     每道题怎么处理？
     回复格式：题号 + 操作
       a = 全部入库
       b = 全部进错题本
       c = 跳过
     例："1a 2b 3c" 或 "全部 a"

学生: 1a 2b 3c
Bot: 完成：
     1 → 已入库（questions）
     2 → 已进错题本（errors）
     3 → 已跳过
```

**设计要点**：
- **每题一句话概括**由 LLM 生成（如"椭圆焦点三角形面积最值"），让学生不看原文也能判断
- **知识点讲解段自动入库**（纯文本 RAG，无需用户确认，成本低）
- 用户操作是**批量 + 按题**的组合，避免每题都问一遍（高频场景要省交互）
- 此范式统一覆盖：试卷、专题、作业、笔记、任何上传文档

## 各阶段详解

### 1. PDF 文本提取

**工具**：PyMuPDF (`fitz`)

**输入**：原始 PDF 文件路径

**输出**：
- 全文文本（按页）
- 页面布局信息（坐标、字体大小，用于判断题号编号）
- 嵌入图像列表（含坐标，用于关联到对应题目）

**策略**：
- 优先使用 PyMuPDF 的 `page.get_text("blocks")` 获取带坐标的文本块
- 如果遇到复杂版面（表格、多栏），降级使用 MinerU2.5-Pro 做版面分析
- 数学公式：PyMuPDF 提取的是 Unicode 文本（如 x² + y² = 1），基本可用；复杂公式（如求和符号、积分）可能需要 LaTeX 化处理

### 2. 题目切分

**核心难点**：把一份试卷从"整篇文本"变成"一题一题"的结构。

**切分策略**：

```
正则模式（按优先级尝试）：
1. r"(\d+)\.\s*"           → "1. " "2. " 编号（最常见）
2. r"第(\d+)题"            → "第1题" "第2题"
3. r"(\d+)\.\s*（"         → "1.（"（含子题）
4. r"\n([一二三四五六])\s" → "一、二、三"（大题编号）
```

**关联逻辑**：
- 题目文字 → 下一题题号之前的所有文本属于当前题
- **内容三分（LLM 语义划分）**：每题的内容由 LLM 语义划分「题目 / 答案 / 解析」边界——**不依赖关键词**（用户粘贴的内容可能没有"参考答案"字样），LLM 依据语义判断哪段是题目、哪段是答案、哪段是解析
- 图像关联：图像坐标落在题目的文本块坐标范围内 → 关联到该题

**内容完整性**：
- `answer_text` / `analysis_text` **允许缺失**（源资料没有 → NULL；缺失解析可后续由 LLM 补生成，MVP 不做）

**异常处理**：
- 编号不连续（跳号）→ 告警，人工确认
- 一题跨页 → 合并前后页文本
- 无编号（如专题讲义的例题）→ 按段落分隔符切分

### 3. 图像提取

**工具**：PyMuPDF `page.get_images()` + `page.get_image_rects()`

**输出**：每张图像落盘 `data/files/raw/images/extracted/{sha256}.png` 并注册到 `files` 表（kind='image'）

**过滤**：
- 排除装饰性图片（logo、水印）：面积 < 1000px² 的跳过
- 排除纯文字截图：如果 OCR 后文字占比 > 90% 且无几何线条，转为文本

### 4. VLM 图形理解

详见 [VLM 策略文档](vlm_strategy.md)。

**核心流程**：
```python
async def vlm_understand_image(image_path: str, question_text: str) -> str:
    """
    调用 Qwen3.7（VLM），传入图像 + 题目文字，
    返回图形的结构化文本描述。
    """
    # 默认用 8B，检测到复杂图形升级 32B
    model = select_vlm_model(image_path)
    
    prompt = f"""你是一位高中数学教师。请分析这张数学图形，给出结构化描述。

题目文字：{question_text}

请按以下格式描述：
1. 图形类型（函数图像 / 几何图形 / 坐标系 / 统计图 / 其他）
2. 关键元素（如：椭圆，长轴=2a，焦点在x轴上）
3. 数量关系（如：x²/4 + y²/3 = 1，离心率 e = 1/2）
4. 与题目的关联（图形提供了什么约束条件）

注意：描述要精确到可用于文本检索，但不要直接给出答案。"""
    
    response = await call_vlm(model, image_path, prompt)
    return response
```

### 5. 知识点标注

**工具**：LLM（开发期默认 DeepSeek V4-Flash，模型中立可换）

**流程**（数据驱动的动态标注，见 [数据模型](data_model.md) 的"动态构建"）：

```python
async def tag_knowledge_points(question_text: str, vlm_desc: str = "") -> list[dict]:
    """
    输入题目文本 + VLM 描述，
    输出知识点列表（开放式提取，不限定候选集）。
    返回 [{name, parent_hint}], parent_hint 为父节点语义提示（可空）。
    """
    prompt = f"""分析以下数学题目，提取所有涉及的知识点。

要求：
1. 知识点用自然名称表达（如"切线放缩"、"端点效应"），不要拘泥于现有分类
2. 如果知识点已有常见表述，使用常见表述（如"离心率"而非"e=c/a"）
3. 为每个知识点给出父节点提示（如"导数应用"），帮助挂载到知识树

题目文本：{question_text}
图形描述：{vlm_desc or "无图形"}

请输出 JSON：{{"topics": [{{"name": "...", "parent_hint": "..."}}]}}"""
    
    response = await call_llm(prompt)
    return parse_json(response)
```

**归位逻辑**（摄取管线内，与 topics 表交互）：
- 提取的知识点名 → 查 topics（含 aliases 模糊匹配）
  - 命中 → 复用节点，插入 question_topics
  - 未命中 → 新增节点（parent 由 LLM 的 parent_hint 判定，挂载后 status=active；无法判定则挂根，status=pending）
- 同义合并：与现有节点语义等价时，写入 aliases 而非新建

**知识点库**：动态演化，不再是固定候选集。树随数据摄入生长。

### 6. 分块与向量化

**分块策略**：按 `chunk_type` 拆分，每道题产出最多 3 个 chunk：

| chunk | 内容 | 是否向量化 |
|-------|------|-----------|
| `question` | 题目文本 + VLM 图形描述 | ✅ |
| `answer` | 答案 + 解析 | ✅ |
| `knowledge_point` | 知识点讲解段（概念/公式/方法，来自任何文档） | ✅ |

**向量嵌入**：
- 模型：Qwen3-Embedding-4B（DashScope API，2560 维，中文 CMTEB 68.09）
- 调用方式：DashScope 官方 API（与 VLM 同厂商，一套 Key）
- **32k 长上下文**：整份讲义/长文档可一次嵌入，简化分块策略（长文档分块失真风险大幅降低）

**知识点讲解（knowledge_point）**：来自讲义/专题/带讲解的作业中的讲解段，写入 `knowledge_notes` 表（关联 topic_id）+ 向量化。**纯文本 RAG**，不需要 VLM——文本切块后直接嵌入，比带图题目更简单。

**入库**：写入 Chroma，metadata 与 SQLite 字段对齐（见 [数据模型](data_model.md)）。

### 7. 元数据入库

写入 SQLite：
- `questions` 表：题目完整信息
- `question_topics` 表：题目-知识点关联
- 如果是错题来源：`errors` 表
- 如果是整卷作答：`exam_attempts` 表（见下）

### 8. 整卷作答摄入（exam_attempts）

试卷切分入库后，学生可能做完整张卷子并报告作答情况。**不识别手写成绩单**（同错题原则），改为用户口述 + LLM 解析：

```python
async def ingest_exam_attempt(file_id: int, user_statement: str) -> dict:
    """
    输入：试卷 file_id（files 表）+ 用户口述（如"选择错2个填空错1个，导数大题没写出来，总分68"）
    输出：写入 exam_attempts 表
    """
    # ① 按 file_id 找到试卷的所有题目（questions 表）
    questions = query_questions_by_file(file_id)
    
    # ② LLM 解析口述 → 逐题对错 + 总分
    parsed = await llm_parse_attempt_statement(user_statement, questions)
    # {question_results: [{question_id, correct, score}], total_score, max_score}
    
    # ③ LLM 生成整卷分析
    summary = await llm_generate_attempt_summary(parsed, questions)
    
    # ④ 入库
    return save_exam_attempt(file_id=file_id, **parsed, answer_summary=summary)
```

**说明**：`question_results` 用 `question_id` 关联已入库的题目，周报可据此聚合"哪些题型失分最多"。

## 摄取管线入口

```bash
# 摄取单个文件
python scripts/ingest.py data/files/raw/试卷/2026_南昌一模.pdf

# 摄取整个目录
python scripts/ingest.py data/files/raw/试卷/ --recursive

# 从 ima 知识库导入
python scripts/ingest.py --source ima --kb "高考2026" --folder "数学/试卷"
```

## 数据来源映射

| ima 知识库位置 | 摄取目标 | source_type |
|---------------|---------|-------------|
| 知识/数学/试卷/* | data/files/raw/试卷/ | exam |
| 知识/数学/专题/* | data/files/raw/专题/ | special_topic |
| 知识/数学/资料/* | data/files/raw/资料/ | reference |
| 知识/数学/错题* | errors 表（直接导入） | error_book |

## 数据源边界（明确不做的事）

**不接入题库网站（组卷网等）**，理由（2026-08 决策）：
- **版权问题**：组卷网等平台的题目版权归属不明，公开/开源项目接入有侵权风险
- **用不到**：答案解析有两个更干净的来源——**用户手动上传**（真题解析、老师讲义）和 **AI 生成**（LLM 基于题目现场生成），无需依赖第三方题库
- 数据来源闭环：真题/专题来自用户知识库导入（ima），新题来自学生拍照/作业（用户生成），解析来自用户上传 + AI 生成——**全部数据自给自足**，不依赖外部爬取

**一句话原则**：Gaokao RAG 只摄入"用户自己拥有的数据"（ima 导入 + 用户拍照 + 用户上传），不爬取、不转载第三方平台的题。

## 作业摄入（高频 · 与试卷同管线）

**作业题与试卷题在摄入层面没有本质区别**——都是 VLM 识别 → 知识点标注 → 向量化 → 入库，唯一差别是 `source_type`。**是否入库由学生按需决定**，而非系统强制。

**交互原则**（IM 场景，见 [IM 接入](im_interface.md)）：

```
学生: [发送作业照片]
Bot: 已识别题目：...
     这道题要存入知识库吗？
     1 = 存入（以后可检索/关联）
     2 = 不存（只当次解答）
     3 = 这题我做错了 → 进入错题录入
```

**三分支处理**：
1. **入库** → 走标准题目摄入管线，`source_type=homework`，文件注册到 `files` 表（title 由 agent 总结，如"2026-08-11 作业"），参与检索与知识点关联
2. **不存** → 只当次解答，不落库（省存储，适合一次性问题）
3. **错题** → 走 errors 流程（口述错因 + error_summary），与拍照错题完全一致

**作业整体情况**（对几错几，可选）→ 轻量进 `exam_attempts` 表：`file_id` 指向作业文件（files 表），`total_score/max_score` 可空（作业无满分），`question_results` 存对错。供周报统计"本周练习量/平均正确率"。

**与试卷摄入的区别**：无固定结构 → 不走 PDF 切分；单题/少量题 → 走轻量识别路径（VLM 直接识别照片中的题目，不需要题号正则）。

## 幂等性

- 同一文件重复摄取 → 检测 `files.sha256` 已存在，跳过或 `--force` 覆盖
- 增量摄取 → 只处理 `data/files/raw/` 中未摄取的新文件
- 摄取失败 → 记录到 `ingest_errors.log`，不影响其他文件
