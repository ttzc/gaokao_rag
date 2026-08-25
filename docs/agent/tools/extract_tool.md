# PDF / 图像提取工具（src/agent/tools/extract_tool.py）

> 对应代码：`src/agent/tools/extract_tool.py`。提取 FunctionTool，挂到 **文档识别子 Agent**（摄入侧，见 [../ingestion/doc_recognition.md](../ingestion/doc_recognition.md)）。

## 定位

把"从任意输入提取结构化内容"封装成 FunctionTool：PDF 提取文本块 + 嵌入图像，照片/图片走 VLM 理解。只做**提取**，不做结构判断（那是结构识别子 Agent 的职责）。

## Tool 清单

| Tool | 签名 | 用途 |
|------|------|------|
| `PDFExtractTool` | `(pdf_path) → {text_blocks, image_list}` | PyMuPDF 提取文本块 + 嵌入图像列表 + 坐标信息；复杂版面降级 MinerU2.5-Pro |
| `VLMImageTool` | `(image_path) → description` | 照片中的题目内容 + 图形描述（Qwen3.7-Flash/Plus） |

## 决策原则

- PDF 优先走 PyMuPDF，版面复杂才降级 MinerU
- 照片直接走 VLM，不需要先 OCR
- 数学公式：Unicode 文本基本可用，复杂公式后续 LaTeX 化（MVP 不做）
- 提取后保留坐标信息，用于后续图像关联

## 输出

结构化文本块 + 图像列表 + 坐标信息 → 写入 `GaokaoState.raw_blocks`，供结构识别子 Agent 消费（见 [README.md 摄入侧数据流契约](../README.md)）。
