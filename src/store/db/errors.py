# src/store/db/errors.py
# 错题表 SQLite 数据访问层：管理 ``errors`` 表（错题记录 + 错因）。
#
# 与其他表的关系：
#   - ``question_id`` → ``questions.id``（外键，必填——"先题后错"铁律）
#   - 一题一行：``idx_errors_question`` UNIQUE 索引保证同一题在错题本只有一条记录，
#     再次错同一题 = 更新这条记录，不新增行（见 docs/store/db/errors.md）
#
# 与 Chroma 的关系：
#   错因 document（``err_{id}``）的向量化归门面层，本层只管 SQLite；
#   「错因待补」记录不写向量。
#
# error_summary 的 JSON 约定：
#   存四键 {error_type, cause, knowledge_gap, fix_suggestion} 的 JSON 字符串，
#   **DB 层不解析、不序列化**——dumps/loads 归门面层，本层只当普通 TEXT 存取。
#
# 读取形态说明（SQLite 无 BOOLEAN 类型）：
#   ``resolved`` 读出恒为 int ``0/1``（DEFAULT 0 与 bind True 均落库为整数）。
#
# 分层铁律：本模块属 store 层，禁止 import src.ingestion / src.agent / src.retrieval。

from __future__ import annotations

import sqlite3
from typing import Any

from trpc_agent_sdk.log import logger

from src.store.db import SQLiteTableDB, row_to_dict


# ── Schema ──────────────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id     INTEGER NOT NULL REFERENCES questions(id),
    user_reflection TEXT,                            -- 用户口述的原始错因描述（自由文本）
    error_summary   TEXT,                            -- LLM 结构化错因总结（JSON 字符串，DB 层不解析）
    first_seen      TEXT DEFAULT (datetime('now')),  -- 首次记入错题本
    last_seen       TEXT DEFAULT (datetime('now')),  -- 最后一次更新（补录 / 修正错因）
    resolved        BOOLEAN DEFAULT 0               -- 是否已掌握（读出为 int 0/1）
);"""

# 一题一行：同一道题在错题本里只有一条记录
_CREATE_INDEX_QUESTION = "CREATE UNIQUE INDEX IF NOT EXISTS idx_errors_question ON errors(question_id);"


# ── 数据访问类 ──────────────────────────────────────────────────────

class ErrorsDB(SQLiteTableDB):
    """错题表 SQLite 数据访问层。

    封装 ``errors`` 表的所有 CRUD 操作。错题经 ``question_id`` 关联到具体题目
    （题目必须先入库——"先题后错"铁律），回答"这题为什么错"（题目粒度）。

    典型调用顺序（幂等由门面层「先查后写」实现，本层只提供原语）：
    1. ``get_by_question_id(question_id)`` → 判存
    2. 未命中 → ``insert(question_id=..., ...)`` → 返回 error_id
    3. 已命中 → ``update(question_id, ...)`` → 补录/修正错因（last_seen 自动刷新）
    4. 删错题 → ``delete_by_question_id(question_id)``

    继承 ``SQLiteTableDB``（``src.store.db``）：共享连接 + 幂等 schema 初始化
    （``_connect`` / ``_init_schema`` / ``close`` 三件套由基类提供）。
    """

    table_name = "errors"
    ddl = (_CREATE_TABLE, _CREATE_INDEX_QUESTION)

    # ── 插入 ────────────────────────────────────────────────────────

    def insert(
        self,
        *,
        question_id: int,
        user_reflection: str | None = None,
        error_summary: str | None = None,
    ) -> int:
        """插入一条错题记录（一题一行）。

        两列错因均可空：均不传 = 「错因待补」状态（批量录入先建行、错因后补）。
        ``error_summary`` 按普通 TEXT 原样存取，本层不解析 JSON。

        Args:
            question_id: 错题对应的题目 ID（``questions.id``，必须已存在）。
            user_reflection: 用户口述的原始错因描述，可选。
            error_summary: LLM 结构化错因总结（JSON 字符串），可选。

        Returns:
            新插入记录的 ``id``（自增主键）。

        Raises:
            ValueError: ``question_id`` 已有错题记录（UNIQUE 冲突，一题一行）——
                        属可预期业务冲突，门面层应改走 ``update()``。
            sqlite3.IntegrityError: ``question_id`` 指向的题目不存在（FK 约束）——
                        违反"先题后错"铁律，属调用方 bug，原样抛出。
        """
        conn = self._connect()
        try:
            cursor = conn.execute(
                """INSERT INTO errors (question_id, user_reflection, error_summary)
                   VALUES (?, ?, ?)""",
                (question_id, user_reflection, error_summary),
            )
            conn.commit()
        except sqlite3.IntegrityError as e:
            # UNIQUE（重复错题）与 FK（题目不存在）都走 IntegrityError，分流：
            # UNIQUE → 门面层可预期，转 ValueError；FK → 调用方 bug，原样抛出
            if "UNIQUE" not in str(e):
                raise
            raise ValueError(
                f"question_id={question_id} 已有错题记录，违反一题一行 UNIQUE 约束。"
                " 如需补录/修正错因，请使用 update()。"
            ) from e

        error_id = cursor.lastrowid
        assert error_id is not None
        logger.info(
            "Error inserted: id=%d question_id=%d pending=%s",
            error_id, question_id,
            user_reflection is None and error_summary is None,
        )
        return error_id

    # ── 单条查询 ────────────────────────────────────────────────────

    def get_by_question_id(self, question_id: int) -> dict[str, Any] | None:
        """按 ``question_id`` 查询错题记录（一题一行，故返回单条）。

        门面层「先查后写」的依赖：返回 ``None`` → 走 ``insert``，否则 → 走 ``update``。

        Args:
            question_id: 题目 ID（``questions.id``）。

        Returns:
            包含所有字段的字典（``error_summary`` 保持 JSON 原始字符串），
            不存在时返回 ``None``。
        """
        row = self._connect().execute(
            "SELECT * FROM errors WHERE question_id = ?", (question_id,)
        ).fetchone()
        return row_to_dict(row) if row else None

    def get_by_id(self, error_id: int) -> dict[str, Any] | None:
        """按 ``id`` 查询错题记录。

        Args:
            error_id: 错题记录 ID。

        Returns:
            包含所有字段的字典，不存在时返回 ``None``。
        """
        row = self._connect().execute(
            "SELECT * FROM errors WHERE id = ?", (error_id,)
        ).fetchone()
        return row_to_dict(row) if row else None

    # ── 更新 ────────────────────────────────────────────────────────

    def update(
        self,
        question_id: int,
        *,
        user_reflection: str | None = None,
        error_summary: str | None = None,
        resolved: bool | None = None,
    ) -> None:
        """按 ``question_id`` 更新错题记录的可变字段。

        ``None`` = 不修改该字段（与 ``questions.update`` 完全一致的语义）；
        传 ``""`` 会原样写入——「更新时空值不覆盖已有错因」是门面层的业务规则，
        本层不兜底。任何字段变更都会顺带把 ``last_seen`` 刷成 ``datetime('now')``
        （``first_seen`` 不动）。

        Args:
            question_id: 题目 ID（定位条件，本身不可改）。
            user_reflection: 新的用户口述错因。
            error_summary: 新的结构化错因总结（JSON 字符串，原样存）。
            resolved: 是否已掌握；再次错同一题时门面层应重置为 ``False``。

        Raises:
            ValueError: ``question_id`` 无对应错题记录。
        """
        conn = self._connect()

        # 动态构建 UPDATE SET 子句
        updates: list[str] = []
        params: list[Any] = []

        if user_reflection is not None:
            updates.append("user_reflection = ?")
            params.append(user_reflection)
        if error_summary is not None:
            updates.append("error_summary = ?")
            params.append(error_summary)
        if resolved is not None:
            updates.append("resolved = ?")
            params.append(1 if resolved else 0)

        if not updates:
            logger.debug("update: question_id=%d 无字段变更", question_id)
            return

        updates.append("last_seen = datetime('now')")
        params.append(question_id)
        cursor = conn.execute(
            f"UPDATE errors SET {', '.join(updates)} WHERE question_id = ?",
            params,
        )
        conn.commit()

        if cursor.rowcount == 0:
            raise ValueError(f"question_id={question_id} 不存在错题记录，无法更新")
        logger.info(
            "Error updated: question_id=%d fields=%s", question_id, updates
        )

    # ── 删除 ────────────────────────────────────────────────────────

    def delete_by_question_id(self, question_id: int) -> bool:
        """按 ``question_id`` 删除错题记录。

        .. warning::
            调用方应先同步删除 Chroma 中 ``err_{id}`` document（归门面层）。

        Args:
            question_id: 题目 ID（``questions.id``）。

        Returns:
            ``True`` = 删除成功，``False`` = 该题无错题记录。
        """
        conn = self._connect()
        cursor = conn.execute(
            "DELETE FROM errors WHERE question_id = ?", (question_id,)
        )
        conn.commit()
        if cursor.rowcount > 0:
            logger.info("Error record deleted: question_id=%d", question_id)
            return True
        logger.warning("delete_by_question_id: question_id=%d 无错题记录", question_id)
        return False

    # ── 统计 ────────────────────────────────────────────────────────

    def count(self) -> int:
        """错题总数。

        Returns:
            ``errors`` 表总行数。
        """
        row = self._connect().execute(
            "SELECT COUNT(*) AS cnt FROM errors"
        ).fetchone()
        return row["cnt"]

    def count_resolved(self) -> int:
        """已掌握错题数（``resolved=1``），供读门面算掌握率。

        Returns:
            ``resolved`` 为 1 的行数。
        """
        row = self._connect().execute(
            "SELECT COUNT(*) AS cnt FROM errors WHERE resolved = 1"
        ).fetchone()
        return row["cnt"]

    def count_pending(self) -> int:
        """「错因待补」行数（周报/补充提示用，门面层 ``pending_count`` 直接读）。

        判定 ``user_reflection IS NULL AND error_summary IS NULL``。只认 SQL NULL：
        ``""`` 空串必须由门面层规范化为 ``None`` 再落库，本层不做
        ``IS NULL OR = ''`` 兜底——否则"待补"判定会在两层各有一套规则。

        Returns:
            两列错因均为 NULL 的行数。
        """
        row = self._connect().execute(
            """SELECT COUNT(*) AS cnt FROM errors
               WHERE user_reflection IS NULL AND error_summary IS NULL"""
        ).fetchone()
        return row["cnt"]

    def __repr__(self) -> str:
        return "ErrorsDB()"


# ── Singleton factory ───────────────────────────────────────────────

_errors_db: ErrorsDB | None = None


def get_errors_db() -> ErrorsDB:
    """返回缓存的 ErrorsDB 单例。

    首次调用创建实例并缓存，后续调用返回同一实例。
    连接统一走全局共享 SQLite 连接，无需传参。

    Returns:
        ErrorsDB 实例。
    """
    global _errors_db
    if _errors_db is None:
        _errors_db = ErrorsDB()
    return _errors_db
