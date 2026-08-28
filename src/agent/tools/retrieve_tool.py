# src/agent/tools/retrieve_tool.py
# 读侧 FunctionTool：框架语义检索工具（MVP 纯向量比较，不带过滤）。
#
# 分层铁律：只调 src.retrieval 读门面（含 knowledge.get_knowledge() 注入
# GaokaoKnowledge），严禁 import src.store.*；框架检索工具注入 GaokaoKnowledge
# 经读门面，符合分层（2026-08-28 组件归位）。
#
# 实体化时机（2026-08-28 CI 教训）：模块级直接 rag=get_knowledge() 会在
# import 阶段构造真实 OpenAIEmbeddings（要求已解析的 DASHSCOPE_API_KEY），
# CI 无 .env 时 collection 即崩。故 knowledge_search_tool 走 PEP 562
# __getattr__ 惰性导出——import 模块零副作用，首次访问才实体化
# （离线构造 + Chroma 文件句柄，不发网络请求、不计费）。
#
# 业务查询工具（search_questions / get_error_stats 等）待 src.retrieval 对应
# 门面落地后逐个补充，见 docs/agent/tools/retrieve_tool.md「工具清单」。

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from trpc_agent_sdk.server.knowledge.langchain_knowledge import SearchType  # 从定义处导入（searchtool 只是 re-export）
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool

from src.retrieval.knowledge import get_knowledge

# ── 检索参数（MVP 基线：top-10 纯相似度，过滤留待 Agentic 版升级） ──────────
TOP_K = 10
SEARCH_TYPE = SearchType.SIMILARITY

_tool: LangchainKnowledgeSearchTool | None = None


def _build_tool() -> LangchainKnowledgeSearchTool:
    """惰性构造：首次访问时实体化 GaokaoKnowledge（需 .env，离线不计费）。"""
    global _tool
    if _tool is None:
        _tool = LangchainKnowledgeSearchTool(
            rag=get_knowledge(), top_k=TOP_K, search_type=SEARCH_TYPE)
    return _tool


def __getattr__(name: str) -> Any:
    """PEP 562：模块级惰性导出，import 模块零副作用（CI 无 .env 也能 collect）。"""
    if name == "knowledge_search_tool":
        return _build_tool()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:  # PEP 562 动态属性对静态检查器的声明（运行时不存在）
    knowledge_search_tool: LangchainKnowledgeSearchTool

__all__ = ["knowledge_search_tool"]
