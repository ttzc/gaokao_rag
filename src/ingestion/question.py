# src/ingestion/question.py
# 原子化单题摄入：将一道题写入三层存储（文件层 + SQLite + Chroma）。
#
# 设计约束：
#   - `ingest_question` 只负责把「一道题」写进三层存储，返回 {"question_id": int, "doc_id": str}。
#   - 不接收任何 errors 参数、不感知 errors 表；错题记录由独立的 `ingest_error` 在题目入库后写。
#   - 知识点归位复用 `src.store.db.topics.get_topics_db()`。
#   - 科目名统一用 "数学"（与 Chroma metadata 过滤及全库示例一致）。
#
# 内部四层封装（顺序固定）：
#   1. 文件层（仅当 raw_file_path 给定）：查询 files 表获取 file_id，落盘处理后文本
#   2. DB 层：插入 questions 表，返回 question_id
#   3. 知识点归位：解析 topic_names，复用/创建知识点，写入 question_topics 表
#   4. 向量层：合并题干/答案/解析/VLM 描述生成 embedding，upsert 到 Chroma

from __future__ import annotations

from trpc_agent_sdk.log import logger

from src.config import config
from src.store.db.files import get_files_db
from src.store.db.questions import get_questions_db
from src.store.db.question_topics import get_question_topics_db
from src.store.db.topics import get_topics_db
from src.store.file_store import get_file_store
from src.store.vector import get_vector_store


def ingest_question(
    *,
    question_text: str,
    answer_text: str = "",
    analysis_text: str = "",
    subject: str = "数学",
    source_type: str = "exam",          # exam / homework / special_topic / reference
    question_type: str = "",
    raw_file_path: str | None = None,   # 关联源文件（files 表路径）；单题拍照无源文件时传 None
    exam_regions: list[str] | None = None,
    exam_year: int | None = None,
    exam_month: int | None = None,
    question_number: str | None = None,
    image_file_ids: list[int] | None = None,
    topic_names: list[str] | None = None,
    vlm_descriptions: list[str] | None = None,
) -> dict:
    """原子化单题摄入：将一道题写入三层存储。

    内部按固定顺序执行四层封装：
    1. 文件层（仅当 raw_file_path 给定）：查询 files 表获取 file_id，落盘处理后文本
    2. DB 层：插入 questions 表，返回 question_id
    3. 知识点归位：解析 topic_names，复用/创建知识点，写入 question_topics 表
    4. 向量层：合并题干/答案/解析/VLM 描述生成 embedding，upsert 到 Chroma

    Args:
        question_text: 题目文本（必填）。
        answer_text: 标准答案，可空。
        analysis_text: 解析，可空。
        subject: 学科，默认 "数学"。
        source_type: 来源类型，默认 "exam"（exam/homework/special_topic/reference）。
        question_type: 题型，如 "单选题" / "解答题"。
        raw_file_path: 关联源文件的 files 表路径，单题拍照无源文件时传 None。
        exam_regions: 考区层级列表，如 ["深圳", "广东", "全国一卷"]。
        exam_year: 年份，如 2026。
        exam_month: 月份，1-12。
        question_number: 题号，如 "第 15 题"。
        image_file_ids: 题目图片的 files.id 列表。
        topic_names: 知识点名字列表。
        vlm_descriptions: VLM 图形描述列表。

    Returns:
        {"question_id": int, "doc_id": str}。

    Raises:
        ValueError: 题目文本为空。
    """
    if not question_text:
        raise ValueError("question_text 不能为空")

    # ── 1. 文件层（仅当 raw_file_path 给定）──────────────────────────
    file_id: int | None = None
    if raw_file_path is not None:
        files_db = get_files_db()
        row = files_db.get_by_path(raw_file_path)
        file_id = row["id"] if row else None
        logger.debug(
            "File lookup: path=%r → file_id=%s",
            raw_file_path, file_id
        )

    # 落盘处理后文本（失败仅 warning，不阻断主流程）
    try:
        file_store = get_file_store()
        processed_name = f"q_{abs(hash(question_text))}.txt"
        file_store.save_processed(
            question_text.encode("utf-8"),
            category="text",
            name=processed_name,
        )
        logger.debug("Processed text saved: %s", processed_name)
    except Exception as exc:
        logger.warning("保存处理后文本失败：%s", exc)

    # ── 2. DB 层：插入 questions 表 ─────────────────────────────────
    questions_db = get_questions_db()
    qid = questions_db.insert(
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
    logger.info("Question inserted: id=%d", qid)

    # ── 3. 知识点归位（复用 store/db/topics.py）──────────────────────
    topics_db = get_topics_db()
    qt_db = get_question_topics_db()
    resolved: list[str] = []

    for name in (topic_names or []):
        hits = topics_db.search(name)
        if hits:
            # 优先精确匹配 name
            exact = [h for h in hits if h["name"] == name]
            resolved.append((exact[0] if exact else hits[0])["name"])
        else:
            # 未命中则创建新知识点
            topics_db.create(name)
            resolved.append(name)

    if resolved:
        qt_db.add_many(qid, resolved, primary_index=0)
        logger.info("Question topics bound: qid=%d topics=%s", qid, resolved)

    # ── 4. 向量层：upsert 到 Chroma ─────────────────────────────────
    parts = [question_text, answer_text, analysis_text]
    if vlm_descriptions:
        parts.append("\n".join(vlm_descriptions))
    embedding_text = "\n".join(p for p in parts if p)

    doc_id = f"q_{qid}"

    # 构建 metadata title：优先使用源文件 title，否则截断题干
    title: str
    if file_id is not None:
        file_row = get_files_db().get_by_id(file_id)
        title = (file_row["title"] if file_row and file_row.get("title") else question_text[:40])
    else:
        title = question_text[:40]

    metadata = {
        "doc_type": "question",
        "subject": subject,
        "source_type": source_type,
        "title": title,
        "topic_tags": resolved if resolved else None,  # Chroma 要求空列表传 None
        "exam_regions": exam_regions if exam_regions else None,  # Chroma 要求空列表传 None
        "exam_year": exam_year or 0,
        "question_type": question_type,
        "has_image": bool(image_file_ids),
    }

    vector_store = get_vector_store()
    vector_store.upsert(doc_id, embedding_text, metadata)
    logger.info("Vector upsert: doc_id=%r", doc_id)

    return {"question_id": qid, "doc_id": doc_id}
