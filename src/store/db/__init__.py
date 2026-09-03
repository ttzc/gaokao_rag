# src/store/db/__init__.py
# SQLite 数据访问层包：按表拆分的模块 + 共享连接管理 + 表 DB 基类（SQLiteTableDB）。
#
# 连接策略：
#   所有表类默认共用同一个 SQLite 连接（通过 ``get_shared_conn()`` 获取），
#   保证外键约束统一生效、事务边界一致。
#   单测 / 特殊场景可通过构造参数传入独立连接。
#
# 当前模块：
#   files.py            → files 表（文件注册表）
#   questions.py        → questions 表（题目内容 + 答案解析 + 元数据）
#   question_topics.py  → question_topics 表（题目-知识点关联）
#   topics.py           → topics 表（知识点树，Materialized Path）

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from trpc_agent_sdk.log import logger

from src.config import config


# ── 共享连接管理 ────────────────────────────────────────────────────

class _SharedConnection:
    """单例共享 SQLite 连接。

    所有表类通过 ``get_shared_conn()`` 获取同一连接，避免多连接导致的
    外键约束不一致 / 事务分裂问题。WAL 模式 + foreign_keys 在连接创建时
    一次性设置。

    Attributes:
        db_path: SQLite 数据库文件路径。
        conn: 共享的 sqlite3.Connection 实例。
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def get(self) -> sqlite3.Connection:
        """获取共享连接，惰性初始化。

        Returns:
            已启用 WAL + foreign_keys 的 sqlite3.Connection。
        """
        if self.conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            logger.debug("Shared DB connection created: %s", self.db_path)
        return self.conn

    def close(self) -> None:
        """关闭共享连接。"""
        if self.conn is not None:
            self.conn.close()
            self.conn = None
            logger.debug("Shared DB connection closed")


# 全局共享连接单例（模块级，所有表类共用）
_shared: _SharedConnection | None = None


def get_shared_conn() -> sqlite3.Connection:
    """返回全局共享的 SQLite 连接。

    首次调用创建连接（含 WAL + foreign_keys），后续直接返回同一实例。
    各表类的 ``_connect()`` 应优先调用本函数而非自行创建连接。

    Returns:
        共享的 sqlite3.Connection。
    """
    global _shared
    if _shared is None:
        _shared = _SharedConnection(config.store.sqlite_path)
    return _shared.get()


def close_shared_conn() -> None:
    """关闭全局共享连接（应用退出时调用）。"""
    global _shared
    if _shared is not None:
        _shared.close()
        _shared = None


# ── 行映射 ──────────────────────────────────────────────────────────

def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 sqlite3.Row 转为普通字典（各表模块共用）。

    Args:
        row: sqlite3.Row 查询结果行。

    Returns:
        普通字典。
    """
    return dict(row)


# ── 表 DB 基类 ──────────────────────────────────────────────────────

class SQLiteTableDB:
    """表级 SQLite 数据访问层基类：共享连接 + 幂等 schema 初始化。

    各表模块（files / questions / topics / question_topics ...）的公共样板，
    子类只需声明两个类属性即获得 ``_connect`` / ``_init_schema`` / ``close``
    三件套：

        table_name: 表名（日志用）。
        ddl:        DDL 语句元组（CREATE TABLE + 全部索引，均 IF NOT EXISTS）。

    schema 初始化按「类 × 连接 id」去重（DDL 幂等，但避免每次连接重复执行）；
    测试隔离由 ``reset_schema_tracking()`` 统一重置记录。
    """

    table_name: str = ""
    ddl: tuple[str, ...] = ()

    # {子类: 已执行过本类 DDL 的连接 id 集合}，集中替代原各模块的
    # _schema_initialized 私有全局（conftest 无需逐模块清理）
    _initialized: dict[type, set[int]] = {}

    def _connect(self) -> sqlite3.Connection:
        """返回共享 SQLite 连接 + 初始化本表 schema。

        ``CREATE TABLE IF NOT EXISTS`` 幂等，每次调用无副作用，
        确保任意表类率先连接时所有表 schema 都被初始化。
        """
        conn = get_shared_conn()
        self._init_schema(conn)
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        """按 ``ddl`` 元组创建表和索引（IF NOT EXISTS），按「类 × 连接 id」去重。

        Args:
            conn: 共享 SQLite 连接（由 ``_connect`` 传入）。
        """
        initialized = SQLiteTableDB._initialized.setdefault(type(self), set())
        conn_id = id(conn)
        if conn_id in initialized:
            return
        for stmt in self.ddl:
            conn.execute(stmt)
        initialized.add(conn_id)
        logger.debug("%s table schema initialized (shared conn)", self.table_name)

    def close(self) -> None:
        """关闭数据库连接。

        共享连接由 ``close_shared_conn()`` 统一管理，本方法保留以兼容
        context manager 协议，但不实际关闭连接。
        """
        logger.debug(
            "%s.close() skipped (shared connection managed by db/__init__.py)",
            type(self).__name__,
        )


def reset_schema_tracking() -> None:
    """清空所有表类的 schema 初始化记录（测试隔离用，conftest 每测试前调用）。"""
    SQLiteTableDB._initialized.clear()
