# src/agent/tools/__init__.py
# FunctionTool 集合包：子 Agent 的业务工具注入层，每文件一个工具。
#
# 挂载矩阵与设计文档见 docs/agent/tools/（README.md 总览 + 各工具一文件）。
#
# 分层铁律（docs/agent/tools/README.md「与门面的边界」）：
#   - 工具**只允许**调用两个门面暴露的函数：写走 src/ingestion，读走 src/retrieval；
#   - **严禁直接 import src.store.***——工具层是子 Agent 与门面之间的适配层
#     （参数整形 + 校验），绕过门面直连存储会架空其编排与约束。
#   - 工具内不含 LLM 决策：LLM 负责判断并生成结构化数据，工具只负责执行。
