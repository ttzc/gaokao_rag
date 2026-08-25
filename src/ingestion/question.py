# src/ingestion/question.py
# 原子化单题摄入：将一道题目写入三层存储（FileStore + SQLite + Chroma），返回 {"question_id", "doc_id"}。
#
# 设计约束：
#   - 不接收 errors 参数、不感知 errors 表；错题记录由独立的 ingest_error 在题目入库后写（先题后错）。
#   - 知识点归位复用 src/store/db/topics.py（知识图谱注册表，不是 Chroma 检索组件）。
#   - 四层顺序固定：文件层（可选）→ DB 层 → 知识点归位 → 向量层。
#
# 调用示例：
#     result = ingest_question(
#         question_text="已知函数 f(x) = x² + 2x - 3，求最小值。",
#         answer_text="最小值为 -4。",
#         analysis_text="配方法：f(x) = (x+1)² - 4。",
#         topic_names=["二次函数", "配方法"],
#     )
#     # result == {"question_id": 1, "doc_id": "q_1"}

from __future__ import annotations

import logging
from typing import Any

from src.store.db.questions import get_questions_db
from src.store.db.question_topics import get_question_topics_db
from src.store.db.topics import get_topics_db
from src.store.db.files import get_files_db
from src.store.file_store import get_file_store
from src.store.vector import get_vector_store

logger = logging.getLogger(__name__)


def ingest_question(
    *,
    question_text: str,
    answer_text: str = "",
    analysis_text: str = "",
    subject: str = "数学",
    source_type: str = "exam",
    question_type: str = "",
    raw_file_path: str | None = None,
    exam_regions: list[str] | None = None,
    exam_year: int | None = None,
    exam_month: int | None = None,
    question_number: str | None = None,
    image_file_ids: list[int] | None = None,
    topic_names: list[str] | None = None,
    vlm_descriptions: list[str] | None = None,
) -> dict:
    """将单道题目原子化写入三层存储。

    四层顺序固定，任何一层的失败都会阻断后续层（不静默吞异常）。
    文件层仅保存处理后文本，保存失败仅 warning 不阻断主流程。

    Args:
        question_text: 题目文本（VLM 处理后含图形描述），必填。
        answer_text: 标准答案，空字符串视为无答案。
        analysis_text: 解析，空字符串视为无解析。
        subject: 学科，默认 "数学"。
        source_type: 来源类型，``"exam"`` / ``"homework"`` / ``"special_topic"`` / ``"reference"``。
        question_type: 题型，如 ``"单选题"`` / ``"填空题"`` / ``"解答题"``。
        raw_file_path: 关联源文件的 ``files`` 表路径；单题拍照无源文件时传 ``None``。
        exam_regions: 考区层级列表，从小到大，如 ``["深圳", "广东", "全国一卷"]``。
        exam_year: 年份，如 ``2026``。
        exam_month: 月份，1-12。
        question_number: 题号，如 ``"第15题"``。
        image_file_ids: 题目图片的 ``files`` 表 id 列表。
        topic_names: 知识点名字列表（自动归位：已存在则复用，不存在则创建）。
        vlm_descriptions: VLM 图形描述列表，追加到 embedding 文本尾部。

    Returns:
        ``{"question_id": int, "doc_id": str}``

    Raises:
        任一层失败均直接抛出异常（不吞异常、不 fallback）。
    """
    # ── 第一层：文件层（可选） ───────────────────────────────────────
    file_id = None
    if raw_file_path is not None:
        files_db = get_files_db()
        row = files_db.get_by_path(raw_file_path)
        file_id = row["id"] if row else None

        # 落盘处理后文本（失败仅 warning，不阻断主流程）
        try:
            get_file_store().save_processed(
                question_text.encode("utf-8"),
                category="text",
                name=f"q_{abs(hash(question_text))}.txt",
            )
        except Exception:
            logger.warning(
                "ingest_question: save_processed failed for question_text hash=%d",
                hash(question_text),
                exc_info=True,
            )

    # ── 第二层：DB 层 ───────────────────────────────────────────────
    qid = get_questions_db().insert(
        source_type=source_type,
        subject=subject,
        content_text=question_text,
        question_type=question_type,
        file_id=file_id,
        exam_regions=exam_regions,
        exam_year=exam_year,
        exam_month=exam_month,
        question_number=question_number,
        answer_text=answer_text or None,
        analysis_text=analysis_text or None,
        image_file_ids=image_file_ids,
    )

    # ── 第三层：知识点归位 ─────────────────────────────────────────
    topics_db = get_topics_db()
    qt_db = get_question_topics_db()
    resolved: list[str] = []

    for name in (topic_names or []):
        hits = topics_db.search(name)
        if hits:
            exact = [h for h in hits if h["name"] == name]
            resolved.append((exact[0] if exact else hits[0])["name"])
        else:
            topics_db.create(name)
            resolved.append(name)

    if resolved:
        qt_db.add_many(qid, resolved, primary_index=0)

    # ── 第四层：向量层 ─────────────────────────────────────────────
    parts = [question_text, answer_text, analysis_text]
    if vlm_descriptions:
        parts.append("\n".join(vlm_descriptions))
    embedding_text = "\n".join(p for p in parts if p)

    doc_id = f"q_{qid}"
    title = (
        get_files_db().get_by_id(file_id)["title"]
        if file_id
        else None
    ) or question_text[:40]
    # Chroma 不接受空列表 metadata（ValueError: non-empty），空列表字段跳过
    meta: dict[str, Any] = {
        "doc_type": "question",
        "subject": subject,
        "source_type": source_type,
        "title": title,
        "exam_year": exam_year or 0,
        "question_type": question_type,
        "has_image": bool(image_file_ids),
    }
    if resolved:
        meta["topic_tags"] = resolved
    if exam_regions:
        meta["exam_regions"] = exam_regions
    get_vector_store().upsert(doc_id, embedding_text, meta)

    return {"question_id": qid, "doc_id": doc_id}
