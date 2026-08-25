# VLM 图形理解工具（src/agent/tools/vlm_tool.py）

> 对应代码：`src/agent/tools/vlm_tool.py`。VLM FunctionTool，挂到 **VLM 理解子 Agent**（查询侧，见 [../retrieval/vlm.md](../retrieval/vlm.md)）与 **文档识别子 Agent**（摄入侧，见 [../ingestion/doc_recognition.md](../ingestion/doc_recognition.md)）。

## 定位

把"看懂图"封装成一个 FunctionTool：输入图片 + 可选题目文本上下文，输出图形的结构化文本描述。描述入库（摄入时写入），查询时不重复调用（见下方调用策略）。

## 签名与用途

| Tool | 签名 | 用途 | 挂载 |
|------|------|------|------|
| `VLMImageTool` | `(image_path, context_text="") → description` | 理解图片中的图形/手写内容，输出文本描述 | 文档识别子 Agent |
| `VLMUnderstandTool` | `(file_id, question_text) → description` | 检索命中的题图，生成图形描述供答案生成 | VLM 理解子 Agent |

**描述粒度**（VLM 策略，详见 [vlm_strategy.md](../../vlm_strategy.md)）：

- 几何图形：元素、位置关系、标注变量
- 函数图像：定义域、单调性、极值点、渐近线
- 统计图表：坐标轴含义、数据趋势
- 手写内容：只描述内容，不替代用户口述错因（手写 CER 15-20% 不可靠，错因走用户口述 + LLM 结构化）

## 调用策略

1. **有图才调用**：查询侧由 `has_image` 标志触发（Chroma metadata 不含 image_file_ids，需解析 `doc_id` 两段式回查 SQLite，见 [../retrieval/vlm.md](../retrieval/vlm.md)）；摄入侧由文档识别判断
2. **描述入库，查询不重复调用**：VLM 在摄入时调用一次，描述存库；查询时直接复用存储的描述，不再花 token 二次识别（2026-08 决策）
3. **模型选型**：Qwen DashScope `qwen3.7-flash`（主力）+ `qwen3.7-plus`（升级档），OpenAI 兼容协议图片输入；开源 Qwen3-VL 作备选通道

## 实现注意

- 走 `src/api/` 的 QwenVLMModel 封装（OpenAIModel 子类，显式构造 + `client_args` 配 timeout/temperature/max_tokens + `shared_http_client_provider_factory` 复用连接）
- 超时与重试走框架 RunConfig，不在 tool 内自实现
