# src/retrieval/error.py
# 读门面·错题本：把 ``errors`` 表聚合成错题画像（统计 + 明细）。
# 本模块只读，不触碰任何 store 写方法、不碰 Chroma（见 docs/retrieval/error.md）。
#
# 本期范围（2026-09-18 定）：只落 get_error_stats / get_error_details。
#   get_weak_topics、知识点分布 by_topic、时间趋势 by_date 属周报口径
#   （accuracy 数据源未定、需 join question_topics），随周报设计一并实现，
#   本期不预留半成品字段。
#
# 依赖 src.store.db 查询原语（errors），门面只做组合与业务封装，
# 不写裸 SQL——缺统计原语在 ErrorsDB 加方法（count / count_resolved / count_pending）。

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from trpc_agent_sdk.log import logger

from src.store.db.errors import get_errors_db


# ═══════════════════════════════════════════════════════════════════════════════
# 业务语义对象（非裸 Row）
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ErrorStats:
    """错题本汇总统计（纯 SQLite；时间窗统计属周报口径，本对象无窗口字段）。

    Attributes:
        total: 错题总数。
        resolved: 已掌握数（``resolved=1``）。
        resolve_rate: 掌握率 = resolved / total；``total=0`` 时为 ``0.0``（除零安全）。
        pending_count: 错因待补数（``user_reflection`` 与 ``error_summary`` 均为
            NULL，判定见 docs/store/db/errors.md）——仍计入 total，但不参与错因分析。
    """

    total: int
    resolved: int
    resolve_rate: float
    pending_count: int


@dataclass
class ErrorDetail:
    """单条错题明细（供输出整理 Agent 拼「这题为什么错」）。

    Attributes:
        error_id: ``errors`` 表主键。
        question_id: 关联 ``questions.id``（回查题面/答案的入口）。
        user_reflection: 用户口述原文（照存），未提供为 ``None``。
        error_summary: **已解析**的四键 dict
            （``{error_type, cause, knowledge_gap, fix_suggestion}``）；
            NULL / 非法 JSON / 非 dict → ``None``（容错不抛）。
        pending: 错因待补（两列均 NULL）——True 时 Leader 应在回复末尾带补充邀请。
        resolved: 是否已掌握（DB int 0/1 → ``bool``）。
        first_seen: 首次记入时间（SQLite datetime 原值）。
        last_seen: 最后一次更新时间（SQLite datetime 原值）。
    """

    error_id: int
    question_id: int
    user_reflection: str | None
    error_summary: dict | None
    pending: bool
    resolved: bool
    first_seen: str
    last_seen: str


# ═══════════════════════════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════════════════════════


def _loads_summary(raw: str | None) -> dict | None:
    """解析 ``error_summary`` JSON 列，容错：空值 / 非法 JSON / 非 dict → ``None``。

    解析失败记 warning 不抛穿（脏数据不该让查询整挂）。语义与写门面
    ``src/ingestion/error.py`` 的同名函数保持一致——两门面互不 import
    （retrieval 禁 import ingestion），逻辑各一份、靠测试对齐
    （``question.py`` 的 ``_json_list`` 同款模式）。
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("error_summary JSON 解析失败，按无总结处理: %r", raw[:80])
        return None
    if not isinstance(data, dict):
        logger.warning("error_summary 非 JSON 对象（%s），按无总结处理", type(data).__name__)
        return None
    return data


def _row_to_detail(row: dict[str, Any]) -> ErrorDetail:
    """errors 记录字典 → ErrorDetail（JSON 解析 + pending 判定 + int→bool）。

    ``pending`` 直接按两列 ``is None`` 判——与 DB ``count_pending()``（IS NULL）
    口径严格一致，统计数与明细标记不会出现两套规则。
    """
    summary = _loads_summary(row["error_summary"])
    return ErrorDetail(
        error_id=row["id"],
        question_id=row["question_id"],
        user_reflection=row["user_reflection"],
        error_summary=summary,
        pending=row["user_reflection"] is None and row["error_summary"] is None,
        resolved=bool(row["resolved"]),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# get_error_stats — 错题统计
# ═══════════════════════════════════════════════════════════════════════════════


def get_error_stats() -> ErrorStats:
    """错题本汇总统计：纯 SQLite 三个计数原语，无向量检索、无时间窗参数。

    内部流程（docs/retrieval/error.md，本期基础口径）：
      1. ``ErrorsDB.count()`` → 总错题数
      2. ``ErrorsDB.count_resolved()`` → 已掌握数，掌握率由门面相除（除零安全）
      3. ``ErrorsDB.count_pending()`` → 错因待补数（单独给出：仍算错题但不参与错因分析）

    知识点分布 / 时间趋势 / 薄弱知识点（get_weak_topics）属周报口径，本期不做。

    Returns:
        ErrorStats——空库也是合法返回（total=0、resolve_rate=0.0），不抛异常。
    """
    errors_db = get_errors_db()
    total = errors_db.count()
    resolved = errors_db.count_resolved()
    stats = ErrorStats(
        total=total,
        resolved=resolved,
        resolve_rate=resolved / total if total else 0.0,
        pending_count=errors_db.count_pending(),
    )
    logger.debug(
        "get_error_stats: total=%d resolved=%d rate=%.3f pending=%d",
        stats.total, stats.resolved, stats.resolve_rate, stats.pending_count,
    )
    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# get_error_details — 错题明细
# ═══════════════════════════════════════════════════════════════════════════════


def get_error_details(question_id: int) -> list[ErrorDetail]:
    """取该题的错题明细（一题一行，实际恒单条；list 签名为将来留口）。

    无错题记录 → 空列表——**查询无结果不是错误**（题目不存在同样空列表，
    存在性校验是写门面职责，读门面只负责"有就给、没有就空"）。

    Args:
        question_id: ``questions.id``。

    Returns:
        ErrorDetail 列表（0 或 1 条）：``error_summary`` 已解析为 dict
        （脏 JSON 容错为 None）、``pending`` / ``resolved`` 已转 bool。
    """
    row = get_errors_db().get_by_question_id(question_id)
    if row is None:
        return []
    return [_row_to_detail(row)]
