# src/api/__init__.py
# 模型客户端层包：封装 LLM / VLM / 嵌入三类外部模型 API（均走 OpenAI 兼容协议）。
# 模型名与 Key 统一来自 config.toml + 环境变量，不硬编码。
