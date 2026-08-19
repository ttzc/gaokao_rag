# tests/conftest.py
# 测试全局配置：每测试自动清理 SQLite 数据，保证测试隔离。

from __future__ import annotations

import sqlite3

import pytest

from src.store.db import get_shared_conn
from src.store.db.files import _files_db, _schema_initialized as _files_schema_flag
from src.store.db.questions import _questions_db, _schema_initialized as _questions_schema_flag


@pytest.fixture(autouse=True)
def _reset_tables():
    """每个测试前后清空 files + questions 表，保证数据隔离。

    注意：不关闭共享连接（保持 WAL + foreign_keys 配置），
    仅 DELETE 数据，schema 保留。缺失表时静默跳过。
    """
    conn = get_shared_conn()
    yield
    for table in ("questions", "files"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass  # 表尚未创建（仅测另一张表时）
    conn.commit()


@pytest.fixture(autouse=True, scope="session")
def _reset_singletons():
    """Session 结束前清理单例 + schema 初始化标志。"""
    yield
    global _files_db, _questions_db
    _files_db = None  # type: ignore[misc]
    _questions_db = None  # type: ignore[misc]
    _files_schema_flag.clear()
    _questions_schema_flag.clear()
