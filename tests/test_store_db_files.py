"""FilesDB 测试：覆盖注册去重、CRUD、sha256 校验、kind 过滤。

依赖 conftest._reset_state（每测试前清空业务表 + 重置单例），测试之间无顺序依赖。
"""

from pathlib import Path

import pytest

from src.store.db.files import FilesDB, get_files_db


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture()
def db() -> FilesDB:
    """FilesDB 实例（共享连接，数据由 conftest 每测试前清空）。"""
    return get_files_db()


@pytest.fixture()
def sample_file(db: FilesDB) -> int:
    """预注册一条 pdf 文件记录，返回 file_id。"""
    return db.register(
        file_path="data/files/raw/pdfs/abc123.pdf",
        sha256="a" * 64,
        size=1024,
        kind="pdf",
        title="2026 南昌一模数学卷",
        source_hint="ima 导出",
    )


@pytest.fixture()
def sample_image(db: FilesDB) -> int:
    """预注册一条 image 文件记录，返回 file_id。"""
    return db.register(
        file_path="data/files/raw/images/uploaded/def456.jpg",
        sha256="b" * 64,
        size=512,
        kind="image",
        title="题目截图",
        source_hint="QQ 上传",
    )


# ── 初始化 ──────────────────────────────────────────────────────────

class TestInit:

    def test_creates_db_file(self):
        """共享连接创建时，数据库文件在 config.store.sqlite_path 落盘。"""
        from src.config import config
        db_path = config.store.sqlite_path
        db = FilesDB()
        db._connect()
        assert Path(db_path).exists()

    def test_idempotent_init(self):
        """多次初始化共享连接不报错（幂等）。"""
        db1 = FilesDB()
        db1._connect()
        db2 = FilesDB()
        db2._connect()

    def test_schema_has_files_table(self, db: FilesDB):
        conn = db._connect()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='files'"
        ).fetchone()
        assert row is not None

    def test_schema_has_indexes(self, db: FilesDB):
        conn = db._connect()
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='files'"
        ).fetchall()
        index_names = {r["name"] for r in indexes}
        assert "idx_files_kind" in index_names
        assert "idx_files_sha" in index_names


# ── register ────────────────────────────────────────────────────────

class TestRegister:

    def test_register_returns_id(self, db: FilesDB):
        file_id = db.register(
            file_path="data/files/raw/pdfs/x.pdf",
            sha256="a" * 64,
            size=100,
            kind="pdf",
        )
        assert isinstance(file_id, int)
        assert file_id > 0

    def test_register_stores_all_fields(self, db: FilesDB):
        file_id = db.register(
            file_path="data/files/raw/pdfs/x.pdf",
            sha256="a" * 64,
            size=2048,
            kind="pdf",
            title="测试试卷",
            source_hint="ima 导出",
        )
        row = db.get_by_id(file_id)
        assert row["file_path"] == "data/files/raw/pdfs/x.pdf"
        assert row["sha256"] == "a" * 64
        assert row["size"] == 2048
        assert row["kind"] == "pdf"
        assert row["title"] == "测试试卷"
        assert row["source_hint"] == "ima 导出"

    def test_register_sets_created_at(self, db: FilesDB):
        file_id = db.register(
            file_path="data/files/raw/pdfs/x.pdf",
            sha256="a" * 64,
            size=100,
            kind="pdf",
        )
        row = db.get_by_id(file_id)
        assert row["created_at"] is not None

    def test_register_dedup_by_sha256(self, db: FilesDB):
        id1 = db.register(
            file_path="data/files/raw/pdfs/first.pdf",
            sha256="same" * 16,
            size=100,
            kind="pdf",
        )
        id2 = db.register(
            file_path="data/files/raw/pdfs/second.pdf",
            sha256="same" * 16,
            size=100,
            kind="pdf",
        )
        assert id1 == id2

    def test_register_dedup_only_one_row(self, db: FilesDB):
        db.register(
            file_path="data/files/raw/pdfs/first.pdf",
            sha256="same" * 16,
            size=100,
            kind="pdf",
        )
        db.register(
            file_path="data/files/raw/pdfs/second.pdf",
            sha256="same" * 16,
            size=100,
            kind="pdf",
        )
        assert db.count() == 1

    def test_register_different_sha_different_rows(self, db: FilesDB):
        db.register(
            file_path="data/files/raw/pdfs/a.pdf",
            sha256="a" * 64,
            size=100,
            kind="pdf",
        )
        db.register(
            file_path="data/files/raw/pdfs/b.pdf",
            sha256="b" * 64,
            size=100,
            kind="pdf",
        )
        assert db.count() == 2

    def test_register_kind_image(self, db: FilesDB):
        file_id = db.register(
            file_path="data/files/raw/images/uploaded/photo.jpg",
            sha256="c" * 64,
            size=500,
            kind="image",
        )
        row = db.get_by_id(file_id)
        assert row["kind"] == "image"

    def test_register_nullable_fields(self, db: FilesDB):
        """title 和 source_hint 为空时也能注册。"""
        file_id = db.register(
            file_path="data/files/raw/pdfs/x.pdf",
            sha256="d" * 64,
            size=100,
            kind="pdf",
        )
        row = db.get_by_id(file_id)
        assert row["title"] is None
        assert row["source_hint"] is None

    def test_register_atomic_dedup_returns_same_id(self, db: FilesDB):
        """原子化去重：重复注册同 sha256 返回同一 id。"""
        id1 = db.register(
            file_path="data/files/raw/pdfs/first.pdf",
            sha256="atomic" * 8,
            size=100,
            kind="pdf",
            title="首次",
        )
        id2 = db.register(
            file_path="data/files/raw/pdfs/second.pdf",
            sha256="atomic" * 8,
            size=100,
            kind="pdf",
            title="二次",
        )
        assert id1 == id2

    def test_register_atomic_dedup_count_unchanged(self, db: FilesDB):
        """原子化去重：重复注册不增加行数。"""
        db.register(
            file_path="data/files/raw/pdfs/first.pdf",
            sha256="atomic" * 8,
            size=100,
            kind="pdf",
        )
        db.register(
            file_path="data/files/raw/pdfs/second.pdf",
            sha256="atomic" * 8,
            size=100,
            kind="pdf",
        )
        assert db.count() == 1

    def test_register_returns_int(self, db: FilesDB):
        """原子化后 register 永远返回 int（不可达 None）。"""
        file_id = db.register(
            file_path="data/files/raw/pdfs/x.pdf",
            sha256="e" * 64,
            size=100,
            kind="pdf",
        )
        assert type(file_id) is int


# ── 单条查询 ────────────────────────────────────────────────────────

class TestQuery:

    def test_get_by_id_existing(self, db: FilesDB, sample_file: int):
        row = db.get_by_id(sample_file)
        assert row is not None
        assert row["id"] == sample_file

    def test_get_by_id_missing(self, db: FilesDB):
        assert db.get_by_id(9999) is None

    def test_get_by_path_existing(self, db: FilesDB, sample_file: int):
        row = db.get_by_path("data/files/raw/pdfs/abc123.pdf")
        assert row is not None
        assert row["id"] == sample_file

    def test_get_by_path_missing(self, db: FilesDB):
        assert db.get_by_path("data/files/raw/pdfs/nonexistent.pdf") is None

    def test_get_by_sha_existing(self, db: FilesDB, sample_file: int):
        row = db.get_by_sha("a" * 64)
        assert row is not None
        assert row["id"] == sample_file

    def test_get_by_sha_missing(self, db: FilesDB):
        assert db.get_by_sha("0" * 64) is None

    def test_get_by_title_existing(self, db: FilesDB, sample_file: int):
        row = db.get_by_title("2026 南昌一模数学卷")
        assert row is not None
        assert row["id"] == sample_file

    def test_get_by_title_missing(self, db: FilesDB):
        assert db.get_by_title("不存在的试卷") is None

    def test_get_by_title_null_title_not_matched(self, db: FilesDB):
        """title 为 NULL 的记录不会命中精确匹配。"""
        db.register(
            file_path="data/files/raw/pdfs/no_title.pdf",
            sha256="f" * 64,
            size=100,
            kind="pdf",
        )
        assert db.get_by_title("") is None

    def test_row_has_all_columns(self, db: FilesDB, sample_file: int):
        row = db.get_by_id(sample_file)
        expected_keys = {
            "id", "title", "file_path", "sha256", "size",
            "kind", "source_hint", "created_at",
        }
        assert expected_keys.issubset(row.keys())


# ── 列表查询 ────────────────────────────────────────────────────────

class TestList:

    def test_list_all_empty(self, db: FilesDB):
        assert db.list_all() == []

    def test_list_all_sorted_by_id(self, db: FilesDB):
        db.register(file_path="a.pdf", sha256="a" * 64, size=1, kind="pdf")
        db.register(file_path="b.pdf", sha256="b" * 64, size=2, kind="pdf")
        db.register(file_path="c.pdf", sha256="c" * 64, size=3, kind="pdf")
        ids = [r["id"] for r in db.list_all()]
        assert ids == sorted(ids)

    def test_list_all_filter_by_kind_pdf(self, db: FilesDB, sample_file: int, sample_image: int):
        rows = db.list_all(kind="pdf")
        assert len(rows) == 1
        assert rows[0]["id"] == sample_file

    def test_list_all_filter_by_kind_image(self, db: FilesDB, sample_file: int, sample_image: int):
        rows = db.list_all(kind="image")
        assert len(rows) == 1
        assert rows[0]["id"] == sample_image

    def test_count_empty(self, db: FilesDB):
        assert db.count() == 0

    def test_count_all(self, db: FilesDB, sample_file: int, sample_image: int):
        assert db.count() == 2

    def test_count_by_kind(self, db: FilesDB, sample_file: int, sample_image: int):
        assert db.count(kind="pdf") == 1
        assert db.count(kind="image") == 1
        assert db.count(kind="unknown") == 0


# ── 更新 ────────────────────────────────────────────────────────────

class TestUpdate:

    def test_set_title(self, db: FilesDB, sample_file: int):
        db.set_title(sample_file, "2026 深圳二模数学卷")
        row = db.get_by_id(sample_file)
        assert row["title"] == "2026 深圳二模数学卷"

    def test_set_title_missing_raises(self, db: FilesDB):
        with pytest.raises(ValueError, match="不存在"):
            db.set_title(9999, "不存在的标题")

    def test_set_title_overwrites(self, db: FilesDB, sample_file: int):
        db.set_title(sample_file, "标题一")
        db.set_title(sample_file, "标题二")
        row = db.get_by_id(sample_file)
        assert row["title"] == "标题二"

    def test_set_source_hint(self, db: FilesDB, sample_file: int):
        db.set_source_hint(sample_file, "QQ 上传")
        row = db.get_by_id(sample_file)
        assert row["source_hint"] == "QQ 上传"

    def test_set_source_hint_missing_raises(self, db: FilesDB):
        with pytest.raises(ValueError, match="不存在"):
            db.set_source_hint(9999, "未知来源")


# ── 完整性校验 ──────────────────────────────────────────────────────

class TestVerify:

    def test_verify_match(self, db: FilesDB, sample_file: int):
        assert db.verify(sample_file, "a" * 64) is True

    def test_verify_mismatch(self, db: FilesDB, sample_file: int):
        assert db.verify(sample_file, "0" * 64) is False

    def test_verify_missing_id(self, db: FilesDB):
        assert db.verify(9999, "a" * 64) is False


# ── 删除 ────────────────────────────────────────────────────────────

class TestDelete:

    def test_delete_existing(self, db: FilesDB, sample_file: int):
        result = db.delete(sample_file)
        assert result is True
        assert db.get_by_id(sample_file) is None

    def test_delete_missing_returns_false(self, db: FilesDB):
        assert db.delete(9999) is False

    def test_delete_reduces_count(self, db: FilesDB, sample_file: int, sample_image: int):
        db.delete(sample_file)
        assert db.count() == 1


# ── 直接使用 ──────────────────────────────────────────────────────

class TestDirectUsage:

    def test_create_and_use(self):
        db = get_files_db()
        file_id = db.register(
            file_path="data/files/raw/pdfs/x.pdf",
            sha256="e" * 64,
            size=100,
            kind="pdf",
        )
        assert file_id > 0
        assert db.count() == 1


# ── 单例 factory ───────────────────────────────────────────────────

class TestSingleton:

    def test_get_files_db_returns_instance(self):
        db = get_files_db()
        assert isinstance(db, FilesDB)
