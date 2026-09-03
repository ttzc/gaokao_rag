# 文档识别 Agent（src/agent/ingestion/doc_recognition.py）

> 对应代码：`src/agent/ingestion/doc_recognition.py`。摄入侧子 Agent 之一，**只调 `src/ingestion` 写门面 / `src/agent/tools` 提取工具，严禁 `import src.store.*`**。

## 定位

摄入链路的**第一棒**：接收学生上传的任意格式（PDF / 照片 / 文本），提取结构化内容。**只提取不写库**——产出 `GaokaoState.raw_blocks`（内存态），由结构识别子 Agent 消费。

## 职责

- 判断输入格式（PDF / 图片 / 纯文本）
- 提取文本内容（PDF 走 PyMuPDF，照片走 VLM）
- 记录嵌入图像与坐标信息（供后续图像关联）

## 挂载工具

- `ExtractTool`：PDF / 图像两路提取（PDF 走 PyMuPDF 文本块 + 嵌入图像列表、复杂版面降级 MinerU2.5-Pro；照片走 Qwen3.7-Flash/Plus 理解题目内容 + 图形描述），只提取不切结构（见 [../tools/ingest_tool.md](../tools/ingest_tool.md)）
- `VLMUnderstandTool`：图形结构化描述（描述入库、查询不重复调用）（见 [../tools/ingest_tool.md](../tools/ingest_tool.md)）

## 决策原则

- PDF 优先走 PyMuPDF，版面复杂才降级 MinerU
- 照片直接走 VLM，不需要先 OCR
- 数学公式：Unicode 文本基本可用，复杂公式后续 LaTeX 化（MVP 不做）
- 提取后保留坐标信息，用于后续图像关联

## 输出（State 契约）

| 字段 | 内容 |
|------|------|
| `raw_blocks` | 结构化文本块 + 图像列表 + 坐标信息 |

数据流见 [README.md 摄入侧数据流契约](../README.md)。
