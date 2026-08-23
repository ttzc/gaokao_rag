# src/store/db/topics.py
# 知识点标签注册表 SQLite 数据访问层：管理 ``topics`` 表（扁平 tag）。
#
# 设计定位：
#   - ``name`` 即知识点规范名（tag），直接用于题目标注和 Chroma metadata
#   - ``aliases`` 存同义表述 JSON 列表，检索时按 name + aliases 并集匹配
#   - ``question_topics`` 关联表存 ``topic_name``（名字），不存 ``topic_id``
#     ——为正式版树形结构升级预留（正式版可扩展为 topic_id + 树展开）
#
# MVP 不做的事：
#   无父子关系（parent_id）、无路径枚举（path）、无树展开（expand_tag_names）
#   无动态归位/合并/挂载。

from __future__ import annotations

import json
import sqlite3
from typing import Any

from trpc_agent_sdk.log import logger

from src.store.db import get_shared_conn


# ── Schema ──────────────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,              -- 知识点规范名（即 tag）
    aliases     TEXT DEFAULT '[]',                 -- 同义表述 JSON: ["离心率", "e=c/a"]
    created_at  TEXT DEFAULT (datetime('now'))
);"""

_CREATE_INDEX_NAME = "CREATE INDEX IF NOT EXISTS idx_topics_name ON topics(name);"

# 已初始化 schema 的连接 id 集合，避免重复执行 DDL（幂等但浪费）
_schema_initialized: set[int] = set()


# ── 行映射 ──────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 sqlite3.Row 转为普通字典。"""
    return dict(row)


# ── 数据访问类 ──────────────────────────────────────────────────────

class TopicsDB:
    """知识点标签注册表 SQLite 数据访问层。

    封装 ``topics`` 表的所有 CRUD 操作。摄取管线调用本类注册/查询知识点，
    摄入 Agent / 知识整理 Agent 通过本类完成"归位"（将 LLM 输出的知识点名字
    映射到规范名）和"合并"（将别名的知识点合并到已有规范名下）。

    典型调用顺序：
    1. ``search(keyword)`` → 先查是否已存在（name 精确或 aliases 模糊）
    2. 未命中 → ``create(name, aliases)`` → 返回新 topic 的 id
    3. 摄取时后续需要追加同义表述 → ``add_alias(topic_id, alias)``
    4. 题目标注 → 使用 ``topic_name`` 写入 ``question_topics`` 表

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
        conn.execute(_CREATE_INDEX_NAME)
        _schema_initialized.add(conn_id)
        logger.debug("topics table schema initialized (shared conn)")

    def close(self) -> None:
        """关闭数据库连接。

        共享连接由 ``src.store.db.close_shared_conn()`` 统一管理，
        本方法保留以兼容 context manager 协议，但不实际关闭连接。
        """
        logger.debug("TopicsDB.close() skipped (shared connection managed by db/__init__.py)")

    # ── 查询 ────────────────────────────────────────────────────────

    def search(self, keyword: str) -> list[dict[str, Any]]:
        """按 name 或 aliases 模糊查。

        匹配规则：name = keyword（精确） OR aliases LIKE '%keyword%'（JSON 数组内模糊）。
        两种条件取并集。

        Args:
            keyword: 搜索关键词（如 ``"椭圆"``）。

        Returns:
            匹配的 topic 字典列表，按 ``id`` 升序排列。每个 dict 包含
            ``id``, ``name``, ``aliases``, ``created_at``。
        """
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM topics
               WHERE name = ? OR aliases LIKE ?
               ORDER BY id""",
            (keyword, f"%{keyword}%"),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_by_name(self, name: str) -> dict[str, Any] | None:
        """精确按 name 查询。

        Args:
            name: 知识点规范名（精确匹配）。

        Returns:
            topic 字典（包含 ``id``, ``name``, ``aliases``, ``created_at``），
            不存在时返回 ``None``。
        """
        row = self._connect().execute(
            "SELECT * FROM topics WHERE name = ?", (name,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        """列出所有 topics，按 id 升序。

        Returns:
            topic 字典列表，按 ``id`` 升序排列。
        """
        rows = self._connect().execute(
            "SELECT * FROM topics ORDER BY id"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ── 写入 ────────────────────────────────────────────────────────

    def create(self, name: str, aliases: list[str] | None = None) -> int:
        """创建新 topic。

        ``name`` 必须唯一（UNIQUE 约束），重复时抛出 ``ValueError``。

        Args:
            name: 规范名（如 ``"椭圆离心率"``）。
            aliases: 同义表述列表，默认空列表。

        Returns:
            新创建 topic 的 ``id``（自增主键）。

        Raises:
            ValueError: ``name`` 已存在（UNIQUE 约束冲突）。
        """
        conn = self._connect()
        aliases_json = json.dumps(aliases or [], ensure_ascii=False)

        try:
            cursor = conn.execute(
                "INSERT INTO topics (name, aliases) VALUES (?, ?)",
                (name, aliases_json),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(
                f"topic name={name!r} 已存在，违反 UNIQUE 约束。"
                " 如需追加 alias，请使用 add_alias()。"
            )

        topic_id = cursor.lastrowid
        assert topic_id is not None
        logger.info("Topic created: id=%d name=%r", topic_id, name)
        return topic_id

    def add_alias(self, topic_id: int, alias: str) -> None:
        """给已有 topic 追加一个 alias。

        内部读取现有 aliases JSON 列表，append 后写回（自动去重）。

        Args:
            topic_id: 目标 topic 的 ``id``。
            alias: 要追加的同义表述。

        Raises:
            ValueError: ``topic_id`` 不存在。
        """
        conn = self._connect()

        # 先确认 topic 存在
        row = conn.execute(
            "SELECT aliases FROM topics WHERE id = ?", (topic_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"topic_id={topic_id} 不存在，无法追加 alias")

        aliases: list[str] = json.loads(row["aliases"] or "[]")
        if alias in aliases:
            logger.debug(
                "add_alias: topic_id=%d alias=%r 已存在，跳过", topic_id, alias
            )
            return

        aliases.append(alias)
        new_json = json.dumps(aliases, ensure_ascii=False)
        conn.execute(
            "UPDATE topics SET aliases = ? WHERE id = ?",
            (new_json, topic_id),
        )
        conn.commit()
        logger.info("Alias added: topic_id=%d alias=%r", topic_id, alias)

    def __repr__(self) -> str:
        return "TopicsDB()"


# ── Singleton factory ───────────────────────────────────────────────

_topics_db: TopicsDB | None = None


def get_topics_db() -> TopicsDB:
    """返回缓存的 TopicsDB 单例。

    首次调用创建实例并缓存，后续调用返回同一实例。
    连接统一走全局共享 SQLite 连接，无需传参。

    Returns:
        TopicsDB 实例。
    """
    global _topics_db
    if _topics_db is None:
        _topics_db = TopicsDB()
    return _topics_db
