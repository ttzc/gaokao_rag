# src/agent/tools/ingest_tool.py
# 题目摄入 FunctionTool：包装 src/ingestion/question.py 的写门面，供入库决策子 Agent 调用。
#
# 为什么薄封装而不直接 FunctionTool(门面)：
#   - 门面 ingest_question 有 14 个 keyword-only 参数，其中 image_file_ids /
#     vlm_descriptions / exam_regions 等列表参数结构复杂，LLM 易传错形状；
#   - 工具只暴露 LLM 友好子集（本文件），门面未暴露的参数走默认值；
#   - 错题分支（ingest_error）待其门面落地后再补，本轮只封装 ingest_question
#     （先题后错，见 docs/agent/tools/ingest_tool.md）。
#
# 分层铁律：工具只调 src/ingestion / src/retrieval 门面，严禁 import src.store.*。
# 不含 LLM 决策：入不入库、归哪个知识点由上游子 Agent 决定，本工具只执行写入。
#
# 注解写法约束（实测验证）：可空参数必须写 typing.Optional[...] 而非 X | None——
# FunctionTool 的 schema 生成器（tools/utils/_function_parameter_parse.py）只识别
# get_origin is Union 的 typing 写法，PEP 604 的 UnionType 会抛
# ValueError: Failed to parse the parameter。

from __future__ import annotations

import asyncio
from typing import Optional

from trpc_agent_sdk.tools import FunctionTool

from src.ingestion.question import ingest_question as _ingest_question

__all__ = ["ingest_question_tool"]


async def ingest_question(
    question_text: str,
    answer_text: str = "",
    analysis_text: str = "",
    topic_names: Optional[list[str]] = None,
    raw_file_path: Optional[str] = None,
    question_type: str = "",
    source_type: str = "exam",
    subject: str = "数学",
    exam_year: Optional[int] = None,
    exam_month: Optional[int] = None,
    question_number: Optional[str] = None,
    exam_regions: Optional[list[str]] = None,
) -> dict:
    """将一道题目写入三层存储（文件 + SQLite + 知识点关联 + 向量索引），返回业务 ID。

    Args:
        question_text: 规范题面（经题目整理归一的完整题目文本，含必要的图形描述文字），必填。
        answer_text: 标准答案文本；没有答案时传空字符串 ""。
        analysis_text: 解析文本；没有解析时传空字符串 ""。
        topic_names: 知识点名字列表，系统自动归位（已存在则复用，不存在则新建）；无知识点时传 None。
        raw_file_path: 题目来源文件在 files 表中的路径（如整卷 PDF 注册后的路径）；学生拍照单题、无源文件时传 None。
        question_type: 题型，如 "单选题" / "填空题" / "解答题"；不确定时传空字符串 ""。
        source_type: 来源类型，取值 "exam"（真题试卷）/ "homework"（作业）/ "special_topic"（专题讲义）/ "reference"（参考资料）/ "error_book"（错题本来源，预留），默认 "exam"。
        subject: 学科，默认 "数学"。
        exam_year: 考试年份，如 2026；未知时传 None。
        exam_month: 考试月份 1-12；未知时传 None。
        question_number: 题号，如 "第15题"；无题号时传 None。
        exam_regions: 考区/卷型层级列表（从小到大），如 ["全国一卷"] 或 ["深圳","广东","全国一卷"]；从来源描述提取卷型/考区，无来源或判断不了时传 None。

    Returns:
        成功时返回 {"question_id": 题目自增 ID（int）, "doc_id": 向量文档 ID（str，形如 "q_1"）}。

    Raises:
        任一层写入失败会抛出异常（不静默吞掉），此时本题未入库，应告知用户入库失败而非重试猜测。
    """
    # 门面为同步实现（文件 IO + SQLite + Chroma 嵌入调用），经 to_thread 下沉到
    # 工作线程执行，避免阻塞 Agent 事件循环（CLAUDE.md：async def 防阻塞 EventLoop）。
    return await asyncio.to_thread(
        _ingest_question,
        question_text=question_text,
        answer_text=answer_text,
        analysis_text=analysis_text,
        subject=subject,
        source_type=source_type,
        question_type=question_type,
        raw_file_path=raw_file_path,
        exam_year=exam_year,
        exam_month=exam_month,
        question_number=question_number,
        exam_regions=exam_regions,
        topic_names=topic_names,
    )


# ── FunctionTool 封装（本模块唯一交付物） ────────────────────────────────────
# 模块级实例安全：FunctionTool.__init__ 只提取函数名 + docstring，不触碰
# config / 网络（schema 声明在 _get_declaration() 里懒生成），import 零副作用。
# 工具函数的 __name__ 即 LLM 可见的工具名，故必须保持 ingest_question。
#
# 组合归 agent 层：多个工具如何拼成 tools=[...] 由入库决策子 Agent 构造时决定，
# 工具文件只交付单个 FunctionTool 实例，不预挂清单。

ingest_question_tool = FunctionTool(ingest_question)
