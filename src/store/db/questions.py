# src/store/db/questions.py
# 题目表 SQLite 数据访问层：管理 ``questions`` 表（题目内容 + 答案解析 + 元数据）。
#
# 与 Chroma 的关系：
#   SQLite 存储题目结构化元数据（source_type / subject / exam_year / question_type 等），
#   Chroma 存储题目 document 的向量嵌入（整篇题干+答案+解析+VLM 描述合并为一段）。
#   两者通过 ``doc_id`` 桥接（格式 ``q_{id}``）。
#
# 与其他表的关系：
#   - question_topics 表通过 ``question_id`` 关联知识点
#   - errors 表通过 ``question_id`` 关联错题记录
#   - exam_attempts 表通过 ``question_id`` 关联作答记录
#   - files 表通过 ``file_id`` 关联来源试卷/作业

from __future__ import annotations

import json
import uuid
from typing import Any

from trpc_agent_sdk.log import logger

from src.config import config
from src.store.db import SQLiteTableDB, row_to_dict


# ── Schema ──────────────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT UNIQUE NOT NULL,
    source_type     TEXT NOT NULL,
    subject         TEXT NOT NULL,
    file_id         INTEGER REFERENCES files(id),
    exam_regions    TEXT,
    exam_year       INTEGER,
    exam_month      INTEGER,
    question_number TEXT,
    question_type   TEXT NOT NULL,
    content_text    TEXT NOT NULL,
    answer_text     TEXT,
    analysis_text   TEXT,
    image_file_ids  TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);"""

_CREATE_INDEX_SOURCE = "CREATE INDEX IF NOT EXISTS idx_questions_source ON questions(source_type, file_id);"
_CREATE_INDEX_SUBJECT = "CREATE INDEX IF NOT EXISTS idx_questions_subject ON questions(subject);"
_CREATE_INDEX_EXAM = "CREATE INDEX IF NOT EXISTS idx_questions_exam ON questions(exam_year);"
_CREATE_INDEX_TYPE = "CREATE INDEX IF NOT EXISTS idx_questions_type ON questions(question_type);"


def _make_doc_id(question_id: int) -> str:
    """生成题目 doc_id。

    Chroma document 的 ``doc_id`` 格式：``q_{id}``（两段式）。
    幂等——同 question_id 恒生成同 doc_id，支持 upsert 去重。

    Args:
        question_id: ``questions`` 表的主键 ID。

    Returns:
        doc_id 字符串，如 ``"q_42"``。
    """
    return f"q_{question_id}"


# ── 数据访问类 ──────────────────────────────────────────────────────

class QuestionsDB(SQLiteTableDB):
    """题目表 SQLite 数据访问层。

    封装 ``questions`` 表的所有 CRUD 操作。摄入管线写入本表 + Chroma document，
    查询侧通过 SQLite 精确过滤（学科/年份/题型/考区）+ Chroma 语义检索联合召回。

    典型调用顺序：
    1. ``insert(source_type=..., subject=..., ...)`` → 插入题目记录（doc_id 自动生成）
    2. 摄入管线同步将 ``_make_doc_id(question_id)`` 生成的 doc_id 写入 Chroma
    3. 更新题目内容 → 先更新 SQLite 记录，再 upsert Chroma document（同 doc_id 覆盖）
    4. 删除题目 → 级联删 ``question_topics`` / ``errors`` / ``exam_attempts`` + Chroma document

    继承 ``SQLiteTableDB``（``src.store.db``）：共享连接 + 幂等 schema 初始化
    （``_connect`` / ``_init_schema`` / ``close`` 三件套由基类提供）。
    """

    table_name = "questions"
    ddl = (
        _CREATE_TABLE,
        _CREATE_INDEX_SOURCE,
        _CREATE_INDEX_SUBJECT,
        _CREATE_INDEX_EXAM,
        _CREATE_INDEX_TYPE,
    )

    # ── 插入 ────────────────────────────────────────────────────────

    def insert(
        self,
        *,
        source_type: str,
        subject: str,
        content_text: str,
        question_type: str,
        file_id: int | None = None,
        exam_regions: list[str] | None = None,
        exam_year: int | None = None,
        exam_month: int | None = None,
        question_number: str | None = None,
        answer_text: str | None = None,
        analysis_text: str | None = None,
        image_file_ids: list[int] | None = None,
    ) -> int:
        """插入一条题目记录。

        doc_id 自动生成（``q_{id}`` 两段式），调用方无需传入。入库后如需写入 Chroma，
        使用返回值 ``question_id`` 通过 ``_make_doc_id()`` 生成同 doc_id。

        Args:
            source_type: 题目来源类型，``"exam"`` / ``"special_topic"`` /
                         ``"homework"`` / ``"error_book"``。
            subject: 学科，如 ``"数学"`` / ``"物理"``。
            content_text: 题目文本（VLM 处理后含图形描述），必填。
            question_type: 题型，``"单选题"`` / ``"多选题"`` / ``"填空题"`` / ``"解答题"``。
            file_id: 所属试卷/作业的 ``files`` 表 ID，可选。
            exam_regions: 考区层级 JSON 数组，从小到大，如 ``["深圳", "广东", "全国一卷"]``。
            exam_year: 年份，如 ``2026``。
            exam_month: 月份，1-12。
            question_number: 题号，如 ``"第15题"`` / ``"选择题3"``。
            answer_text: 标准答案，可空（源资料缺失时 NULL）。
            analysis_text: 解析，可空（源资料缺失时 NULL，可后续 LLM 补）。
            image_file_ids: 题目图片的 ``files`` 表 id 列表，可空。

        Returns:
            新插入记录的 ``id``（自增主键）。
        """
        conn = self._connect()
        regions_json = json.dumps(exam_regions, ensure_ascii=False) if exam_regions else None
        image_ids_json = json.dumps(image_file_ids, ensure_ascii=False) if image_file_ids else None

        # 两段式 doc_id 依赖自增主键，需两步完成：
        #   1. INSERT 时用 UUID 占位（保证 NOT NULL 且全局唯一）
        #   2. 拿到 lastrowid 后 UPDATE 为正式的 q_{id}
        temp_doc_id = f"_tmp_{uuid.uuid4().hex}"
        cursor = conn.execute(
            """INSERT INTO questions
               (doc_id, source_type, subject, file_id, exam_regions, exam_year,
                exam_month, question_number, question_type, content_text,
                answer_text, analysis_text, image_file_ids)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (temp_doc_id, source_type, subject, file_id, regions_json, exam_year,
             exam_month, question_number, question_type, content_text,
             answer_text, analysis_text, image_ids_json),
        )
        conn.commit()
        question_id = cursor.lastrowid
        assert question_id is not None
        real_doc_id = _make_doc_id(question_id)
        conn.execute(
            "UPDATE questions SET doc_id = ? WHERE id = ?",
            (real_doc_id, question_id),
        )
        conn.commit()
        logger.info(
            "Question inserted: id=%d doc_id=%r type=%s subject=%s year=%s",
            question_id, real_doc_id, question_type, subject, exam_year,
        )
        return question_id

    # ── 单条查询 ────────────────────────────────────────────────────

    def get_by_id(self, question_id: int) -> dict[str, Any] | None:
        """按 ``id`` 查询题目记录。

        Args:
            question_id: 题目记录 ID。

        Returns:
            包含所有字段的字典（JSON 字段保持原始字符串），不存在时返回 ``None``。
        """
        row = self._connect().execute(
            "SELECT * FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        return row_to_dict(row) if row else None

    def get_by_doc_id(self, doc_id: str) -> dict[str, Any] | None:
        """按 ``doc_id`` 查询题目记录（Chrom ↔ SQLite 桥接）。

        Args:
            doc_id: Chroma document 的 doc_id，如 ``"q_42"``。

        Returns:
            记录字典，不存在时返回 ``None``。
        """
        row = self._connect().execute(
            "SELECT * FROM questions WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return row_to_dict(row) if row else None

    def get_by_file_id(self, file_id: int) -> list[dict[str, Any]]:
        """列出某试卷/作业下的所有题目。

        Args:
            file_id: ``files`` 表 ID。

        Returns:
            题目记录字典列表，按 ``id`` 升序排列。
        """
        rows = self._connect().execute(
            "SELECT * FROM questions WHERE file_id = ? ORDER BY id", (file_id,)
        ).fetchall()
        return [row_to_dict(r) for r in rows]

    # ── 列表查询 ────────────────────────────────────────────────────

    def list_all(
        self,
        subject: str | None = None,
        source_type: str | None = None,
        exam_year: int | None = None,
        question_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出题目，可按学科 / 来源 / 年份 / 题型组合过滤。

        Args:
            subject: 可选学科过滤，如 ``"数学"``。
            source_type: 可选来源类型过滤，如 ``"exam"``。
            exam_year: 可选年份过滤，如 ``2026``。
            question_type: 可选题型过滤，如 ``"解答题"``。

        Returns:
            题目记录字典列表，按 ``id`` 升序排列。
        """
        conn = self._connect()
        conditions: list[str] = []
        params: list[Any] = []

        if subject is not None:
            conditions.append("subject = ?")
            params.append(subject)
        if source_type is not None:
            conditions.append("source_type = ?")
            params.append(source_type)
        if exam_year is not None:
            conditions.append("exam_year = ?")
            params.append(exam_year)
        if question_type is not None:
            conditions.append("question_type = ?")
            params.append(question_type)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM questions {where} ORDER BY id", params
        ).fetchall()
        return [row_to_dict(r) for r in rows]

    def count(
        self,
        subject: str | None = None,
        source_type: str | None = None,
    ) -> int:
        """统计题目数量。

        Args:
            subject: 可选学科过滤。
            source_type: 可选来源类型过滤。

        Returns:
            符合条件的题目数。
        """
        conn = self._connect()
        conditions: list[str] = []
        params: list[Any] = []

        if subject is not None:
            conditions.append("subject = ?")
            params.append(subject)
        if source_type is not None:
            conditions.append("source_type = ?")
            params.append(source_type)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM questions {where}", params
        ).fetchone()
        return row["cnt"]

    # ── 更新 ────────────────────────────────────────────────────────

    def update(
        self,
        question_id: int,
        *,
        content_text: str | None = None,
        answer_text: str | None = None,
        analysis_text: str | None = None,
        question_number: str | None = None,
        question_type: str | None = None,
        exam_regions: list[str] | None = None,
        exam_year: int | None = None,
        exam_month: int | None = None,
        image_file_ids: list[int] | None = None,
    ) -> None:
        """更新题目记录的可变字段。

        调用方应同步更新 Chroma 侧同 doc_id 的 document（upsert）。

        Args:
            question_id: 题目记录 ID。
            content_text: 新题目文本（全量替换）。
            answer_text: 新标准答案（全量替换，传 ``""`` 可清空）。
            analysis_text: 新解析（全量替换，传 ``""`` 可清空）。
            question_number: 新题号。
            question_type: 新题型。
            exam_regions: 新考区层级列表，传 ``[]`` 可清空。
            exam_year: 新年份。
            exam_month: 新月份。
            image_file_ids: 新图片 files.id 列表，传 ``[]`` 可清空。

        Raises:
            ValueError: ``question_id`` 不存在。
        """
        conn = self._connect()

        # 动态构建 UPDATE SET 子句
        updates: list[str] = []
        params: list[Any] = []

        if content_text is not None:
            updates.append("content_text = ?")
            params.append(content_text)
        if answer_text is not None:
            updates.append("answer_text = ?")
            params.append(answer_text)
        if analysis_text is not None:
            updates.append("analysis_text = ?")
            params.append(analysis_text)
        if question_number is not None:
            updates.append("question_number = ?")
            params.append(question_number)
        if question_type is not None:
            updates.append("question_type = ?")
            params.append(question_type)
        if exam_regions is not None:
            updates.append("exam_regions = ?")
            params.append(json.dumps(exam_regions, ensure_ascii=False) if exam_regions else None)
        if exam_year is not None:
            updates.append("exam_year = ?")
            params.append(exam_year)
        if exam_month is not None:
            updates.append("exam_month = ?")
            params.append(exam_month)
        if image_file_ids is not None:
            updates.append("image_file_ids = ?")
            params.append(json.dumps(image_file_ids, ensure_ascii=False) if image_file_ids else None)

        if not updates:
            logger.debug("update: question_id=%d 无字段变更", question_id)
            return

        params.append(question_id)
        sql = f"UPDATE questions SET {', '.join(updates)} WHERE id = ?"
        cursor = conn.execute(sql, params)
        conn.commit()

        if cursor.rowcount == 0:
            raise ValueError(f"question_id={question_id} 不存在，无法更新")
        logger.info("Question updated: id=%d fields=%s", question_id, updates)

    # ── 删除 ────────────────────────────────────────────────────────

    def delete(self, question_id: int) -> bool:
        """删除题目记录。

        .. warning::
            调用方应先级联删除关联数据：
            - ``question_topics`` 中 ``question_id`` 关联的记录
            - ``errors`` 中 ``question_id`` 关联的记录
            - ``exam_attempts`` 中 ``question_id`` 关联的记录
            - Chroma 中 ``doc_id`` 对应的 document

        Args:
            question_id: 题目记录 ID。

        Returns:
            ``True`` = 删除成功，``False`` = ``question_id`` 不存在。
        """
        conn = self._connect()
        cursor = conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info("Question record deleted: id=%d", question_id)
            return True
        logger.warning("delete: question_id=%d 不存在", question_id)
        return False

    def __repr__(self) -> str:
        return "QuestionsDB()"


# ── Singleton factory ───────────────────────────────────────────────

_questions_db: QuestionsDB | None = None


def get_questions_db() -> QuestionsDB:
    """返回缓存的 QuestionsDB 单例。

    首次调用创建实例并缓存，后续调用返回同一实例。
    连接统一走全局共享 SQLite 连接，无需传参。

    Returns:
        QuestionsDB 实例。
    """
    global _questions_db
    if _questions_db is None:
        _questions_db = QuestionsDB()
    return _questions_db
