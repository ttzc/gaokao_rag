# src/agent/tools/ingest_tool.py
# 写侧 FunctionTool：包装 src/ingestion/question.py + src/ingestion/error.py 写门面——
#   - ingest_question（入库）→ 入库决策子 Agent 挂载；
#   - update_question / delete_question（改题 / 删题，2026-09-04 落地）
#     → 题目维护子 Agent 挂载（manage 分支，见 docs/agent/ingestion/question_maintain.md）；
#   - ingest_error / update_error / delete_error（错题本，2026-09-21 落地）
#     → 错题管理子 Agent 挂载（规划中，见 docs/agent/tools/ingest_tool.md）。
#
# 为什么薄封装而不直接 FunctionTool(门面)：
#   - 门面 ingest_question 有 14 个 keyword-only 参数，其中 image_file_ids /
#     vlm_descriptions / exam_regions 等列表参数结构复杂，LLM 易传错形状；
#   - 工具只暴露 LLM 友好子集（本文件），门面未暴露的参数走默认值；
#   - update_question 工具同理不暴露 image_file_ids——图片摄入管线（ingest_image）
#     未落地，库里不存在合法的图片 file_id，图形关联改动本版不支持；
#   - 错题本三件同理薄封装 src/ingestion/error.py：参数即门面全子集
#     （LLM 无复杂形状可传错），先题后错校验归门面，工具不重复实现。
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

from src.ingestion.error import delete_error as _delete_error
from src.ingestion.error import ingest_error as _ingest_error
from src.ingestion.error import update_error as _update_error
from src.ingestion.question import delete_question as _delete_question
from src.ingestion.question import ingest_question as _ingest_question
from src.ingestion.question import update_question as _update_question

__all__ = [
    "ingest_question_tool", "update_question_tool", "delete_question_tool",
    "ingest_error_tool", "update_error_tool", "delete_error_tool",
]


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


async def update_question(
    *,
    question_id: int,
    content_text: Optional[str] = None,
    answer_text: Optional[str] = None,
    analysis_text: Optional[str] = None,
    question_number: Optional[str] = None,
    question_type: Optional[str] = None,
    exam_regions: Optional[list[str]] = None,
    exam_year: Optional[int] = None,
    exam_month: Optional[int] = None,
    topic_names: Optional[list[str]] = None,
) -> dict:
    """修改一道已入库题目：只改你传入的字段，未传字段保持原样（部分更新）。

    参数三态语义（务必区分）：
    - 不传 / None = 不修改该字段——只想改答案就只传 answer_text，其余全部留 None；
    - "" / [] = 清空该字段；
    - topic_names 特殊：None = 知识点关联不动，[] = 清空关联，非空列表 = 全量替换
      （先清空旧关联再按新列表重建，系统自动归位），故改知识点必须传**完整的新列表**，
      不是只传增量。

    不可变字段（source_type / subject / file_id）没有修改入口——改学科或来源类型
    等于换一道题，应走「删除 + 重新入库」；图片关联（image_file_ids）本版不支持
    修改，用户要求改图形内容时如实告知暂不支持。

    Args:
        question_id: 题目 ID（questions.id），必填。
        content_text: 新题面（全量替换）；None = 不动。
        answer_text: 新答案文本；"" = 清空。
        analysis_text: 新解析文本；"" = 清空。
        question_number: 新题号，如 "第15题"；"" = 清空。
        question_type: 新题型，如 "单选题" / "填空题" / "解答题"；"" = 清空。
        exam_regions: 新考区/卷型层级列表（从小到大），如 ["南昌","江西"]；[] = 清空。
        exam_year: 新考试年份，如 2026；None = 不动（年份无法清空）。
        exam_month: 新考试月份 1-12；None = 不动（月份无法清空）。
        topic_names: 新知识点名字列表（全量替换语义，见上）；None = 关联不动。

    Returns:
        {"question_id": int, "doc_id": str, "updated_fields": 实际发生变更的字段名列表}。
        updated_fields 为空列表 = 传入值与现值全部相同，未发生任何变更。

    Raises:
        ValueError: question_id 不存在——如实报告未找到，不要换 ID 猜测重试。
    """
    # 门面为同步实现（SQLite + 知识点归位 + Chroma 重嵌 upsert），经 to_thread
    # 下沉工作线程，防阻塞 Agent 事件循环。
    return await asyncio.to_thread(
        _update_question,
        question_id=question_id,
        content_text=content_text,
        answer_text=answer_text,
        analysis_text=analysis_text,
        question_number=question_number,
        question_type=question_type,
        exam_regions=exam_regions,
        exam_year=exam_year,
        exam_month=exam_month,
        topic_names=topic_names,
    )


async def delete_question(*, question_id: int) -> dict:
    """删除一道题目（依赖闸门 + 手动清理三处）。本操作**不可逆**（无软删除 / 回收站）。

    **仅当用户已明确确认删除时才调用本工具**——回显确认（向用户列出删除范围并
    得到同意）由 Leader 在委派前完成；委派任务里没有明确的用户确认标记
    （user_confirmed=true）时，绝不调用本工具。

    依赖闸门（不做级联删除）：删前检查该题的 errors / exam_attempts 引用——
    有依赖则拒绝删除（一个字节都不删），返回 blocked_by 计数。拿到非空
    blocked_by 时应回显用户（如「该题还有 1 条错题记录，要先清掉吗」），
    确认后**先委派错题管理 Agent 调 delete_error 清依赖，再重试本工具**。
    无依赖时级联清理三处：Chroma 向量文档 + question_topics 知识点关联 +
    questions 主行。源文件不受影响：raw 原始文件与 files 登记行保留。

    Args:
        question_id: 题目 ID（questions.id），必填。

    Returns:
        {"question_id": int, "doc_id": str, "deleted": bool,
         "blocked_by": None（无依赖）或 {"errors": 错题记录数, "exam_attempts": 作答记录数},
         "cascade": {"question_topics": 删除的知识点关联条数, "vector": 向量是否已删}}。

    幂等与两种 deleted=False：blocked_by=None 且 deleted=False → 题不存在，
    如实报告「该题已不在库中」即可，不要重试；blocked_by 非 None 且 deleted=False
    → 被依赖挡住，先清依赖再删。两种都不抛异常。
    """
    # 门面为同步实现（Chroma delete + SQLite 级联删），经 to_thread 下沉工作线程。
    return await asyncio.to_thread(_delete_question, question_id=question_id)


# ── FunctionTool 封装（本模块唯一交付物） ────────────────────────────────────
# 模块级实例安全：FunctionTool.__init__ 只提取函数名 + docstring，不触碰
# config / 网络（schema 声明在 _get_declaration() 里懒生成），import 零副作用。
# 工具函数的 __name__ 即 LLM 可见的工具名，故必须保持
# ingest_question / update_question / delete_question。
#
# 组合归 agent 层：多个工具如何拼成 tools=[...] 由各子 Agent 构造时决定
# （入库决策挂 ingest_question_tool；题目维护挂 update/delete 两件），
# 工具文件只交付单个 FunctionTool 实例，不预挂清单。

ingest_question_tool = FunctionTool(ingest_question)
update_question_tool = FunctionTool(update_question)
delete_question_tool = FunctionTool(delete_question)


# ── 错题本写侧 FunctionTool（包装 src/ingestion/error.py，2026-09-21 落地） ──
# 错题定位一律用 question_id（一题一行），不是 error_id；空值语义
# （"" / {} = 未提供、不覆盖既有错因）由门面实现，工具原样透传不加工。
# 工具函数 __name__ 即 LLM 可见工具名，故必须逐字保持
# ingest_error / update_error / delete_error。


async def ingest_error(
    *,
    question_id: int,
    user_reflection: Optional[str] = None,
    error_summary: Optional[dict] = None,
) -> dict:
    """记录一道题的错因到错题本（先题后错：题目必须已入库）。

    **幂等，不必先查**——该题不在错题本则新建记录，已存在则更新该行；
    「同一题又错一次 / 又补一句错因」重复调用是常态，不是错误。

    **允许空错因**——用户只说「这题进错题本」、没说为什么错时，
    `user_reflection` 与 `error_summary` 都留空即可：先建行（错因待补），
    之后再用 `update_error` 补录。

    **再次错同一题会复位「已掌握」**——又错了说明还没掌握，掌握标记自动清除、
    最近错误时间刷新；且本次带的新错因**覆盖**旧值。
    **空值不覆盖**——只传 `question_id`（不传错因）时，已有的错因不会被清空。

    Args:
        question_id: 题目 ID（questions.id），必填，须已入库；未入库会报错，不要臆造 ID 重试。
        user_reflection: 用户口述的原始错因描述（照存原文）；用户没说就留空 None。
        error_summary: 结构化错因总结，四键 dict：{"error_type": 错因类型, "cause": 具体错因, "knowledge_gap": 知识点缺口, "fix_suggestion": 改进建议}。键名逐字照写；某个键不知道就**省略该键**，不要编造填充；没有总结就留空 None。

    Returns:
        {"error_id": 错题记录 ID（int）, "created": bool}——created=True 为新建，created=False 表示更新了已有记录（该题此前已在错题本）。

    Raises:
        ValueError: question_id 不存在（先题后错）——如实报告题目未入库，不要换 ID 猜测重试。
    """
    # 门面为同步实现（SQLite errors 行 + Chroma err_{id} 重嵌），经 to_thread
    # 下沉工作线程，防阻塞 Agent 事件循环。
    return await asyncio.to_thread(
        _ingest_error,
        question_id=question_id,
        user_reflection=user_reflection,
        error_summary=error_summary,
    )


async def update_error(
    *,
    question_id: int,
    user_reflection: Optional[str] = None,
    error_summary: Optional[dict] = None,
    resolved: Optional[bool] = None,
) -> dict:
    """补录 / 修正一道错题的错因，或标记掌握——**部分更新**，只改你传入的字段。

    三种用途：
    - **补录错因**：先前留空的「错因待补」记录，用户后来才说错在哪；
    - **修正错因**：用户改口，传入新值全量替换旧值；
    - **标记掌握**：用户说「这题我搞懂了」→ `resolved=True`（反悔则 `resolved=False`）。

    不传 / None / "" = **不修改该字段**（错因不可清空——传 "" 视为未提供，
    与 update_question 的 ""=清空 语义**相反**，勿照搬）。

    **与 ingest_error 的区别**：本工具**不自动碰 `resolved`**——它是「补录 / 修正」
    语义，不影响掌握状态；只有显式传 `resolved` 才改掌握标记。想表达「又错了」
    应该用 ingest_error。

    Args:
        question_id: 题目 ID（questions.id），必填，用于定位错题记录。
        user_reflection: 新的用户口述错因（全量替换旧原文）；None/"" = 不动。
        error_summary: 新的结构化错因总结，四键 dict：{"error_type": 错因类型, "cause": 具体错因, "knowledge_gap": 知识点缺口, "fix_suggestion": 改进建议}，键名逐字照写、不知道的键省略（全量替换旧总结）；None/{} = 不动。
        resolved: 是否已掌握：True =「这题我搞懂了」/ False = 取消掌握标记 / 不传 = 不动掌握状态。

    Returns:
        {"error_id": 错题记录 ID（int）, "updated_fields": 实际发生变更的字段名列表}。updated_fields 为空列表 = 传入值与现值全部相同、未发生任何变更（不是错误，如实报告即可）。

    Raises:
        ValueError: question_id 无错题记录——该题还没进过错题本，应改用 ingest_error 记录，不要换 ID 猜测重试。
    """
    # 门面为同步实现（SQLite 逐项比对 + 仅错因变化才重嵌），经 to_thread 下沉。
    return await asyncio.to_thread(
        _update_error,
        question_id=question_id,
        user_reflection=user_reflection,
        error_summary=error_summary,
        resolved=resolved,
    )


async def delete_error(question_id: int) -> dict:
    """把一道题**移出错题本**——**≠ 删题目**：题面、答案、解析、知识点关联全部保留。

    用户说「这道题我不想再在错题本里看到」用本工具；说「把这题删了」应该用
    delete_question（删的是题目本身），二者别混用。

    本操作**不可逆**（无软删除 / 回收站）。**仅当 Leader 已完成回显确认时
    才调用本工具**——委派任务里没有明确的用户确认时，绝不调用。

    Args:
        question_id: 题目 ID（questions.id），必填，用于定位错题记录。

    Returns:
        {"question_id": int, "deleted": bool}。

    幂等：该题无错题记录 → {"deleted": False}，不抛异常——如实报告
    「该题不在错题本里」即可，不要重试。
    """
    # 门面为同步实现（先删 Chroma err_{id} 再删 errors 行），经 to_thread 下沉。
    return await asyncio.to_thread(_delete_error, question_id=question_id)


ingest_error_tool = FunctionTool(ingest_error)
update_error_tool = FunctionTool(update_error)
delete_error_tool = FunctionTool(delete_error)
