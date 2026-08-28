# 工具层总览（src/agent/tools/）

> 对应代码：`src/agent/tools/`。FunctionTool 集合，**按「写 / 读」拆两个文件，挂到各子 Agent**。工具只调两个门面暴露的函数（写：`src.ingestion`，读：`src.retrieval`），**严禁直接 `import src.store.*`**（见 [architecture.md 分层边界契约](../../architecture.md)）。

## 定位

子 Agent 的业务能力全部通过 FunctionTool 注入。工具包按「写 / 读」拆为两个文件：

- `ingest_tool.py`：写侧（PDF / 图像提取、VLM 理解、知识点 tag 归位、题目 / 错题摄入）
- `retrieve_tool.py`：读侧（语义检索、题目 / 知识点查询、错题 / 作答统计、周报聚合）+ 框架检索工具介绍

工具是子 Agent 与门面之间的适配层——内部封装函数调用 + 参数校验，**不含 LLM 决策**。

## 工具一览

| 工具文件 | 侧 | 内容 | 文档 |
|----------|----|------|------|
| [ingest_tool.md](ingest_tool.md) | 写 | `ExtractTool` / `VLMUnderstandTool` / `KnowledgeTool` / `IngestQuestionTool` | 摄入工具（写门面适配层） |
| [retrieve_tool.md](retrieve_tool.md) | 读 | 框架检索工具介绍（`LangchainKnowledgeSearchTool` / `AgenticLangchainKnowledgeSearchTool`）+ 业务查询工具规划 | 检索工具（读门面适配层） |

## 挂载矩阵（按子 Agent 维度）

| 子 Agent | 侧 | 挂载工具（文件） |
|----------|----|------------------|
| 搜索信息 | 查询 | `retrieve_tool`（框架检索 + 业务查询） |
| VLM 理解 | 查询 | `ingest_tool`（`VLMUnderstandTool`） |
| 聚合数据 | 查询 | `retrieve_tool`（统计 / 周报） |
| 输出整理 | 查询 | —（纯 LLM 格式化） |
| 文档识别 | 摄入 | `ingest_tool`（`ExtractTool` + `VLMUnderstandTool`） |
| 结构识别 | 摄入 | —（纯 LLM 语义划分） |
| 知识整理 | 摄入 | `ingest_tool`（`KnowledgeTool`） |
| 入库决策 | 摄入 | `ingest_tool`（`IngestQuestionTool`） |

## 与门面的边界

- **写**：`ingest_tool` 封装 `src.ingestion` 原子函数
- **读**：`retrieve_tool` 封装 `src.retrieval` 读门面 + 框架 `GaokaoKnowledge` 检索
- 工具内严禁 `import src.store.*`；违规 import 由 CI lint 拒绝（机制堵死「入口直连存储」）
