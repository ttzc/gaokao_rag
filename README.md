# Gaokao RAG

> 帮助高中学生备考 —— 基于 tRPC-Agent-Python 的多模态 RAG 系统（MVP 聚焦数学）

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Framework-tRPC--Agent--Python-green.svg)](https://github.com/trpc-group/trpc-agent-python)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

## 这是什么

**Gaokao RAG 的核心目的是帮助高中学生备考**——通过 AI 让备考过程更高效、更有针对性。技术上，它把历年真题、专题讲义、个人错题导入统一知识库，用 VLM（视觉语言模型）理解图形，结合知识点图谱和 RAG 检索，为高三考生提供服务（**MVP 聚焦数学**，架构上支持扩展到理化生等科目）。

**具体功能还在演进中**，当前候选方向（MVP 逐步明确）：

- **真题检索与解析** —— 按知识点、题型、年份检索真题，AI 生成解题思路
- **知识点关联复习** —— 从一道错题出发，找到相关知识点和同类题型
- **个性化复习建议** —— 基于错题分布，生成针对性复习路径
- **周报/月报** —— 通过指令唤起"生成周报/月报"，自动聚合周期内错题、分析薄弱知识点、给出针对性练习建议（含推荐题目）

## 为什么做这个

三个动机凑在一起：

1. **巩固 RAG 知识** —— 作者刚完成 AlgoNotes RAG（算法竞赛笔记助手）的 MVP，想通过新场景深化对多模态 RAG、知识点图谱、Agent 编排的理解
2. **真实数据驱动** —— 作者高三（2026 届）在 ima 知识库中积累了 233 条备考资料（试卷、专题、错题、方法笔记），数据结构完整、场景真实
3. **帮到朋友** —— 不是玩具项目，目标是让后届考生能用起来

## 技术选型

| 层 | 选择 | 理由 |
| --- | --- | --- |
| **Agent 框架** | tRPC-Agent-Python（**TeamAgent 多 Agent 协作**） | 生产级 Agent 框架；Leader 编排已落地的 3 个子 Agent（搜索/结构识别/入库决策），意图识别内联 Leader 系统提示词，其余成员按 roadmap 补齐；内置 MCP、Session/Memory、FastAPI 服务化 |
| **用户入口（IM）** | trpc-claw（OpenClaw-like） | 通过 QQ（官方 API + nanobot 原生通道 + 适配器扩展）使用，**高考生零学习成本、无需电脑** |
| **LLM** | DeepSeek 官方 API（开发期写死 V4-Flash） | OpenAI 兼容协议接入；**模型中立**——架构上不绑定任何厂商，理论上用户可自选任何 OpenAI 兼容模型 |
| **VLM** | Qwen（DashScope 官方 API，开发期写死 Qwen3.7-Flash / Qwen3.7-Plus） | 处理数学图形（几何图、函数图像、立体几何），Flash 轻量低成本、Plus 复杂图形推理 |
| **PDF 解析** | PyMuPDF + MinerU2.5-Pro | 文本提取 + 复杂版面解析 |
| **向量存储** | Chroma | 轻量本地，和 AlgoNotes 一致 |
| **嵌入模型** | qwen3.7-text-embedding（DashScope API，dimensions=1024） | 中文能力强、长上下文整文档嵌入、与 VLM 同厂商一套 Key；换模型/维度须删重建 Chroma |
| **元数据索引** | SQLite | 题目-知识点关联、错题记录、检索过滤 |

## 核心架构

Gaokao RAG 基于 tRPC-Agent-Python 的 TeamAgent 多 Agent 协作模式构建（入口 → Agent → 存储三层结构，见 [架构设计](docs/architecture.md) 的架构图）。

## 数据来源

数据来自作者高三期间在 ima 知识库「高考2026」中积累的备考资料：

```tree
高考2026 (233 条)
├── 方法 (5 条)                # 学习策略、逆袭经验、AI 方法论
├── 心态 (4 条)                # 考前心态、知行合一
└── 知识 (220+ 条)
    └── 数学 ← MVP 入口
        ├── 试卷 (按月份)       # 核心数据源
        │   ├── 2月 (9条): 宜春期末、成都一诊、郑州一测、九江一模...
        │   ├── 3月 (9条): 南昌一模、深圳调研、湖北联考、赣州摸底...
        │   ├── 4月 / 5月 / 6月 (回归真题)
        │   └── 高二~高三上学期 (早期积累)
        ├── 专题 (9份 PDF)     # 圆锥曲线(3)、导数(2)、立体几何、解析几何、概率统计、直线参数方程
        ├── 资料 (2份)         # 考点回顾、方程运算表
        └── 作业
```

## 项目结构

```tree
gaokao_rag/
├── README.md                  # 本文件
├── CLAUDE.md                  # 给 Claude 的开发交接文档
├── pyproject.toml             # 项目依赖与元数据
├── uv.lock                    # uv 锁定依赖
├── config.toml                # 系统配置（模型、存储路径、VLM 参数）
├── .env.example               # 环境变量模板（.env 存敏感信息，不入库）
├── main.py                    # 入口（当前最小化，调试走 scripts/chat.py）
├── .github/                   # CI 工作流（全量 pytest）
├── LICENSE
├── docs/                      # 项目文档
│   ├── architecture.md        # 架构设计详解
│   ├── store/                 # 存储层设计（db/ SQLite DDL、files/、vector/）
│   ├── ingestion/             # 多模态摄取管线（README + 题目/试卷/错因/图像）
│   ├── retrieval/             # 检索读门面（README + 题目/知识点/错题/报告等）
│   ├── agent/                 # Agent 编排（README、leader、ingestion/、retrieval/、tools/、skills/）
│   ├── scripts/               # CLI 入口说明（chat.py / cli.py）
│   ├── vlm_strategy.md        # VLM 图形理解策略
│   ├── mcp/                   # MCP 接口设计（README：工具定义、传输方式）
│   ├── im/                    # IM 接入（QQ 官方 API + 通道适配器，README）
│   ├── test.md                # 测试规范（pytest）
│   ├── onboarding.md          # 协作者学习路径
│   └── roadmap.md             # 开发路线图（实际时间线 + 待完成 V0.6 → V1.0）
├── src/                       # 源代码（由 Claude 实现）
│   ├── config.py              # 配置加载（依赖图最底层，禁 import logger）
│   ├── api/                   # 模型客户端层（llm.py / embedding.py 工厂）
│   ├── agent/                 # TeamAgent 编排（leader + 子 Agent）
│   │   ├── retrieval/         #   查询侧（search 搜索信息，prompts）
│   │   ├── ingestion/         #   摄入侧（structure_recognition / storage_decision）
│   │   ├── tools/             #   工具层（ingest_tool / retrieve_tool）
│   │   └── skills/            #    Agent Skill（question-organize）
│   ├── ingestion/             # 摄入门面（无 LLM，写库）
│   ├── retrieval/             # 检索门面（无 LLM，读库）
│   └── store/                 # 三层存储（SQLite + 文件 + Chroma 向量）
├── scripts/                   # CLI 入口
│   ├── chat.py                # Team Leader 对话调试入口（模拟 QQ）
│   └── cli.py                 # 只读 CLI（browse / detail）
├── data/                      # 数据目录（运行时生成，不入库）
│   ├── chroma_db/             # 向量数据库
│   ├── files/                 # 文件层（raw 原始 / processed/{text,vlm_desc}）
│   └── gaokao.db              # SQLite 索引
├── outputs/                   # 对话实测输出（已 gitignore）
└── tests/                     # 测试（pytest，test_<module>.py）
```

## MVP 范围

**项目愿景：帮助高中学生备考**（数学 → 理化生 → 其他科目）。**MVP 只做数学单科**，但架构从第一天起就是为全科设计的——学科作为一级维度贯穿存储、路由、图谱。

**MVP 聚焦数学的理由**：

1. 数学大量依赖图形（几何图、函数图像），VLM 价值最大化
2. 作者的知识库中数学数据最完整（试卷 + 专题 + 错题）
3. 数学知识点体系清晰，易于建立图谱（动态构建的起点）

**全科扩展路径**（MVP 之后）：新增学科 = 摄入该科数据（数据驱动知识点树自动生长）+ 路由表加学科分支。VLM 策略已评估理化生图形可行性（见 [VLM 策略](docs/vlm_strategy.md) 能力边界章节），化学结构式/生物图属"质量工程"而非"换技术"。

### MVP 核心闭环

1. **摄入**：从 ima 导出数学试卷 PDF → PDF 解析 → 图像提取 → VLM 理解 → 知识点标注 → 向量化入库
2. **检索**：用户在 IM 提问 → trpc-claw 接入 → Leader 意图路由 → search Agent 语义检索（MVP 纯向量 top-10，不配过滤条件）→ 返回相关题目和解析
3. **复习**：基于错题分布 → 生成知识点薄弱分析 → 推荐复习路径
4. **周期报告**：指令唤起"周报/月报" → 聚合周期内错题 → 薄弱知识点分析 → 针对性练习建议（含推荐题目）

### IM 通道说明

| 通道 | 适用人群 | 说明 |
| ------ | --------- | ------ |
| **QQ（官方 API + 适配器）** | 高三学生（主力） | QQ 官方机器人（AppID/AppSecret），零成本、无封号风险、学生零学习成本 |
| CLI / MCP / FastAPI | 开发者 | 保留给开发调试和外部 Agent 接入 |

## 文档导航

| 文档 | 内容 |
| ------ | ------ |
| [架构设计](docs/architecture.md) | 系统架构、tRPC-Agent 集成方式、TeamAgent 编排设计 |
| [存储层](docs/store/README.md) | 三层存储总览、db/ 逐表设计、向量 Document/doc_id 策略 |
| [摄取管线](docs/ingestion/README.md) | PDF 解析、图像提取、VLM 理解、分块向量化流程 |
| [Agent 编排设计](docs/agent/README.md) | TeamAgent 子 Agent 分工、委派策略、Prompt 策略 |
| [VLM 策略](docs/vlm_strategy.md) | 模型选型、图像理解 prompt、描述粒度 |
| [MCP 接口](docs/mcp/README.md) | MCP 工具定义、传输方式 |
| [IM 接入](docs/im/README.md) | trpc-claw QQ 接入（nanobot 通道适配器）、单用户 MVP |
| [学习指南](docs/onboarding.md) | 协作者从零上手的学习路径（含 AI 搜索关键词） |
| [测试规范](docs/test.md) | pytest 单元测试约定、目录结构、fixture 规范 |
| [开发路线图](docs/roadmap.md) | 实际开发时间线 + 待完成顺序（V0.6 → V1.0 MVP）、风险与坑 |

## 开源许可

MIT License
