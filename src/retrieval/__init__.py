# src/retrieval/__init__.py
# 读门面包：只读不写、无 LLM（语义检索组件 + 业务查询门面）。
# 依赖 src.store 查询原语与 src.api 模型客户端；上层（agent tools / MCP / CLI）
# 一律经本包读取，严禁绕过门面直连 store 层。
