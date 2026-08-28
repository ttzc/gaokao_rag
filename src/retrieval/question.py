# src/retrieval/question.py
# 读门面·题目检索：把「结构化过滤 + SQLite 补全」组合成对学生友好的题目查询接口。
# 本模块只读，不触碰任何 store 写方法（见 docs/retrieval/question.md）。
#
# 设计：
#   - QuestionHit      — 检索/浏览命中摘要（列表展示用，content_text 为截断摘要）
#   - QuestionDetail   — 题目完整详情（题干 + 答案 + 解析 + 关联知识点 + 图片 file_id）
#   - browse_questions — 结构化浏览，纯 SQLite 过滤，不走向量检索
#   - get_question_detail — 按 id 取完整详情，供输出整理 Agent 拼溯源引用
#
# 依赖 src.store.db 查询原语（questions / question_topics / topics / files），
# 门面层只做组合与业务封装，不写裸 SQL、不碰 Chroma。
#
# TODO(semantic): search_questions（语义召回 + 过滤）待接入 get_knowledge()，
#                 见 docs/retrieval/question.md「search_questions」。

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from trpc_agent_sdk.log import logger

from src.store.db.files import get_files_db
from src.store.db.question_topics import get_question_topics_db
from src.store.db.questions import get_questions_db
from src.store.db.topics import get_topics_db

# 列表展示用的题干摘要截断长度（字符）
_SUMMARY_LEN = 120

# browse_questions 支持的过滤条件白名单（LLM 构造 filters 时防手滑：
# 未知键直接报错，而不是静默忽略导致结果比预期多）
_SUPPORTED_FILTERS = frozenset({
    "subject",      # 学科，如 "数学"
    "source_type",  # 来源类型，如 "exam"
    "exam_year",    # 年份，如 2026
    "exam_month",   # 月份 1-12
    "question_type",  # 题型，如 "解答题"
    "exam_region",  # 考区名单值（对 exam_regions 层级列表做包含匹配），如 "南昌"
    "topic_name",   # 知识点规范名（经 question_topics 反查题目）
    "file_id",      # 来源试卷/作业的 files.id（列出某份卷子的全部题目）
    "limit",        # 返回条数上限
})


# ═══════════════════════════════════════════════════════════════════════════════
# 业务语义对象（非裸 Row）
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class QuestionHit:
    """题目检索/浏览命中（列表展示用摘要）。

    Attributes:
        doc_id: Chroma 文档 ID（``q_{id}``），与向量层桥接。
        question_id: ``questions`` 表主键。
        content_text: 题干摘要（截断至 ``_SUMMARY_LEN`` 字符）。
        question_type: 题型（单选题/多选题/填空题/解答题）。
        exam_regions: 考区层级列表（从小到大），无则空列表。
        exam_year: 年份，可空。
        exam_month: 月份 1-12，可空（与 exam_year 组合展示"2026-03"）。
        question_number: 题号（如 "第15题"），可空。
        has_image: 是否含图片（image_file_ids 非空）。
        score: 语义相关度（search_questions 填充；browse_questions 恒为 None）。
    """

    doc_id: str
    question_id: int
    content_text: str
    question_type: str
    exam_regions: list[str] = field(default_factory=list)
    exam_year: int | None = None
    exam_month: int | None = None
    question_number: str | None = None
    has_image: bool = False
    score: float | None = None


@dataclass
class QuestionDetail:
    """题目完整详情（供输出整理 Agent 拼答案与溯源引用：哪份试卷第几题）。

    Attributes:
        question_id / doc_id: 同上。
        subject: 学科。
        source_type: 来源类型。
        file_id: 来源试卷/作业的 ``files.id``（溯源用），可空。
        question_number: 题号，可空。
        question_type: 题型。
        exam_regions: 考区层级列表。
        exam_year / exam_month: 年月，可空。
        content_text: 题干全文（VLM 处理后含图形描述）。
        answer_text: 标准答案，可空。
        analysis_text: 解析，可空。
        topic_names: 关联知识点规范名列表（已经 topics 表校验）。
        image_file_ids: 题目图片的 ``files.id`` 列表（已校验存在，供上层拼路径）。
    """

    question_id: int
    doc_id: str
    subject: str
    source_type: str
    question_type: str
    content_text: str
    file_id: int | None = None
    question_number: str | None = None
    exam_regions: list[str] = field(default_factory=list)
    exam_year: int | None = None
    exam_month: int | None = None
    answer_text: str | None = None
    analysis_text: str | None = None
    topic_names: list[str] = field(default_factory=list)
    image_file_ids: list[int] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════════════════════════


def _json_list(raw: Any) -> list[Any]:
    """安全解析 JSON 列表字段（exam_regions / image_file_ids）。

    Args:
        raw: 数据库中的原始值（JSON 字符串 / None / 空串）。

    Returns:
        解析出的列表；字段为空或非法 JSON 时返回空列表（不抛异常）。
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("JSON 列表字段解析失败，按空处理: %r", raw)
        return []
    return value if isinstance(value, list) else []


def _summary(text: str) -> str:
    """截断题干为列表展示摘要。"""
    if len(text) <= _SUMMARY_LEN:
        return text
    return text[:_SUMMARY_LEN] + "…"


def _row_to_hit(row: dict[str, Any], score: float | None = None) -> QuestionHit:
    """questions 记录字典 → QuestionHit（JSON 字段解析 + 题干截断）。"""
    return QuestionHit(
        doc_id=row["doc_id"],
        question_id=row["id"],
        content_text=_summary(row.get("content_text") or ""),
        question_type=row.get("question_type") or "",
        exam_regions=[str(r) for r in _json_list(row.get("exam_regions"))],
        exam_year=row.get("exam_year"),
        exam_month=row.get("exam_month"),
        question_number=row.get("question_number"),
        has_image=bool(_json_list(row.get("image_file_ids"))),
        score=score,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# get_question_detail — 题目完整详情
# ═══════════════════════════════════════════════════════════════════════════════


def get_question_detail(question_id: int) -> QuestionDetail:
    """按 ``question_id`` 取题目完整详情（纯 SQLite 读取，无向量检索）。

    内部流程（docs/retrieval/question.md）：
      1. ``questions`` 主表取题目内容（题干 + 答案 + 解析）
      2. ``question_topics`` join ``topics`` 取关联知识点名列表
         （topics 表未登记的悬空 tag 跳过并 warning）
      3. ``image_file_ids`` → ``files`` 校验图片记录存在（供上层拼 VLM 描述路径）

    Args:
        question_id: ``questions`` 表主键。

    Returns:
        QuestionDetail 完整详情。

    Raises:
        ValueError: ``question_id`` 不存在。
    """
    row = get_questions_db().get_by_id(question_id)
    if row is None:
        raise ValueError(f"question_id={question_id} 不存在，无法取详情")

    # ── 知识点：question_topics join topics（name 规范化校验） ──────
    topics_db = get_topics_db()
    topic_names: list[str] = []
    for rel in get_question_topics_db().get_by_question(question_id):
        name = rel["topic_name"]
        if topics_db.get_by_name(name) is None:
            logger.warning(
                "get_question_detail: question_id=%d 关联知识点 %r 未在 topics 登记，跳过",
                question_id, name,
            )
            continue
        topic_names.append(name)

    # ── 图片：image_file_ids → files 校验存在 ───────────────────────
    files_db = get_files_db()
    image_file_ids: list[int] = []
    for fid in _json_list(row.get("image_file_ids")):
        if files_db.get_by_id(int(fid)) is None:
            logger.warning(
                "get_question_detail: question_id=%d 图片 file_id=%s 不在 files 表，跳过",
                question_id, fid,
            )
            continue
        image_file_ids.append(int(fid))

    return QuestionDetail(
        question_id=row["id"],
        doc_id=row["doc_id"],
        subject=row["subject"],
        source_type=row["source_type"],
        question_type=row.get("question_type") or "",
        content_text=row.get("content_text") or "",
        file_id=row.get("file_id"),
        question_number=row.get("question_number"),
        exam_regions=[str(r) for r in _json_list(row.get("exam_regions"))],
        exam_year=row.get("exam_year"),
        exam_month=row.get("exam_month"),
        answer_text=row.get("answer_text"),
        analysis_text=row.get("analysis_text"),
        topic_names=topic_names,
        image_file_ids=image_file_ids,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# browse_questions — 结构化浏览（无语义）
# ═══════════════════════════════════════════════════════════════════════════════


def browse_questions(filters: dict) -> list[QuestionHit]:
    """结构化浏览题目：纯 SQLite 过滤，不走向量检索。

    用于"列出 2026 南昌一模所有解答题"这类明确筛选。支持的过滤键见
    ``_SUPPORTED_FILTERS``（subject / source_type / exam_year / exam_month /
    question_type / exam_region / topic_name / file_id / limit），全部可选、
    相互 AND；未登记键抛 ``ValueError``。

    Args:
        filters: 过滤条件字典。``exam_region`` 为单值，对考区层级列表做包含
            匹配（如 "南昌" 命中 ["南昌","江西","全国一卷"]）；``topic_name``
            经 ``question_topics`` 反查题目 id 集合；``limit`` 截断返回条数。

    Returns:
        QuestionHit 列表，按 ``id`` 升序；无命中时为空列表。
        ``score`` 恒为 None（无语义相关度）。

    Raises:
        ValueError: filters 含不支持的键。
    """
    unknown = set(filters) - _SUPPORTED_FILTERS
    if unknown:
        raise ValueError(f"browse_questions 不支持过滤条件: {sorted(unknown)}")

    # ── 知识点反查：先取 question_id 集合（无关联 → 提前空返回） ────
    topic_ids: set[int] | None = None
    topic_name = filters.get("topic_name")
    if topic_name is not None:
        rels = get_question_topics_db().get_by_topic(topic_name)
        topic_ids = {rel["question_id"] for rel in rels}
        if not topic_ids:
            return []

    # ── 取候选行：file_id 走整卷列表（索引命中），否则 list_all SQL 预筛 ──
    file_id = filters.get("file_id")
    if file_id is not None:
        rows = get_questions_db().get_by_file_id(int(file_id))
    else:
        rows = get_questions_db().list_all(
            subject=filters.get("subject"),
            source_type=filters.get("source_type"),
            exam_year=filters.get("exam_year"),
            question_type=filters.get("question_type"),
        )

    # ── 内存精筛：等值条件（file_id 路径下 SQL 预筛未生效，统一补齐）
    #    + exam_month + 考区包含 + 知识点 id 集合 ──────────────────────
    eq_filters = [
        (row_key, filters[row_key])
        for row_key in (
            "subject", "source_type", "exam_year", "exam_month", "question_type",
        )
        if filters.get(row_key) is not None
    ]
    exam_region = filters.get("exam_region")
    hits: list[QuestionHit] = []
    for row in rows:
        if topic_ids is not None and row["id"] not in topic_ids:
            continue
        if any(row.get(key) != value for key, value in eq_filters):
            continue
        if (
            exam_region is not None
            and exam_region not in _json_list(row.get("exam_regions"))
        ):
            continue
        hits.append(_row_to_hit(row))

    limit = filters.get("limit")
    if limit is not None:
        hits = hits[: max(0, int(limit))]
    return hits
