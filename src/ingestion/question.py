# src/ingestion/question.py
# 题目摄入与维护门面：
#   - ingest_question   —— 原子化单题摄入，写入三层存储（FileStore + SQLite + Chroma）
#   - update_question   —— 改题：SQLite 可变字段 + 知识点关联全量替换 + 向量重建（文件层不动）
#   - delete_question   —— 删题：级联三处（Chroma document → question_topics → questions 主行）
# 设计契约见 docs/ingestion/question.md。
#
# 约束：
#   - 不接收 errors 参数、不感知 errors 表；错题记录由独立的 ingest_error 在题目入库后写（先题后错）。
#   - 知识点归位复用 src/store/db/topics.py（知识图谱注册表，不是 Chroma 检索组件）。
#   - 四层顺序固定：文件层（可选）→ DB 层 → 知识点归位 → 向量层。
#   - 删题级联 errors / exam_attempts 属阶段 2（DB 模块未落地）；返回契约 cascade 恒四键，
#     阶段 1 中 errors / exam_attempts 恒为 0。
#
# 各函数的调用示例见对应 docstring。

from __future__ import annotations

import json
from typing import Any

from trpc_agent_sdk.log import logger

from src.store.db.questions import get_questions_db
from src.store.db.question_topics import get_question_topics_db
from src.store.db.topics import get_topics_db
from src.store.db.files import get_files_db
from src.store.file_store import get_file_store
from src.store.vector import get_vector_store


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
        source_type: 来源类型，``"exam"`` / ``"homework"`` / ``"special_topic"`` /
            ``"reference"`` / ``"error_book"``（错题本来源，预留）。
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

    示例：
        result = ingest_question(
            question_text="已知函数 f(x) = x² + 2x - 3，求最小值。",
            answer_text="最小值为 -4。",
            analysis_text="配方法：f(x) = (x+1)² - 4。",
            topic_names=["二次函数", "配方法"],
        )
        # result == {"question_id": 1, "doc_id": "q_1"}
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
    resolved = _resolve_topic_names(topic_names or [])
    if resolved:
        get_question_topics_db().add_many(qid, resolved, primary_index=0)

    # ── 第四层：向量层 ─────────────────────────────────────────────
    parts = [question_text, answer_text, analysis_text]
    if vlm_descriptions:
        parts.append("\n".join(vlm_descriptions))
    embedding_text = "\n".join(p for p in parts if p)

    doc_id = f"q_{qid}"
    title = (
        get_files_db().get_by_id(file_id)["title"] # pyright: ignore[reportOptionalSubscript]
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

    # 四层全部写完（能走到这里说明 upsert 未抛异常），汇总一条便于运行期观察
    logger.info(
        "ingest_question done: question_id=%d doc_id=%s file_id=%s topics=%d vector=%s",
        qid,
        doc_id,
        file_id if file_id is not None else "-",
        len(resolved),
        "ok",
    )

    return {"question_id": qid, "doc_id": doc_id}


# ═════════════════════════════════════════════════════════════════════
# 题目维护：改 / 删（设计契约 docs/ingestion/question.md，2026-09-03 定案）
# ═════════════════════════════════════════════════════════════════════

# update_question 可变字段全集（返回的 updated_fields 按此顺序输出）
_MUTABLE_FIELD_ORDER = (
    "content_text",
    "answer_text",
    "analysis_text",
    "question_number",
    "question_type",
    "exam_regions",
    "exam_year",
    "exam_month",
    "image_file_ids",
    "topic_names",
)


def _resolve_topic_names(topic_names: list[str]) -> list[str]:
    """知识点名字归位：``topics`` 表 search 命中复用规范名，未命中则新建。

    ``ingest_question`` / ``update_question`` 共用同款归位逻辑（两处必须一致）。

    Args:
        topic_names: Agent 提取的知识点名字列表。

    Returns:
        归一后的规范名列表（顺序与输入一致）。
    """
    topics_db = get_topics_db()
    resolved: list[str] = []
    for name in topic_names:
        hits = topics_db.search(name)
        if hits:
            exact = [h for h in hits if h["name"] == name]
            resolved.append((exact[0] if exact else hits[0])["name"])
        else:
            topics_db.create(name)
            resolved.append(name)
    return resolved


def _load_vlm_descriptions(image_file_ids: list[int]) -> list[str]:
    """按 image_file_ids 回读 VLM 图形描述（改题重嵌内部调用，不暴露给调用方）。

    路径约定（docs/store/files/processed.md）：``image_file_ids`` → ``files.sha256``
    → ``processed/vlm_desc/{sha256}.json``。门面不向调用方暴露 ``vlm_descriptions``
    参数——否则调用方漏传就静默丢掉图形描述、向量质量退化。
    单条读取/解析失败仅 warning 跳过、不阻断重嵌（与 ingest_question 中
    save_processed 的降级策略对称）。

    Args:
        image_file_ids: 题目图片的 ``files`` 表 id 列表。

    Returns:
        成功读到的图形描述文本列表（缺失条目跳过）。
    """
    if not image_file_ids:
        return []
    files_db = get_files_db()
    # 经 FileStore 单例解析 vlm_desc 目录（data_dir 相对/绝对两种模式下都正确）
    desc_dir = get_file_store()._subdirs["processed_vlm_desc"]
    descs: list[str] = []
    for fid in image_file_ids:
        try:
            frow = files_db.get_by_id(fid)
            if frow is None:
                logger.warning(
                    "update_question: image_file_id=%d 未在 files 表注册，跳过 VLM 描述", fid
                )
                continue
            path = desc_dir / f"{frow['sha256']}.json"
            if not path.exists():
                logger.warning(
                    "update_question: VLM 描述缓存缺失 %s，跳过", path.name
                )
                continue
            data = json.loads(path.read_text("utf-8"))
            # 写入方（ingest_image 管线）尚未落地，JSON 形状未定案——
            # 宽容解析：str / {"description": ...} / list[str] 三种形态都接受
            if isinstance(data, str):
                text: Any = data
            elif isinstance(data, dict):
                text = data.get("description") or data.get("desc") or data.get("text")
            elif isinstance(data, list):
                text = "\n".join(str(x) for x in data)
            else:
                text = None
            if isinstance(text, str) and text:
                descs.append(text)
            else:
                logger.warning(
                    "update_question: VLM 描述缓存 %s 无可用描述字段，跳过", path.name
                )
        except Exception:
            logger.warning(
                "update_question: 读取 VLM 描述失败 file_id=%d，跳过", fid, exc_info=True
            )
    return descs


def update_question(
    *,
    question_id: int,
    content_text: str | None = None,
    answer_text: str | None = None,
    analysis_text: str | None = None,
    question_number: str | None = None,
    question_type: str | None = None,
    exam_regions: list[str] | None = None,
    exam_year: int | None = None,
    exam_month: int | None = None,
    image_file_ids: list[int] | None = None,
    topic_names: list[str] | None = None,
) -> dict:
    """修改一道题：SQLite 可变字段 + 知识点关联全量替换 + Chroma 文档重建（文件层不动）。

    参数语义与 store 层 ``QuestionsDB.update()`` 对齐：``None`` = 不修改该字段；
    ``""`` / ``[]`` = 清空该字段。不可变字段（``id`` / ``doc_id`` / ``source_type``
    / ``subject`` / ``file_id`` / ``created_at``）不提供修改入口——改学科或来源等于
    换一道题，应走「删除 + 重新入库」。

    四层顺序（与 ``ingest_question`` 对称，任一环节失败直接抛出、不吞异常）：
    校验 → DB 层 → 知识点层（``topic_names`` 非 ``None`` 时全量替换）→ 向量层。
    指定字段全部与现值相同（无实际变更）时跳过全部写入，``updated_fields`` 返回空列表。

    实现偏差说明（vs 设计意图「只改元数据不必重嵌」，docs/ingestion/question.md）：
    ``VectorStore.upsert()`` 是「先删后加 + langchain ``add_documents``」，每次 add
    强制重算 embedding，框架未暴露 metadata-only 通道（chromadb 底层
    ``collection.update()`` 存在，但伸手 ``vectorstore._collection`` 属 hack 框架
    内部）。故本实现对一切变更统一走重嵌 upsert，接受元数据改动多付一次 embedding
    调用，换取实现一致、不破坏分层。

    Args:
        question_id: 题目 ID（``questions.id``），必填。
        content_text: 新题面（全量替换）。
        answer_text: 新答案，``""`` 清空。
        analysis_text: 新解析，``""`` 清空。
        question_number: 新题号，``""`` 清空。
        question_type: 新题型，``""`` 清空。
        exam_regions: 新考区层级列表，``[]`` 清空。
        exam_year: 新年份（``None`` = 不动，无法清空）。
        exam_month: 新月份（``None`` = 不动，无法清空）。
        image_file_ids: 新图片 ``files`` id 列表，``[]`` 清空；变更后 ``has_image``
            快照与 VLM 描述回读随之重建。
        topic_names: 新知识点名字列表，全量替换语义：``None`` = 关联不动，
            ``[]`` = 清空关联，非空 = 先清空旧关联再按归位逻辑重建。

    Returns:
        ``{"question_id": int, "doc_id": str, "updated_fields": list[str]}``，
        ``updated_fields`` 只列本次实际发生变更的字段名（按签名顺序）。

    Raises:
        ValueError: ``question_id`` 不存在（先校验，避免 DB 写完了才发现改了个空气）。

    示例：
        # 改答案 + 知识点重标（全量替换），其余字段不动
        result = update_question(
            question_id=1,
            answer_text="最小值为 -4。",
            topic_names=["二次函数", "配方法", "最值"],
        )
        # result == {"question_id": 1, "doc_id": "q_1",
        #            "updated_fields": ["answer_text", "topic_names"]}
    """
    # ── 第 1 步：校验 ──────────────────────────────────────────────
    questions_db = get_questions_db()
    row = questions_db.get_by_id(question_id)
    if row is None:
        raise ValueError(f"question_id={question_id} 不存在，无法修改")

    def _json_list(raw: str | None) -> list:
        return json.loads(raw) if raw else []

    cur_regions = _json_list(row["exam_regions"])
    cur_images = _json_list(row["image_file_ids"])

    def _text_changed(new: str | None, cur: str | None) -> bool:
        """文本字段差异：None = 不动；None 与 "" 等价（入库 NULL 化 / 改后 "" 化互不算变更）。"""
        return new is not None and (new or None) != (cur or None)

    # ── 第 2 步：DB 层——逐项对照现值，只写实际变更的字段 ─────────────────
    changed: set[str] = set()
    db_kwargs: dict[str, Any] = {}
    for field, value in (
        ("content_text", content_text),
        ("answer_text", answer_text),
        ("analysis_text", analysis_text),
        ("question_number", question_number),
        ("question_type", question_type),
    ):
        if _text_changed(value, row[field]):
            db_kwargs[field] = value
            changed.add(field)
    for field, value in (("exam_year", exam_year), ("exam_month", exam_month)):
        if value is not None and value != row[field]:
            db_kwargs[field] = value
            changed.add(field)
    if exam_regions is not None and list(exam_regions) != cur_regions:
        db_kwargs["exam_regions"] = exam_regions
        changed.add("exam_regions")
    if image_file_ids is not None and list(image_file_ids) != cur_images:
        db_kwargs["image_file_ids"] = image_file_ids
        changed.add("image_file_ids")

    if db_kwargs:
        questions_db.update(question_id, **db_kwargs)

    # 合并出更新后的有效值（未传字段沿用现值），供知识点 / 向量层重建用
    eff_content = content_text if content_text is not None else row["content_text"]
    eff_answer = answer_text if answer_text is not None else row["answer_text"]
    eff_analysis = analysis_text if analysis_text is not None else row["analysis_text"]
    eff_question_type = question_type if question_type is not None else row["question_type"]
    eff_exam_year = exam_year if exam_year is not None else row["exam_year"]
    eff_regions = exam_regions if exam_regions is not None else cur_regions
    eff_images = image_file_ids if image_file_ids is not None else cur_images

    # ── 第 3 步：知识点层——topic_names 非 None 时全量替换 ─────────────────
    qt_db = get_question_topics_db()
    cur_topics = [r["topic_name"] for r in qt_db.get_by_question(question_id)]
    if topic_names is not None and list(topic_names) != cur_topics:
        qt_db.remove_by_question(question_id)
        resolved = _resolve_topic_names(topic_names)
        if resolved:
            qt_db.add_many(question_id, resolved, primary_index=0)
        changed.add("topic_names")
    else:
        resolved = cur_topics

    # ── 第 4 步：向量层——重建 embedding_text + metadata，upsert 同 doc_id ──
    # 无实际变更则整体跳过（不空耗 embedding 调用）；metadata-only 变更也走重嵌，
    # 原因见 docstring「实现偏差说明」。
    if changed:
        doc_id = row["doc_id"]
        parts = [eff_content, eff_answer, eff_analysis]
        vlm_descs = _load_vlm_descriptions(list(eff_images))
        if vlm_descs:
            parts.append("\n".join(vlm_descs))
        embedding_text = "\n".join(p for p in parts if p)

        # title 规则与 ingest_question 完全一致（files.title 优先，否则题面前 40 字），
        # 避免改完题标题跳变
        file_row = get_files_db().get_by_id(row["file_id"]) if row["file_id"] else None
        title = (file_row["title"] if file_row else None) or eff_content[:40]
        # Chroma 不接受空列表 metadata（ValueError: non-empty），空列表字段跳过；
        # 全量重建 = 照抄 ingest_question 的字段集，不可变字段直接取 row 现值
        meta: dict[str, Any] = {
            "doc_type": "question",
            "subject": row["subject"],
            "source_type": row["source_type"],
            "title": title,
            "exam_year": eff_exam_year or 0,
            "question_type": eff_question_type,
            "has_image": bool(eff_images),  # 快照随 image_file_ids 变，SQLite 侧不存
        }
        if resolved:
            meta["topic_tags"] = resolved
        if eff_regions:
            meta["exam_regions"] = eff_regions
        get_vector_store().upsert(doc_id, embedding_text, meta)

        logger.info(
            "update_question done: question_id=%d doc_id=%s fields=%s",
            question_id, doc_id, sorted(changed),
        )

    return {
        "question_id": question_id,
        "doc_id": row["doc_id"],
        "updated_fields": [f for f in _MUTABLE_FIELD_ORDER if f in changed],
    }


def delete_question(*, question_id: int) -> dict:
    """删除一道题：级联清理 Chroma document + question_topics 关联 + questions 主行。

    执行顺序不可反（docs/ingestion/question.md「为什么先删 Chroma」）：跨 SQLite /
    Chroma 没有分布式事务，先删 Chroma——中断残留的是「向量没了、数据还在」，
    重跑向量化即可恢复；反序则残留孤儿向量（检索能命中、回查 SQLite 拿不到内容），
    是不可恢复的脏数据。

    级联分两阶段：阶段 1（本实现）级联三处；errors / exam_attempts 的 DB 模块未
    落地，返回契约 ``cascade`` 恒含四键（阶段 2 只让计数变非零、不新增键），
    阶段 1 中 ``errors`` / ``exam_attempts`` 恒为 0。

    边界：不动 ``files`` 表、不动 ``data/files/raw/``（源数据不可再生），也不清理
    ``processed/``（中间产物可重建，随后续清理策略统一走）。

    Args:
        question_id: 题目 ID（``questions.id``），必填。

    Returns:
        ``{"question_id": int, "doc_id": str, "deleted": bool,
        "cascade": {"question_topics": int, "errors": 0, "exam_attempts": 0,
        "vector": bool}}``。

    幂等：``question_id`` 不存在 → ``deleted=False`` + cascade 各键 0/False，
    不抛异常（删一个不存在的题不是错误）。

    示例：
        result = delete_question(question_id=1)
        # result == {"question_id": 1, "doc_id": "q_1", "deleted": True,
        #            "cascade": {"question_topics": 2, "errors": 0,
        #                        "exam_attempts": 0, "vector": True}}
    """
    questions_db = get_questions_db()

    # 1. 校验存在 + 取 doc_id
    row = questions_db.get_by_id(question_id)
    if row is None:
        logger.info(
            "delete_question: question_id=%d 不存在，幂等返回 deleted=False", question_id
        )
        return {
            "question_id": question_id,
            "doc_id": f"q_{question_id}",
            "deleted": False,
            "cascade": {
                "question_topics": 0,
                "errors": 0,  # 阶段 1 恒 0（模块未落地），契约形状固定
                "exam_attempts": 0,  # 同上
                "vector": False,
            },
        }

    doc_id = row["doc_id"]

    # 2. 先删 Chroma document（顺序依据见 docstring）
    get_vector_store().delete([doc_id])

    # 3. 再删知识点关联，取删除条数填 cascade
    removed_topics = get_question_topics_db().remove_by_question(question_id)

    # 4. 最后删 questions 主行
    deleted = questions_db.delete(question_id)

    logger.info(
        "delete_question done: question_id=%d doc_id=%s topics=%d deleted=%s",
        question_id, doc_id, removed_topics, deleted,
    )

    # 5. 组装返回（cascade 恒四键；raw / processed / files 不动，见 docstring 边界）
    return {
        "question_id": question_id,
        "doc_id": doc_id,
        "deleted": deleted,
        "cascade": {
            "question_topics": removed_topics,
            "errors": 0,
            "exam_attempts": 0,
            "vector": True,
        },
    }
