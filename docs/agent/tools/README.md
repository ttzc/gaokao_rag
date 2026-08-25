# 工具层总览（src/agent/tools/）

> 对应代码：`src/agent/tools/`。FunctionTool 集合，**每文件一个工具，挂到各子 Agent**。工具只调两个门面暴露的函数（写：`src/ingestion`，读：`src/retrieval`），**严禁直接 `import src.store.*`**（见 [architecture.md 分层边界契约](../../architecture.md)）。

## 定位

子 Agent 的业务能力全部通过 FunctionTool 注入：VLM 理解、知识点查询、错题分析、PDF/图像提取、题目摄入。工具是子 Agent 与门面之间的适配层——内部封装函数调用 + 参数校验，**不含 LLM 决策**（LLM 只负责判断与生成结构化数据，调用由工具执行）。

## 工具一览

| 工具 | 对应文件 | 能力 | 挂载子 Agent |
|------|----------|------|-------------|
| [vlm_tool.md](vlm_tool.md) | `vlm_tool.py` | VLM 图形理解（描述入库，查询不重复调用） | VLM 理解（查询侧）、文档识别（摄入侧） |
| [knowledge_tool.md](knowledge_tool.md) | `knowledge_tool.py` | 知识点查询 / tag 归位（search / create / add_alias） | 搜索信息（查询侧）、知识整理（摄入侧） |
| [error_tool.md](error_tool.md) | `error_tool.py` | 错题统计 / 薄弱点 / 作答统计（errors + exam_attempts 双源） | 聚合数据（查询侧） |
| [extract_tool.md](extract_tool.md) | `extract_tool.py` | PDF / 图像提取（PyMuPDF + VLM，只提取不切结构） | 文档识别（摄入侧） |
| [ingest_tool.md](ingest_tool.md) | `ingest_tool.py` | 题目 / 错题摄入（`ingest_question` → `ingest_error`，先题后错） | 入库决策（摄入侧） |

## 挂载矩阵（按子 Agent 维度）

| 子 Agent | 侧 | 挂载工具 |
|----------|----|---------|
| 意图识别 | 查询 | —（纯 LLM 分类） |
| 搜索信息 | 查询 | `knowledge_tool` |
| VLM 理解 | 查询 | `vlm_tool` |
| 聚合数据 | 查询 | `error_tool` |
| 输出整理 | 查询 | —（纯 LLM 格式化） |
| 文档识别 | 摄入 | `extract_tool` + `vlm_tool` |
| 结构识别 | 摄入 | —（纯 LLM 语义划分） |
| 知识整理 | 摄入 | `knowledge_tool` |
| 入库决策 | 摄入 | `ingest_tool` |

## 与门面的边界

- **写**：`ingest_tool` 封装 `src/ingestion` 的原子函数（`ingest_question` / `ingest_error` / `ingest_image` / `ingest_exam_paper`），子 Agent 不直接碰存储
- **读**：`knowledge_tool` / `error_tool` 封装 `src/retrieval` 的查询函数
- 工具内严禁 `import src.store.*`；违规 import 应在 `src/agent/__init__.py` 或 CI lint 拒绝（机制堵死「入口直连存储」）
