# src/store/db/files.py
# 文件注册表 SQLite 数据访问层：管理 ``files`` 表（源文件元数据中枢）。
#
# 与 FileStore（file_store.py）的分工：
#   FileStore  → 物理文件读写（哈希命名、路径穿越防护）
#   FilesDB    → 数据库元数据（sha256 去重、title 管理、完整性校验）
#
# 业务表（questions / knowledge_notes / exam_attempts）通过 file_id 引用本表。

from __future__ import annotations

import sqlite3
from typing import Any

from trpc_agent_sdk.log import logger

from src.config import config
from src.store.db import get_shared_conn


# ── Schema ──────────────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT,
    file_path   TEXT UNIQUE NOT NULL,
    sha256      TEXT NOT NULL,
    size        INTEGER,
    kind        TEXT NOT NULL,
    source_hint TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);"""

_CREATE_INDEX_KIND = "CREATE INDEX IF NOT EXISTS idx_files_kind ON files(kind);"
_CREATE_INDEX_SHA = "CREATE UNIQUE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);"

# 已初始化 schema 的连接 id 集合，避免重复执行 DDL（幂等但浪费）
_schema_initialized: set[int] = set()


# ── 行映射 ──────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 sqlite3.Row 转为普通字典。"""
    return dict(row)


# ── 数据访问类 ──────────────────────────────────────────────────────

class FilesDB:
    """文件注册表 SQLite 数据访问层。

    封装 ``files`` 表的所有 CRUD 操作。与 ``FileStore``（物理文件层）配合使用：

    - **FileStore** 负责磁盘上的哈希命名文件读写（save_raw / read / delete）
    - **FilesDB** 负责数据库中的元数据记录（register / set_title / verify）

    典型调用顺序：
    1. ``FileStore.save_raw(content, kind="pdf")`` → 落盘 + 返回相对路径
    2. ``FilesDB.register(file_path=..., sha256=..., ...)`` → 入库 + 返回 file_id
    3. 业务表（questions / knowledge_notes）通过 ``file_id`` 引用

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
        conn.execute(_CREATE_INDEX_KIND)
        conn.execute(_CREATE_INDEX_SHA)
        _schema_initialized.add(conn_id)
        logger.debug("files table schema initialized (shared conn)")

    def close(self) -> None:
        """关闭数据库连接。

        共享连接由 ``src.store.db.close_shared_conn()`` 统一管理，
        本方法保留以兼容 context manager 协议，但不实际关闭连接。
        """
        logger.debug("FilesDB.close() skipped (shared connection managed by db/__init__.py)")

    # ── 注册（INSERT + 去重） ────────────────────────────────────────

    def register(
        self,
        *,
        file_path: str,
        sha256: str,
        size: int,
        kind: str,
        title: str | None = None,
        source_hint: str | None = None,
    ) -> int:
        """注册文件到 ``files`` 表。

        采用原子化去重：先尝试 ``INSERT OR IGNORE``，命中已有 sha256 时忽略插入
        并通过 ``get_by_sha`` 回退查询返回已有 ``id``，消除 SELECT→INSERT 的并发竞态。

        Args:
            file_path: 磁盘相对路径（基准=项目根），如 ``"data/files/raw/pdfs/abc.pdf"``。
                       外部存储模式下为绝对路径。
            sha256: 文件内容的 sha256 hex digest（64 字符）。
            size: 文件字节大小。
            kind: 文件类型，``"pdf"``（试卷 PDF）或 ``"image"``（学生上传照片/截图）。
            title: 语义标题（如 ``"2026 南昌一模数学卷"``），可空=待 LLM 生成或用户自定义。
            source_hint: 原始来源备注（如 ``"QQ 上传"`` / ``"ima 导出"``），可选。

        Returns:
            文件记录的 ``id``：新建则返回新自增 ID，同内容命中则返回已有 ID。
        """
        conn = self._connect()

        # 原子化去重：INSERT OR IGNORE 避免 SELECT→INSERT 竞态
        cursor = conn.execute(
            """INSERT OR IGNORE INTO files (title, file_path, sha256, size, kind, source_hint)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, file_path, sha256, size, kind, source_hint),
        )
        conn.commit()
        if cursor.rowcount == 0:
            # IGNORE 命中已有行，回退查询返回已有 id
            existing = self.get_by_sha(sha256)
            assert existing is not None  # rowcount==0 说明 sha256 已存在
            logger.debug(
                "File dedup (atomic) by sha256: id=%d (sha256=%s)",
                existing["id"], sha256[:12],
            )
            return existing["id"]

        file_id = cursor.lastrowid
        assert file_id is not None  # INSERT 成功后 lastrowid 必为 int
        logger.info(
            "File registered: id=%d kind=%s size=%d sha256=%s path=%s",
            file_id, kind, size, sha256[:12], file_path,
        )
        return file_id

    # ── 单条查询 ────────────────────────────────────────────────────

    def get_by_id(self, file_id: int) -> dict[str, Any] | None:
        """按 ``id`` 查询文件记录。

        Args:
            file_id: 文件记录 ID。

        Returns:
            包含所有字段的字典，不存在时返回 ``None``。
        """
        row = self._connect().execute(
            "SELECT * FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_by_path(self, file_path: str) -> dict[str, Any] | None:
        """按 ``file_path`` 查询文件记录。

        Args:
            file_path: 磁盘相对路径，如 ``"data/files/raw/pdfs/abc.pdf"``。

        Returns:
            记录字典，不存在时返回 ``None``。
        """
        row = self._connect().execute(
            "SELECT * FROM files WHERE file_path = ?", (file_path,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_by_sha(self, sha256: str) -> dict[str, Any] | None:
        """按 ``sha256`` 查询文件记录。

        Args:
            sha256: 内容哈希值。

        Returns:
            记录字典，不存在时返回 ``None``。
        """
        row = self._connect().execute(
            "SELECT * FROM files WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_by_title(self, title: str) -> dict[str, Any] | None:
        """按 ``title`` 查询文件记录。

        用户按试卷名找卷子的自然入口（如 ``"2026 南昌一模数学卷"``）。
        ``title`` 为 ``NULL`` 的记录不会命中。

        Args:
            title: 语义标题（精确匹配）。

        Returns:
            记录字典，不存在时返回 ``None``。
        """
        row = self._connect().execute(
            "SELECT * FROM files WHERE title = ?", (title,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    # ── 列表查询 ────────────────────────────────────────────────────

    def list_all(self, kind: str | None = None) -> list[dict[str, Any]]:
        """列出所有文件记录，可按 ``kind`` 过滤。

        Args:
            kind: 可选过滤类型。``"pdf"`` 列出试卷，``"image"`` 列出图片，
                  传 ``None``（默认）列出全部。

        Returns:
            记录字典列表，按 ``id`` 升序排列。
        """
        conn = self._connect()
        if kind is not None:
            rows = conn.execute(
                "SELECT * FROM files WHERE kind = ? ORDER BY id", (kind,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM files ORDER BY id").fetchall()
        return [_row_to_dict(r) for r in rows]

    def count(self, kind: str | None = None) -> int:
        """统计文件记录数。

        Args:
            kind: 可选过滤类型。

        Returns:
            符合条件的记录数。
        """
        conn = self._connect()
        if kind is not None:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM files WHERE kind = ?", (kind,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM files").fetchone()
        return row["cnt"]

    # ── 更新 ────────────────────────────────────────────────────────

    def set_title(self, file_id: int, title: str) -> None:
        """更新文件语义标题。

        title 挂在文件上而非题目上——一份试卷改一次标题，关联的所有题目全局生效。

        Args:
            file_id: 文件记录 ID。
            title: 新的语义标题（如 ``"2026 南昌一模数学卷"``）。

        Raises:
            ValueError: ``file_id`` 不存在。
        """
        conn = self._connect()
        cursor = conn.execute(
            "UPDATE files SET title = ? WHERE id = ?", (title, file_id)
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"file_id={file_id} 不存在，无法更新 title")
        logger.info("File title updated: id=%d → %r", file_id, title)

    def set_source_hint(self, file_id: int, source_hint: str) -> None:
        """更新来源备注。

        Args:
            file_id: 文件记录 ID。
            source_hint: 来源备注文本（如 ``"QQ 上传"``）。

        Raises:
            ValueError: ``file_id`` 不存在。
        """
        conn = self._connect()
        cursor = conn.execute(
            "UPDATE files SET source_hint = ? WHERE id = ?", (source_hint, file_id)
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"file_id={file_id} 不存在，无法更新 source_hint")
        logger.info("File source_hint updated: id=%d → %r", file_id, source_hint)

    # ── 完整性校验 ──────────────────────────────────────────────────

    def verify(self, file_id: int, expected_sha256: str) -> bool:
        """校验文件完整性。

        比较数据库中存储的 ``sha256`` 与传入的预期值是否一致，用于检测 raw 文件
        是否损坏或被篡改。

        Args:
            file_id: 文件记录 ID。
            expected_sha256: 预期 sha256 值（通常由 ``FileStore.compute_hash`` 计算）。

        Returns:
            ``True`` = 哈希一致，``False`` = 不一致或 ``file_id`` 不存在。
        """
        row = self.get_by_id(file_id)
        if row is None:
            logger.warning("verify: file_id=%d 不存在", file_id)
            return False
        match = row["sha256"] == expected_sha256
        if not match:
            logger.warning(
                "verify: file_id=%d sha256 不匹配 (db=%s, expected=%s)",
                file_id, row["sha256"][:12], expected_sha256[:12],
            )
        return match

    # ── 删除 ────────────────────────────────────────────────────────

    def delete(self, file_id: int) -> bool:
        """删除文件记录。

        .. warning::
            调用方应先确认无业务数据引用（questions / knowledge_notes / exam_attempts
            等表通过 ``file_id`` 引用本表）。有引用时删除会导致外键悬空。

        Args:
            file_id: 文件记录 ID。

        Returns:
            ``True`` = 删除成功，``False`` = ``file_id`` 不存在。
        """
        conn = self._connect()
        cursor = conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info("File record deleted: id=%d", file_id)
            return True
        logger.warning("delete: file_id=%d 不存在", file_id)
        return False

    def __repr__(self) -> str:
        return "FilesDB()"


# ── Singleton factory ───────────────────────────────────────────────

_files_db: FilesDB | None = None


def get_files_db() -> FilesDB:
    """返回缓存的 FilesDB 单例。

    首次调用创建实例并缓存，后续调用返回同一实例。
    连接统一走全局共享 SQLite 连接，无需传参。

    Returns:
        FilesDB 实例。
    """
    global _files_db
    if _files_db is None:
        _files_db = FilesDB()
    return _files_db
