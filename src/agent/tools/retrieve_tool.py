# src/agent/tools/retrieve_tool.py
# 读侧 FunctionTool：框架语义检索工具（MVP 纯向量比较，不带过滤）
# + 业务查询工具（薄封装 src.retrieval 门面，首个：get_question_detail）。
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
# 业务查询工具（如 get_question_detail）与 ingest_tool 同理：FunctionTool
# 构造零副作用（只提取函数名 + docstring），门面 import 不碰网络，
# 模块级实例直接导出，无需惰性实体化。
#
# 其余业务查询工具（search_questions / get_error_stats 等）待 src.retrieval
# 对应门面落地后逐个补充，见 docs/agent/tools/retrieve_tool.md「工具清单」。
#
# 注解写法约束（同 ingest_tool，实测验证）：可空参数必须写 typing.Optional[...]
# 而非 X | None——FunctionTool schema 生成器只识别 typing 写法。

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from trpc_agent_sdk.server.knowledge.langchain_knowledge import SearchType  # 从定义处导入（searchtool 只是 re-export）
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool
from trpc_agent_sdk.tools import FunctionTool

from src.retrieval.knowledge import get_knowledge
from src.retrieval.question import get_question_detail as _get_question_detail

# ── 检索参数（MVP 基线：top-10 纯相似度全量召回，过滤留待 Agentic 版升级） ──
TOP_K = 10
# 用 SIMILARITY_SCORE_THRESHOLD 而非名义等价的 SIMILARITY（2026-08-29 排查结论）：
# 框架 LangchainKnowledge._run_vectorstore_retrieve 里只有前者的分支走
# asimilarity_search_with_relevance_scores（返回带分元组）；SIMILARITY 走
# asearch 只回 List[Document]，SearchDocument.score 恒为默认 0.0——召回集合与
# 排序完全相同，但 LLM 永远看不到相关度。不配 score_threshold（框架不透传，
# langchain 默认 None 不过滤），检索语义仍是 top-10 纯相似度。
SEARCH_TYPE = SearchType.SIMILARITY_SCORE_THRESHOLD
# min_score 钉成 -1.0（默认 0.0 会引入静默丢弃）：langchain_chroma 在 l2 空间的
# relevance = 1 - d²/√2（d² 为平方欧氏距离，单位向量下界 -0.414），不相关文档
# 为负分——默认 0.0 会让工具层 _serialize_documents 把负分文档过滤掉，破坏
# 「top-10 全量召回」基线。-1.0 低于理论下界，保证 score 只是信息、不是闸门；
# 真要按分过滤是 Agentic/阈值检索的后续命题。
MIN_SCORE = -1.0

_tool: LangchainKnowledgeSearchTool | None = None


def _build_tool() -> LangchainKnowledgeSearchTool:
    """惰性构造：首次访问时实体化 GaokaoKnowledge（需 .env，离线不计费）。"""
    global _tool
    if _tool is None:
        _tool = LangchainKnowledgeSearchTool(
            rag=get_knowledge(), top_k=TOP_K, search_type=SEARCH_TYPE,
            min_score=MIN_SCORE)
    return _tool


def __getattr__(name: str) -> Any:
    """PEP 562：模块级惰性导出，import 模块零副作用（CI 无 .env 也能 collect）。"""
    if name == "knowledge_search_tool":
        return _build_tool()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:  # PEP 562 动态属性对静态检查器的声明（运行时不存在）
    knowledge_search_tool: LangchainKnowledgeSearchTool


# ── get_question_detail — 题目完整详情（薄封装 src.retrieval.question） ──────


async def get_question_detail(question_id: int) -> dict:
    """按 question_id 查一道题的完整详情（纯 SQLite 读取，非语义检索）。

    用于 knowledge_search 召回之后补全单题信息：完整题干（含图形描述）/
    标准答案 / 解析 / 关联知识点规范名 / 溯源字段（来源试卷 file_id、题号、
    考区、年月）/ 图片 file_id 列表。

    Args:
        question_id: 题目主键 ID（整数），取自召回结果 doc_id 的数字部分（如 "q_42" → 42），不得臆造。

    Returns:
        详情字典，字段含 question_id / doc_id / subject / source_type / question_type / content_text（题干全文）/ answer_text（答案，可空）/ analysis_text（解析，可空）/ topic_names（知识点名列表）/ file_id（来源试卷 files.id，可空）/ question_number（题号，可空）/ exam_regions（考区层级列表）/ exam_year / exam_month / image_file_ids（图片 files.id 列表）。

    Raises:
        ValueError: question_id 在库中不存在——此时如实报告未找到，不要换 ID 猜测重试。
    """
    # 门面为同步实现（SQLite 读取），经 to_thread 下沉工作线程，防阻塞 EventLoop。
    detail = await asyncio.to_thread(_get_question_detail, question_id)
    # QuestionDetail dataclass → dict，保证返回体可 JSON 序列化给 LLM。
    return asdict(detail)


# FunctionTool 构造零副作用（只提取函数名 + docstring，schema 懒生成），
# 与 knowledge_search_tool 不同，无需 PEP 562 惰性导出。
# 工具函数 __name__ 即 LLM 可见工具名，故保持 get_question_detail。
get_question_detail_tool = FunctionTool(get_question_detail)

__all__ = ["knowledge_search_tool", "get_question_detail_tool"]
