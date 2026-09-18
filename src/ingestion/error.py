# src/ingestion/error.py
# 错题本写门面：管理 SQLite ``errors`` 行 + Chroma ``err_{id}`` document 的两态一致。
#   - ingest_error  —— 记错因（先查后写幂等；再次错同一题 → 更新 + 复位 resolved）
#   - update_error  —— 补录 / 修正错因 / 标记掌握（部分更新，不碰 resolved 除非显式传）
#   - delete_error  —— 移出错题本（不动 questions 主行）
# 设计契约见 docs/ingestion/error.md；embedding 文本格式见
# docs/store/vector/vector_store.md「错因 document 的 embedding 文本格式」。
#
# 约束：
#   - 本层无 LLM：error_summary 四键是上游（结构识别 Agent 过 error-organize Skill）
#     产出的成品，本层只做 dumps/loads + 中文分节文本转换。
#   - 先题后错：所有函数要求 question_id 已入库，门面显式校验给出清晰报错
#     （DB 层 FK 约束只是兜底）。
#   - JSON 只到本层为止：四键 JSON 存 SQLite，写向量前转中文分节文本，JSON 不进向量库。
#   - 空值规范化："" / {} / 四键全空 dict 一律转 None（SQL NULL）再落库——
#     count_pending 只认 IS NULL，「错因待补」判定依赖这一点。错因**不可清空**：
#     本门面的 "" = 未提供，与 question 门面的 "" = 清空**语义相反**，勿混。
#   - 删除顺序：先删向量后删 DB（跨存储无分布式事务，中断残留「数据还在、
#     可重建」优于孤儿向量）。

from __future__ import annotations

import json
from typing import Any

from trpc_agent_sdk.log import logger

from src.store.db.errors import get_errors_db
from src.store.db.question_topics import get_question_topics_db
from src.store.db.questions import get_questions_db
from src.store.vector import get_vector_store


# update_error 可变字段全集（返回的 updated_fields 按此顺序输出，照 question.py 模式）
_MUTABLE_FIELD_ORDER = ("user_reflection", "error_summary", "resolved")

# 四键 → 中文段前缀（顺序即 embedding 文本分节顺序，见 vector_store.md）
_SUMMARY_SECTIONS = (
    ("error_type", "错因类型："),
    ("cause", "错因："),
    ("knowledge_gap", "知识点缺口："),
    ("fix_suggestion", "改进建议："),
)


# ── 私有工具 ────────────────────────────────────────────────────────

def _summary_to_text(summary: dict | None) -> str:
    """四键 summary → 中文分节 embedding 文本（**不是 JSON**，无 LLM 纯函数）。

    只拼非空段、不留空行；段前缀 ``错因类型：`` / ``错因：`` / ``知识点缺口：``
    / ``改进建议：``。花括号、引号、英文键名都是语义噪声，不进向量库。

    Args:
        summary: 四键错因总结，``None`` / 空 dict / 四键全空 → 无嵌文本。

    Returns:
        分节文本；无可嵌内容时返回 ``""``（调用方据此判断"没有可嵌文本"）。
    """
    if not summary:
        return ""
    parts = [
        f"{prefix}{summary[key]}"
        for key, prefix in _SUMMARY_SECTIONS
        if summary.get(key)
    ]
    return "\n".join(parts)


def _normalize_summary(summary: dict | None) -> dict | None:
    """四键 summary 规范化：空 dict / 四键全空 → ``None``（视为未提供）。

    否则 ``{"cause": ""}`` 这类 truthy dict 会落成**非 NULL 脏行**：既不算
    「错因待补」（``count_pending`` 只认 IS NULL），又没有向量文本——状态悬空。

    Args:
        summary: 调用方传入的四键 dict（或 ``None``）。

    Returns:
        规范化后的 dict 或 ``None``。
    """
    if not summary or not any(summary.values()):
        return None
    return summary


def _loads_summary(raw: str | None) -> dict | None:
    """``error_summary`` JSON 列还原为 dict，容错：脏数据按 ``None`` 处理不抛穿。

    Args:
        raw: SQLite 里的 JSON 字符串（可能为 NULL / 非法 JSON / 非 dict 形状）。

    Returns:
        解析出的 dict；空值或解析失败返回 ``None``（记 warning）。
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


def _error_doc_id(error_id: int) -> str:
    """errors 行 → Chroma doc_id（两段式 ``err_{id}``，与 ``q_{id}`` 前缀区分）。"""
    return f"err_{error_id}"


def _upsert_error_doc(
    *,
    error_id: int,
    question_id: int,
    subject: str,
    summary: dict | None,
    first_seen: str,
) -> None:
    """写 / 重建 ``err_{id}`` document（错因变化的唯一向量入口）。

    无可嵌文本（summary 空或四键全空）时直接跳过——「错因待补」的行在向量库
    里根本不存在，不会污染召回。metadata 字段取舍见 vector_store.md：不存
    resolved（状态会变且不影响语义）/ 题面 / user_reflection / title。
    Chroma 拒绝空字符串/空列表 metadata，空值字段条件写入。
    """
    text = _summary_to_text(summary)
    if not text:
        return
    # Chroma 不接受空列表 metadata（ValueError: non-empty），空列表字段跳过
    meta: dict[str, Any] = {
        "doc_type": "error",
        "subject": subject,
        "question_id": question_id,
        "first_seen": first_seen,  # SQLite datetime 原值透传、不加工
    }
    error_type = (summary or {}).get("error_type")
    if error_type:
        meta["error_type"] = error_type
    topic_names = [
        r["topic_name"] for r in get_question_topics_db().get_by_question(question_id)
    ]
    if topic_names:
        meta["topic_tags"] = topic_names
    # doc_id 由 upsert 内部注入 metadata，不手写进 meta
    get_vector_store().upsert(_error_doc_id(error_id), text, meta)


# ═════════════════════════════════════════════════════════════════════
# 记错因（先查后写幂等）
# ═════════════════════════════════════════════════════════════════════

def ingest_error(
    *,
    question_id: int,
    user_reflection: str = "",
    error_summary: dict | None = None,
) -> dict:
    """记录一道错题的错因，幂等——该题不在错题本则插入，已存在则更新该行。

    「再次错同一题」走更新路径：``resolved`` 自动重置为 0（又错了说明还没掌握，
    与本次是否带来新错因无关）、``last_seen`` 刷新；本次带来的错因按**覆盖**
    语义写入。传入的空值（``""`` / ``None`` / ``{}``）**不覆盖**原有内容——
    否则用户只说一句「这题我又错了」就会把已记好的错因清空；但**新建**时空值
    照写，得到「错因待补」行（批量录入先建行、错因后补）。

    调用方无需先判断该题是否已在错题本（与 ``topics.create`` 冲突抛错的语义不同，
    这里"同一题又错一次/又补一句"是常态）。

    Args:
        question_id: 题目 ID（``questions.id``），必须已入库（先题后错）。
        user_reflection: 用户口述的原始错因描述，空串视为未提供。
        error_summary: LLM 结构化错因总结（四键 dict），空 dict 视为未提供；
            以 JSON 字符串落库，写向量时转中文分节文本。

    Returns:
        ``{"error_id": int, "created": bool}``，``created=False`` 表示更新了已有记录。

    Raises:
        ValueError: ``question_id`` 不存在（先题后错硬约束）。

    示例：
        result = ingest_error(
            question_id=42,
            user_reflection="把 b/a 当成离心率了",
            error_summary={"error_type": "知识盲区", "cause": "记混 e=c/a 与 b²=a²-c²",
                           "knowledge_gap": "椭圆离心率定义", "fix_suggestion": "复习焦点三角形模型"},
        )
        # result == {"error_id": 1, "created": True}
    """
    # ── 校验题目存在（顺带拿 subject 供 metadata 用） ─────────────────
    qrow = get_questions_db().get_by_id(question_id)
    if qrow is None:
        raise ValueError(f"question_id={question_id} 不存在，请先入库题目（先题后错）")

    # ── 空值规范化为 NULL（count_pending 只认 IS NULL；不覆盖由 None 语义实现） ──
    reflection = user_reflection or None
    summary = _normalize_summary(error_summary)
    summary_json = json.dumps(summary, ensure_ascii=False) if summary else None

    # ── 先查后写（一题一行，全项目无 UPSERT 先例） ────────────────────
    errors_db = get_errors_db()
    row = errors_db.get_by_question_id(question_id)
    if row is None:
        error_id = errors_db.insert(
            question_id=question_id,
            user_reflection=reflection,
            error_summary=summary_json,
        )
        created = True
    else:
        # 再次错同一题：恒复位 resolved + 刷 last_seen；未传的错因字段为 None → DB 不动
        errors_db.update(
            question_id,
            user_reflection=reflection,
            error_summary=summary_json,
            resolved=False,
        )
        error_id = row["id"]
        created = False

    # ── 向量层：本次携带错因**且与旧值不同**才 upsert（待补行不写向量；
    #    "再错一次但错因没变"不白花一次 embedding；row 是写前旧行） ──
    reembedded = False
    if summary_json and (row is None or summary_json != row["error_summary"]):
        fresh = errors_db.get_by_question_id(question_id)
        assert fresh is not None  # 上面 insert/update 刚写过，行必存在（取落库后的 first_seen）
        _upsert_error_doc(
            error_id=error_id,
            question_id=question_id,
            subject=qrow["subject"],
            summary=summary,
            first_seen=fresh["first_seen"],
        )
        reembedded = True

    logger.info(
        "ingest_error done: error_id=%d question_id=%d created=%s vector=%s",
        error_id, question_id, created, reembedded,
    )
    return {"error_id": error_id, "created": created}


# ═════════════════════════════════════════════════════════════════════
# 补录 / 修正错因 / 标记掌握
# ═════════════════════════════════════════════════════════════════════

def update_error(
    *,
    question_id: int,
    user_reflection: str | None = None,
    error_summary: dict | None = None,
    resolved: bool | None = None,
) -> dict:
    """补录 / 修正错因、标记掌握——部分更新。

    不传 / ``None`` / ``""`` / ``{}`` / 四键全空 dict = 不修改该字段。注意
    ``""`` 语义与 ``update_question`` **相反**：那边 ``""`` 是清空字段，这边错因
    **不可清空**、``""`` 视为未提供——调用方漏说一句话不应抹掉已记好的错因。
    **与 ``ingest_error`` 的区别**：本函数不自动碰 ``resolved``（那是"又错了"
    的语义）——补录/修正错因不影响掌握状态，``resolved`` 只在显式传入时改。

    向量层：**只有 ``error_summary`` 实际变化才重嵌**（只改 ``resolved`` /
    口述不动 document——掌握状态不影响语义）；此前处于待补（无 document）→
    补齐 summary 时首次建 document。全部字段与现值相同 → 跳过一切写入。

    Args:
        question_id: 题目 ID（``questions.id``），定位错题记录。
        user_reflection: 新的用户口述错因（全量替换）。
        error_summary: 新的结构化错因总结（四键 dict，全量替换）。
        resolved: 标记是否已掌握（``True`` 学会 / ``False`` 取消标记）。

    Returns:
        ``{"error_id": int, "updated_fields": list[str]}``，``updated_fields``
        只列本次实际发生变更的字段名（按签名顺序）。

    Raises:
        ValueError: ``question_id`` 无错题记录，或题目行已丢失（孤儿记录）。

    示例：
        # 隔天补录错因（待补行 → 建 document）
        result = update_error(question_id=42, error_summary={"cause": "符号看漏了"})
        # result == {"error_id": 1, "updated_fields": ["error_summary"]}
    """
    errors_db = get_errors_db()
    row = errors_db.get_by_question_id(question_id)
    if row is None:
        raise ValueError(f"question_id={question_id} 无错题记录，无法更新")
    error_id = row["id"]

    # subject 是向量 metadata 必需项——FK 保证正常情况下题目恒在，
    # 查不到说明题目被绕过级联删除（孤儿数据），显式报错而非写坏 metadata
    qrow = get_questions_db().get_by_id(question_id)
    if qrow is None:
        raise ValueError(
            f"question_id={question_id} 不存在（错题记录已失联，请 delete_error 后重新记录）"
        )

    # ── 逐项比对现值，只写实际变更的字段 ──────────────────────────────
    cur_summary = _loads_summary(row["error_summary"])
    new_summary = _normalize_summary(error_summary)

    changed: set[str] = set()
    db_kwargs: dict[str, Any] = {}
    # "" = 未提供（错因不可清空——与 update_question 的 ""=清空 相反，勿照搬
    # _text_changed）；右侧 or None 兼容历史脏数据里的空串行
    reflection = user_reflection or None
    if reflection is not None and reflection != (row["user_reflection"] or None):
        db_kwargs["user_reflection"] = reflection
        changed.add("user_reflection")
    if new_summary is not None and new_summary != cur_summary:
        db_kwargs["error_summary"] = json.dumps(new_summary, ensure_ascii=False)
        changed.add("error_summary")
    if resolved is not None and bool(row["resolved"]) != resolved:
        db_kwargs["resolved"] = resolved
        changed.add("resolved")

    if db_kwargs:
        errors_db.update(question_id, **db_kwargs)

    # ── 向量层：仅 error_summary 实际变化才重嵌（first_seen 不随更新变，用现值） ──
    if "error_summary" in changed:
        _upsert_error_doc(
            error_id=error_id,
            question_id=question_id,
            subject=qrow["subject"],
            summary=new_summary,
            first_seen=row["first_seen"],
        )

    updated_fields = [f for f in _MUTABLE_FIELD_ORDER if f in changed]
    logger.info(
        "update_error done: error_id=%d question_id=%d fields=%s",
        error_id, question_id, updated_fields,
    )
    return {"error_id": error_id, "updated_fields": updated_fields}


# ═════════════════════════════════════════════════════════════════════
# 移出错题本
# ═════════════════════════════════════════════════════════════════════

def delete_error(question_id: int) -> dict:
    """把题目移出错题本：删 ``errors`` 行 + ``err_{id}`` document，**不动 questions 主行**。

    与 ``delete_question`` 是两件事：一个是「这道题我不想再在错题本里看到」，
    一个是「这道题从题库删掉」。不可逆 → 调用前须经 Leader 回显确认。

    执行顺序：先删向量、后删 DB（跨存储无分布式事务，中断残留「数据还在、
    可重建」优于孤儿向量）。待补记录本无 document，删除不存在的 doc_id 幂等跳过。

    Args:
        question_id: 题目 ID（``questions.id``），定位错题记录。

    Returns:
        ``{"question_id": int, "deleted": bool}``。

    幂等：``question_id`` 无错题记录 → ``deleted=False``，不抛异常。
    """
    errors_db = get_errors_db()
    row = errors_db.get_by_question_id(question_id)
    if row is None:
        logger.info(
            "delete_error: question_id=%d 无错题记录，幂等返回 deleted=False", question_id
        )
        return {"question_id": question_id, "deleted": False}

    doc_id = _error_doc_id(row["id"])

    # 1. 先删 Chroma document（顺序依据见 docstring）
    get_vector_store().delete([doc_id])

    # 2. 再删 errors 行
    deleted = errors_db.delete_by_question_id(question_id)

    logger.info(
        "delete_error done: question_id=%d doc_id=%s deleted=%s",
        question_id, doc_id, deleted,
    )
    return {"question_id": question_id, "deleted": deleted}
