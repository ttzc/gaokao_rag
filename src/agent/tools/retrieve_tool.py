# src/agent/tools/retrieve_tool.py
# 读侧 FunctionTool：框架语义检索工具（MVP 纯向量比较，不带过滤）。
#
# 分层铁律：只调 src.retrieval 读门面（含 knowledge.get_knowledge() 注入
# GaokaoKnowledge），严禁 import src.store.*；框架检索工具注入 GaokaoKnowledge
# 经读门面，符合分层（2026-08-28 组件归位）。
#
# 业务查询工具（search_questions / get_error_stats 等）待 src.retrieval 对应
# 门面落地后逐个补充，见 docs/agent/tools/retrieve_tool.md「工具清单」。

from __future__ import annotations

from trpc_agent_sdk.server.knowledge.langchain_knowledge import SearchType  # 从定义处导入（searchtool 只是 re-export）
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool

from src.retrieval.knowledge import get_knowledge

# ── 检索参数（MVP 基线：top-10 纯相似度，过滤留待 Agentic 版升级） ──────────
TOP_K = 10
SEARCH_TYPE = SearchType.SIMILARITY

# 模块级实例：rag=get_knowledge() 在 import 时实体化 GaokaoKnowledge 懒单例
# （构造仅存引用：OpenAIEmbeddings 离线构造 + Chroma PersistentClient 文件句柄，
# 不发网络请求、不计费）。搜索信息子 Agent 直接挂载本实例。
knowledge_search_tool = LangchainKnowledgeSearchTool(
    rag=get_knowledge(), top_k=TOP_K, search_type=SEARCH_TYPE)

__all__ = ["knowledge_search_tool"]
