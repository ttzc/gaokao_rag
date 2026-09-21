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
# 错题本读侧（get_error_stats / get_error_details，2026-09-21 工具化）包装
# src/retrieval/error.py 读门面——dataclass 一律经 asdict 转 dict 再给 LLM。
# 其余业务查询工具（search_questions 等）待 src.retrieval 对应门面落地后
# 逐个补充，见 docs/agent/tools/retrieve_tool.md「工具清单」。
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

from src.retrieval.error import get_error_details as _get_error_details
from src.retrieval.error import get_error_stats as _get_error_stats
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


# ── 错题本读侧（薄封装 src.retrieval.error，2026-09-21 工具化） ──────────────
# 两个读门面都返回 dataclass（ErrorStats / ErrorDetail），必须 asdict 转
# dict 再返回（明细包进 {count, details}）——既对齐工具返回统一 dict 的规范、
# 可 JSON 序列化，也规避框架对 falsy 返回的 `or {}` 折叠（见 get_error_details）。
#（get_question_detail 同款模式）。
# 工具函数 __name__ 即 LLM 可见工具名，故保持 get_error_stats / get_error_details。


async def get_error_stats() -> dict:
    """统计错题本整体情况：错题总数 / 已掌握数 / 掌握率 / 错因待补数（纯 SQLite 计数，**不是语义检索**，别和 knowledge_search 混用）。

    用于回答「我有多少错题 / 掌握得怎么样」类问题，如「我的错题本情况」
    「错题掌握率多少」——一次调用给出全局画像，不需要逐题查明细。

    Returns:
        {"total": 错题总数（int）, "resolved": 已掌握数（int）, "resolve_rate": 掌握率（float，0~1，= resolved/total）, "pending_count": 错因待补数（int——口述与结构化总结都还没记的错题，仍算错题、计入 total，但不参与错因分析）}。

    空错题本返回全 0（total=0、resolve_rate=0.0）——这是合法结果不是错误，直接报告「错题本还是空的」即可。
    """
    # 门面为同步实现（SQLite 三个计数原语），经 to_thread 下沉工作线程。
    stats = await asyncio.to_thread(_get_error_stats)
    # ErrorStats dataclass → dict，保证返回体可 JSON 序列化给 LLM。
    return asdict(stats)


async def get_error_details(question_id: int) -> dict:
    """按 question_id 查该题的错题明细：错因原文 + 结构化总结 + 掌握状态（纯 SQLite 读取，非语义检索）。

    用于回答「**这道题我为什么错**」——取该题错因记录后组织回复。
    错题本一题一行，明细通常 0 或 1 条。

    Args:
        question_id: 题目主键 ID（整数），取自对话上下文或召回结果 doc_id 的数字部分（如 "q_42" → 42），不得臆造。

    Returns:
        {"count": 记录条数（0 或 1）, "details": 明细字典列表（0 或 1 条）}。details 每条字段含：error_id / question_id / error_summary（已解析的四键 dict {error_type, cause, knowledge_gap, fix_suggestion}，可空 None）/ user_reflection（用户口述错因原文，可空 None）/ pending（bool，True = 错因待补——此时应告知用户这道题还没记录错因，邀请补充）/ resolved（bool，是否已掌握）/ first_seen（首次记入时间）/ last_seen（最后一次更新时间）。

    该题无错题记录 → count=0、details=[]（题目不存在同样 count=0）——**不是错误**，如实报告「这道题不在错题本里」即可，不要重试或换 ID 猜测。
    """
    # 门面为同步实现（SQLite 单行读取），经 to_thread 下沉工作线程。
    details = await asyncio.to_thread(_get_error_details, question_id)
    # list[ErrorDetail] → list[dict]（asdict 逐条），包进非空 dict 返回：
    # 一是项目工具返回规范统一为 dict，二是规避框架 _run_async_impl 的
    # `res = ... or {}` 对 falsy 空列表的折叠（返回裸 [] 时 LLM 会看到 {}），
    # count 键让 LLM 一眼区分「无记录（0）」与「有记录（1）」。
    return {"count": len(details), "details": [asdict(d) for d in details]}


get_error_stats_tool = FunctionTool(get_error_stats)
get_error_details_tool = FunctionTool(get_error_details)

__all__ = [
    "knowledge_search_tool", "get_question_detail_tool",
    "get_error_stats_tool", "get_error_details_tool",
]
