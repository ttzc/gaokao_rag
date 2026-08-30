# 学习指南（Onboarding）

> 给刚加入 Gaokao RAG 的协作者：从零到能上手开发的学习路径。
> **怎么用**：每个知识点都给了「AI 搜索关键词」——直接用 DeepSeek / ChatGPT / Kimi 等任何 AI 搜这些词，让它给你讲；也可以自己用搜索引擎查。
> 原则：**边做边学**，卡住了再查，不要学完所有理论再动手。

## 项目简介（先知道我们在做什么）

**Gaokao RAG** 是一个帮助高中学生备考的 AI 助手（MVP 聚焦数学，后面扩展到理化生等全科）。学生通过 **QQ 聊天**就能用——问数学题、拍照存错题、每周自动收到学习周报。

- **技术栈**：Python + tRPC-Agent（腾讯开源 Agent 框架）+ DeepSeek/Qwen API + SQLite/Chroma 存储 + QQ 机器人入口
- **分工**：你和 zaochen 负责设计、验证、审代码；代码由 Claude Code 实现
- **当前状态**：设计文档已完成（仓库建立后可见），代码开发未开始——现在正是加入的好时机

## 前置要求

- Python 基础语法（会写函数、类，看得懂代码）——不会的先花 1-2 天过一遍 Python 入门
- 能装环境（python / uv / git）
- 有一个 AI 对话工具（DeepSeek、ChatGPT 等），随时查概念

## 工具链（贯穿全程，先装好）

> 这些工具不是"知识"，是"每天要用的家伙什"。不用一次学精，先会用，边用边熟。

### 1. 终端 / 命令行

| 工具 | 要掌握什么                                  | AI 搜索关键词               |
| ---- | ------------------------------------------- | --------------------------- |
| 终端 | cd / ls / mkdir / 跑 Python 脚本            | "命令行入门 常用命令"       |
| 路径 | Windows 的 `D:\xxx` 和 Git Bash 的 `/d/xxx` | "git bash 路径转换 windows" |

**过关标准**：能在终端里 `cd` 到项目目录，跑 `python xxx.py`。

### 2. git

| 工具     | 要掌握什么                         | AI 搜索关键词                  |
| -------- | ---------------------------------- | ------------------------------ |
| 基本流程 | clone / add / commit / push / pull | "git 入门教程"、"git 常用命令" |
| 分支     | branch / checkout / merge          | "git 分支协作流程"             |
| 冲突     | 多人改同一文件怎么解决             | "git 解决冲突"                 |

**过关标准**：clone 项目 → 建自己的分支 → 提交改动 → push，全程不出错。

### 3. Python 环境（uv）

| 工具     | 要掌握什么                    | AI 搜索关键词                     |
| -------- | ----------------------------- | --------------------------------- |
| uv       | 建虚拟环境 / 装依赖           | "uv python 虚拟环境"、"uv vs pip" |
| 依赖管理 | pyproject.toml / requirements | "pyproject.toml 是什么"           |

**过关标准**：用 uv 建一个环境，装好项目依赖，能 import 项目模块。

### 4. Claude Code（本项目核心工作流）

> **Gaokao RAG 的代码由 Claude Code 实现**。你和 zaochen 负责设计、验证、审代码——但**必须会跟 Claude Code 协作**，这是本项目最独特的技能。

| 工具     | 要掌握什么                      | AI 搜索关键词                                   |
| -------- | ------------------------------- | ----------------------------------------------- |
| 会话启动 | 在项目目录启动 Claude Code 会话 | "claude code 入门 使用"、"claude code cli 安装" |
| 任务下发 | 用自然语言描述要它做的事        | "claude code 任务 编写"、"ai 编程 提示词 规范"  |
| 审代码   | 看它改了什么、为什么改          | "claude code diff 审查"、"code review 方法"     |
| 迭代     | 不满意就让它改，直到符合要求    | "ai 编程 迭代 修改"                             |

**和 Claude Code 协作的要点**：

- 项目根目录有一份 `CLAUDE.md`（仓库建立后可见），里面写了分工、技术栈、决策记录——Claude Code 会读它，你也要读
- 你说"帮我实现 X"，它写代码；你看 `git diff` 审代码；有问题就让它改
- **不要直接让它乱改**：架构级改动先找 zaochen 确认
- **省钱技巧**：Claude Code 可以用 **cc switch**（Claude Code Switch）切换模型供应商，接入 DeepSeek / Qwen 等国产模型，成本比官方 Claude 模型低很多——开发期够用

**过关标准**：让 Claude Code 在项目里实现一个 20 行的小功能，你能看懂它的改动并提意见。

### 5. 其他辅助

| 工具 | 用途                                             |
| ---- | ------------------------------------------------ |
| MCP  | 后续通过 MCP 调项目工具（开发接口），V1.0 才涉及 |

## 阶段 0：Python 预热（约 1 周）

| 知识点      | 要掌握什么                             | AI 搜索关键词                                   |
| ----------- | -------------------------------------- | ----------------------------------------------- |
| Python 异步 | `async def` / `await` / `asyncio` 基础 | "Python asyncio 教程"、"async await 到底是什么" |
| 类型注解    | 函数签名写清参数/返回类型              | "Python 类型注解 typing 入门"                   |

**过关标准**：能写一个 `async def` 函数，用 `await` 调用另一个协程，跑通。

## 阶段 1：LLM API（约 1 周）

| 知识点          | 要掌握什么                              | AI 搜索关键词                                          |
| --------------- | --------------------------------------- | ------------------------------------------------------ |
| OpenAI 兼容协议 | `base_url` / `api_key` / `model` 三件套 | "OpenAI API 调用 python 示例"、"chat completions 参数" |
| 流式输出        | SSE / stream 流式接收                   | "OpenAI stream 流式输出 python"                        |
| 多轮对话        | messages 列表的 role 体系               | "chat messages system user assistant 区别"             |
| 模型中立        | 换厂商只改三参数                        | "OpenAI 兼容 API 换 base_url 切换模型"                 |

**过关标准**：用 DeepSeek API 写一个能多轮对话的命令行脚本。

## 阶段 2：RAG 核心（约 1-2 周）

| 知识点        | 要掌握什么                     | AI 搜索关键词                                  |
| ------------- | ------------------------------ | ---------------------------------------------- |
| Embedding     | 文本怎么变成向量、相似度怎么算 | "什么是 embedding 词向量"、"向量相似度 余弦"   |
| 向量检索      | 向量库怎么找最近邻             | "向量数据库 检索 原理"、"ANN 近邻搜索"         |
| Chunk 切分    | 长文档怎么切成块               | "RAG chunking 分块策略"、"document chunk 切分" |
| RAG 全流程    | 摄入 → 索引 → 检索 → 生成      | "RAG 检索增强生成 原理"、"RAG 完整流程 图解"   |
| metadata 过滤 | 向量检索时按字段过滤           | "RAG metadata filter 元数据过滤"               |

**过关标准**：能把几段文本嵌入向量库，问一个问题检索出最相关的片段。

## 阶段 3：tRPC-Agent 框架（约 1 周）

| 知识点   | 要掌握什么                      | AI 搜索关键词                                          |
| -------- | ------------------------------- | ------------------------------------------------------ |
| 框架定位 | tRPC-Agent 是什么、解决什么问题 | "tRPC-Agent 腾讯 agent 框架"、"trpc agent python 介绍" |
| 最小示例 | 跑通官方 quickstart             | "trpc-agent-python quickstart"、"tRPC-Agent 快速开始"  |
| 模型接入 | OpenAIModel / LLMModel          | "tRPC-Agent OpenAIModel"、"agent 框架 model 接入"      |
| Runner   | Agent 怎么被跑起来              | "tRPC-Agent Runner 用法"                               |

**过关标准**：本地跑通官方示例，模型在命令行回答你的问题。

## 阶段 4：Agent 编排（约 1-2 周，V0.5 核心）

| 知识点         | 要掌握什么                  | AI 搜索关键词                                      |
| -------------- | --------------------------- | -------------------------------------------------- |
| TeamAgent      | Leader 自由委派多个子 Agent | "TeamAgent 多 agent 协作"、"agent team 委派模式"   |
| 子 Agent 分工  | 每个职责一个专业 Agent      | "agent 子代理 分工 设计"、"multi-agent 架构"       |
| 委派策略       | Leader 按需决定调谁/顺序    | "agent leader delegation 委派"                     |
| FunctionTool   | 把函数注册成工具给模型调用  | "function calling 函数调用"、"agent tool use 原理" |
| Session/Memory | 多轮对话记忆                | "agent session memory 会话记忆"                    |

**过关标准**：搭一个 TeamAgent（Leader + 2-3 个子 Agent），让 Leader 自动委派任务给合适的子 Agent 并汇总结果。

## 阶段 5：存储层（约 1 周，V0.3/V0.4）

| 知识点             | 要掌握什么                    | AI 搜索关键词                                           |
| ------------------ | ----------------------------- | ------------------------------------------------------- |
| SQLite 表设计      | 建表、外键、索引              | "SQLite 建表 外键 索引"                                 |
| 路径枚举（树存储） | 树形结构存储与查询（Materialized Path）| "SQLite materialized path 树"、"SQLite 树形结构 path 枚举" |
| Chroma             | 向量库基本操作                | "Chroma 向量数据库 python"、"chroma collection 用法"    |
| qwen3.7-text-embedding | 中文嵌入模型（DashScope API） | "Qwen3-Embedding 使用 教程"、"dashscope 文本向量化 API" |

**过关标准**：SQLite 里建一棵知识点树并用递归查出来；Chroma 里增删查向量。

## 阶段 6：VLM 多模态（约 1 周）

| 知识点      | 要掌握什么           | AI 搜索关键词                                  |
| ----------- | -------------------- | ---------------------------------------------- |
| VLM 概念    | 视觉语言模型能做什么 | "VLM 视觉语言模型 原理"、"多模态大模型 是什么" |
| Qwen-VL API | 传图片给模型看图说话 | "Qwen-VL API 调用"、"dashscope 多模态 图片"    |
| 图转文本    | 图片描述进 RAG       | "VLM 图片 描述 生成"                           |

**过关标准**：给 Qwen-VL 传一张数学图形图片，让它输出结构化描述。

## 阶段 7：IM 接入（V1.0，可后置）

| 知识点    | 要掌握什么          | AI 搜索关键词                                 |
| --------- | ------------------- | --------------------------------------------- |
| 消息网关  | IM 消息怎么进 Agent | "nanobot channels 接入"、"agent 消息网关"     |
| QQ 机器人 | 官方 API / AppID    | "QQ 机器人 开放平台 AppID"、"qq bot 官方 API" |
| WebSocket | 长连接概念          | "WebSocket 是什么"                            |

**过关标准**：手机 QQ 给机器人发消息，机器人回复。

## 学习方法（项目约定）

1. **边做边学**：每个阶段跟着项目里程碑走，产出能跑的东西，不追求学完理论
2. **先读源码**：tRPC-Agent 的源码和文档都在官方 GitHub（<https://github.com/trpc-group/trpc-agent-python>），框架不是黑盒，卡住就去看
3. **写下来**：把学到的知识点记成笔记，把"看懂"变成"会做"
4. **每周回顾**：像项目里的周报一样，每周复盘哪里强哪里弱
5. **工具即习惯**：终端 / git / Claude Code 不用专门学，每天用、每天熟——卡住就先搜关键词，别死磕

## 参考资料

> 以下项目文档在 **git 仓库建立并发布到 GitHub 后可见**，届时从仓库 README 进入。现阶段你可以先靠本指南的 AI 搜索关键词自学。

- 项目 README（项目定位、技术栈）
- `CLAUDE.md`（分工边界 + 技术栈 + 决策记录——和 Claude Code 协作前必读）
- `docs/architecture.md`（系统架构）
- `docs/store/README.md`（存储层文档入口：三层总览、db/ 逐表设计、vector/ 向量策略）
- `docs/agent/README.md`（TeamAgent 子 Agent 编排）
- `docs/roadmap.md`（开发路线：已完成时间线 + 待完成顺序 V0.6 → V1.0）

**独立可用的学习资源（现在就有的）**：

- DeepSeek / Qwen 官方文档（API 调用示例）
- tRPC-Agent 官方 GitHub：<https://github.com/trpc-group/trpc-agent-python> （源码 + 文档 + 示例都在这里）
- 本指南每个阶段给出的 AI 搜索关键词——这是你现阶段的主要学习入口
