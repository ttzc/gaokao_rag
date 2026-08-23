# src/store/db/question_topics.py
# 题目-知识点关联表 SQLite 数据访问层：管理 ``question_topics`` 表。
#
# 关联设计：
#   - ``question_id`` → ``questions.id``（外键，题目被标注对象）
#   - ``topic_name`` → ``topics.name``（字符串关联，存名字而非 id）
#     为正式版知识点树形结构升级预留——名字是稳定 tag，即使 topics 表后续
#     增加 parent_id/path 等字段，``topic_name`` 也不会变。
#
# 常见场景：
#   - 摄取时 LLM 输出一道题涉及的知识点名字列表 → 批量写入本表
#   - 周报聚合：按 topic_name 统计题目数（count_by_topic）
#   - 知识点检索：按 topic_name 反查相关题目（get_by_topic）

from __future__ import annotations

import sqlite3
from typing import Any

from trpc_agent_sdk.log import logger

from src.store.db import get_shared_conn


# ── Schema ──────────────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS question_topics (
    question_id  INTEGER NOT NULL REFERENCES questions(id),
    topic_name   TEXT NOT NULL,                     -- 知识点规范名（tag）
    is_primary   BOOLEAN DEFAULT 0,                 -- 是否是主要知识点
    created_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (question_id, topic_name)
);"""

_CREATE_INDEX_QUESTION = "CREATE INDEX IF NOT EXISTS idx_qt_question ON question_topics(question_id);"
_CREATE_INDEX_TOPIC = "CREATE INDEX IF NOT EXISTS idx_qt_topic ON question_topics(topic_name);"

# 已初始化 schema 的连接 id 集合，避免重复执行 DDL（幂等但浪费）
_schema_initialized: set[int] = set()


# ── 行映射 ──────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 sqlite3.Row 转为普通字典。"""
    return dict(row)


# ── 数据访问类 ──────────────────────────────────────────────────────

class QuestionTopicsDB:
    """题目-知识点关联表 SQLite 数据访问层。

    封装 ``question_topics`` 表的所有 CRUD 操作。一道题可关联多个知识点
    （如"椭圆离心率最值"同时挂"椭圆"和"离心率"），
    ``is_primary`` 标记主要知识点（用于周报加权聚合）。

    典型调用顺序：
    1. ``add_many(question_id, topic_names, primary_index=0)`` → 摄取时批量标注
    2. ``get_by_question(question_id)`` → 查询某题涉及的知识点
    3. ``get_by_topic(topic_name)`` → 查询某知识点关联的所有题目
    4. ``count_by_topic(topic_name)`` → 周报聚合薄弱知识点

    连接走全局共享 SQLite 连接（由 ``src.store.db.get_shared_conn()`` 管理），
    保证跨表外键约束统一生效。
    """

    def __init__(self) -> None:
        pass

    # ── 连接管理 ────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """返回共享 SQLite 连接 + 初始化本表 schema。

        ``CREATE TABLE IF NOT EXISTS`` 幂等，每次调用无副作用，
        确保任意表类率先连接时所有表 schema 都被初始化。
        """
        conn = get_shared_conn()
        self._init_schema(conn)
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        """创建表和索引（IF NOT EXISTS）。

        使用连接 id 去重，同一连接只执行一次 DDL。

        Args:
            conn: 共享 SQLite 连接（由 ``_connect`` 传入）。
        """
        conn_id = id(conn)
        if conn_id in _schema_initialized:
            return
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_INDEX_QUESTION)
        conn.execute(_CREATE_INDEX_TOPIC)
        _schema_initialized.add(conn_id)
        logger.debug("question_topics table schema initialized (shared conn)")

    def close(self) -> None:
        """关闭数据库连接。

        共享连接由 ``src.store.db.close_shared_conn()`` 统一管理，
        本方法保留以兼容 context manager 协议，但不实际关闭连接。
        """
        logger.debug(
            "QuestionTopicsDB.close() skipped "
            "(shared connection managed by db/__init__.py)"
        )

    # ── 写入 ────────────────────────────────────────────────────────

    def add(self, question_id: int, topic_name: str, is_primary: bool = False) -> None:
        """插入一条关联（幂等：重复插入静默跳过）。

        联合主键 ``(question_id, topic_name)`` 天然防止重复关联。

        Args:
            question_id: 题目 ID（``questions.id``）。
            topic_name: 知识点规范名（tag，需在 ``topics`` 表中已存在）。
            is_primary: 是否是主要知识点，默认 ``False``。
        """
        conn = self._connect()
        conn.execute(
            """INSERT OR IGNORE INTO question_topics
                   (question_id, topic_name, is_primary)
               VALUES (?, ?, ?)""",
            (question_id, topic_name, 1 if is_primary else 0),
        )
        conn.commit()
        logger.debug(
            "question_topics: question_id=%d topic=%s is_primary=%s",
            question_id, topic_name, is_primary,
        )

    def add_many(
        self,
        question_id: int,
        topic_names: list[str],
        primary_index: int = 0,
    ) -> None:
        """批量插入关联（单事务）。

        使用 ``executemany`` 在同一个事务内完成所有插入，
        避免逐条 commit 的性能损耗。

        Args:
            question_id: 题目 ID。
            topic_names: 知识点名字列表（长度 >= 1）。
            primary_index: 主要知识点的索引（默认第 0 个），
                           对应 ``is_primary=1``。超出范围时静默无主知识点。
        """
        if not topic_names:
            return

        conn = self._connect()
        rows = [
            (question_id, name, 1 if i == primary_index else 0)
            for i, name in enumerate(topic_names)
        ]
        conn.executemany(
            """INSERT OR IGNORE INTO question_topics
                   (question_id, topic_name, is_primary)
               VALUES (?, ?, ?)""",
            rows,
        )
        conn.commit()
        primary_name = (
            topic_names[primary_index]
            if 0 <= primary_index < len(topic_names)
            else None
        )
        logger.info(
            "question_topics batch add: question_id=%d count=%d primary=%r",
            question_id, len(topic_names), primary_name,
        )

    # ── 查询 ────────────────────────────────────────────────────────

    def get_by_question(self, question_id: int) -> list[dict[str, Any]]:
        """查询某题的所有知识点关联，按 created_at 升序。

        Args:
            question_id: 题目 ID。

        Returns:
            关联字典列表，按 ``created_at`` 升序。每个 dict 包含
            ``question_id``, ``topic_name``, ``is_primary``, ``created_at``。
        """
        rows = self._connect().execute(
            """SELECT * FROM question_topics
               WHERE question_id = ?
               ORDER BY created_at""",
            (question_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_by_topic(self, topic_name: str) -> list[dict[str, Any]]:
        """查询某知识点关联的所有题目，按 created_at 升序。

        Args:
            topic_name: 知识点规范名（tag）。

        Returns:
            关联字典列表，按 ``created_at`` 升序。
        """
        rows = self._connect().execute(
            """SELECT * FROM question_topics
               WHERE topic_name = ?
               ORDER BY created_at""",
            (topic_name,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def count_by_topic(self, topic_name: str) -> int:
        """统计某知识点关联的题目数（周报聚合用）。

        Args:
            topic_name: 知识点规范名。

        Returns:
            关联题目数。
        """
        row = self._connect().execute(
            """SELECT COUNT(*) AS cnt FROM question_topics
               WHERE topic_name = ?""",
            (topic_name,),
        ).fetchone()
        return row["cnt"]

    # ── 删除 ────────────────────────────────────────────────────────

    def remove(self, question_id: int, topic_name: str) -> bool:
        """删除一条关联。

        Args:
            question_id: 题目 ID。
            topic_name: 知识点规范名。

        Returns:
            ``True`` = 删除成功，``False`` = 关联不存在。
        """
        conn = self._connect()
        cursor = conn.execute(
            """DELETE FROM question_topics
               WHERE question_id = ? AND topic_name = ?""",
            (question_id, topic_name),
        )
        conn.commit()
        if cursor.rowcount > 0:
            logger.debug(
                "question_topics removed: question_id=%d topic=%s",
                question_id, topic_name,
            )
            return True
        return False

    def __repr__(self) -> str:
        return "QuestionTopicsDB()"


# ── Singleton factory ───────────────────────────────────────────────

_question_topics_db: QuestionTopicsDB | None = None


def get_question_topics_db() -> QuestionTopicsDB:
    """返回缓存的 QuestionTopicsDB 单例。

    首次调用创建实例并缓存，后续调用返回同一实例。
    连接统一走全局共享 SQLite 连接，无需传参。

    Returns:
        QuestionTopicsDB 实例。
    """
    global _question_topics_db
    if _question_topics_db is None:
        _question_topics_db = QuestionTopicsDB()
    return _question_topics_db
