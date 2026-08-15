"""FileStore 测试：覆盖 CRUD、哈希命名去重、路径基准（项目根相对）。"""

import hashlib
from pathlib import Path

import pytest

from src.store.file_store import FileStore


@pytest.fixture()
def store(tmp_path: Path) -> FileStore:
    """基于临时目录的 FileStore 实例（_rel 仍返回项目根相对路径）。"""
    return FileStore(base_dir=str(tmp_path))


# ── 初始化 ────────────────────────────────────────────────────────

class TestInit:

    def test_base_is_project_root_relative(self):
        store = FileStore()
        # base = _PROJECT_ROOT / data/files/raw，parents: raw → files → data → project_root
        assert store.base.name == "raw"
        assert store.base.parent.name == "files"
        assert store.base.parent.parent.name == "data"

    def test_creates_all_subdirs(self):
        store = FileStore()
        expected = [
            "pdfs",
            "images/uploaded",
            "images/extracted",
            "../processed/text",
            "../processed/vlm_desc",
        ]
        for rel in expected:
            target = store.base / rel
            assert target.is_dir(), f"目录未创建: {target}"

    def test_idempotent_mkdir(self):
        store = FileStore()
        store2 = FileStore()
        assert store2.base == store.base


# ── save_raw ──────────────────────────────────────────────────────

class TestSaveRaw:

    def test_save_pdf_returns_project_root_relative_path(self, store: FileStore):
        path = store.save_raw(b"%PDF-1.4 fake pdf content", kind="pdf")
        assert path.startswith("data/files/raw/pdfs/")
        assert path.endswith(".pdf")

    def test_save_pdf_hash_named(self, store: FileStore):
        content = b"%PDF-1.4 some content"
        expected_hash = hashlib.sha256(content).hexdigest()
        path = store.save_raw(content, kind="pdf")
        assert path == f"data/files/raw/pdfs/{expected_hash}.pdf"

    def test_save_pdf_writes_file(self, store: FileStore):
        content = b"%PDF-1.4 fake"
        path = store.save_raw(content, kind="pdf")
        full = store._resolve(path)
        assert full.exists()
        assert full.read_bytes() == content

    def test_save_image_uploaded(self, store: FileStore):
        jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        path = store.save_raw(jpg, kind="image", subdir="uploaded")
        assert "data/files/raw/images/uploaded/" in path
        assert path.endswith(".jpg")

    def test_save_image_extracted(self, store: FileStore):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        path = store.save_raw(png, kind="image", subdir="extracted")
        assert "data/files/raw/images/extracted/" in path
        assert path.endswith(".png")

    def test_save_raw_dedup_same_content(self, store: FileStore):
        content = b"%PDF-1.4 duplicate"
        path1 = store.save_raw(content, kind="pdf")
        path2 = store.save_raw(content, kind="pdf")
        assert path1 == path2
        pdf_dir = store._subdirs["raw_pdf"]
        assert len(list(pdf_dir.iterdir())) == 1

    def test_save_raw_different_content_different_file(self, store: FileStore):
        path_a = store.save_raw(b"%PDF-1.4 content A", kind="pdf")
        path_b = store.save_raw(b"%PDF-1.4 content B", kind="pdf")
        assert path_a != path_b
        assert len(list(store._subdirs["raw_pdf"].iterdir())) == 2

    def test_save_raw_unknown_kind_raises(self, store: FileStore):
        with pytest.raises(ValueError, match="Unknown kind"):
            store.save_raw(b"data", kind="unknown")


# ── save_processed ────────────────────────────────────────────────

class TestSaveProcessed:

    def test_save_text(self, store: FileStore):
        content = b"cleaned text content"
        path = store.save_processed(content, category="text", name="q_001.txt")
        assert path == "data/files/processed/text/q_001.txt"
        assert store._resolve(path).read_bytes() == content

    def test_save_vlm_desc(self, store: FileStore):
        content = b"VLM description"
        path = store.save_processed(content, category="vlm_desc", name="q_001_vlm.txt")
        assert path == "data/files/processed/vlm_desc/q_001_vlm.txt"

    def test_save_processed_rejects_dotdot_path(self, store: FileStore):
        with pytest.raises(ValueError, match="Invalid filename"):
            store.save_processed(b"evil", category="text", name="../../etc/passwd")

    def test_save_processed_rejects_dotdot_slash_prefix(self, store: FileStore):
        with pytest.raises(ValueError, match="Invalid filename"):
            store.save_processed(b"evil", category="text", name="../secret.txt")

    def test_save_processed_rejects_absolute_path_name(self, store: FileStore):
        with pytest.raises(ValueError, match="Invalid filename"):
            store.save_processed(b"evil", category="text", name="/etc/passwd")

    def test_save_processed_rejects_forward_slash_in_name(self, store: FileStore):
        with pytest.raises(ValueError, match="Invalid filename"):
            store.save_processed(b"evil", category="text", name="sub/injected.txt")

    def test_save_processed_allows_double_dot_embedded(self, store: FileStore):
        """a..b.txt 不含路径分隔符，不应被误拦。"""
        path = store.save_processed(b"ok", category="text", name="a..b.txt")
        assert path == "data/files/processed/text/a..b.txt"


# ── read / read_text / exists / delete ────────────────────────────

class TestReadWrite:

    def test_read_existing(self, store: FileStore):
        path = store.save_raw(b"%PDF-1.4 test", kind="pdf")
        assert store.read(path) == b"%PDF-1.4 test"

    def test_read_missing_returns_none(self, store: FileStore):
        assert store.read("data/files/raw/pdfs/nonexistent.pdf") is None

    def test_read_text(self, store: FileStore):
        path = store.save_processed("hello world".encode(), category="text", name="test.txt")
        assert store.read_text(path) == "hello world"

    def test_read_text_missing_returns_none(self, store: FileStore):
        assert store.read_text("data/files/processed/text/nope.txt") is None

    def test_read_rejects_absolute_path(self, store: FileStore):
        with pytest.raises(ValueError):
            store.read("/etc/passwd")

    def test_exists_true(self, store: FileStore):
        path = store.save_raw(b"data", kind="pdf")
        assert store.exists(path) is True

    def test_exists_false(self, store: FileStore):
        assert store.exists("data/files/raw/pdfs/no.pdf") is False

    def test_delete_existing(self, store: FileStore):
        path = store.save_raw(b"data", kind="pdf")
        assert store.delete(path) is True
        assert not store.exists(path)

    def test_delete_missing_returns_false(self, store: FileStore):
        assert store.delete("data/files/raw/pdfs/no.pdf") is False


# ── compute_hash ──────────────────────────────────────────────────

class TestComputeHash:

    def test_hash_matches_content(self, store: FileStore):
        content = b"%PDF-1.4 content for hash"
        expected_hash = hashlib.sha256(content).hexdigest()
        path = store.save_raw(content, kind="pdf")
        assert store.compute_hash(path) == expected_hash

    def test_hash_missing_raises(self, store: FileStore):
        with pytest.raises(FileNotFoundError):
            store.compute_hash("data/files/raw/pdfs/no.pdf")


# ── list ──────────────────────────────────────────────────────────

class TestList:

    def test_list_raw_empty(self, store: FileStore):
        assert store.list_raw("pdf") == []

    def test_list_raw_sorted(self, store: FileStore):
        store.save_raw(b"%PDF-1.4 z", kind="pdf")
        store.save_raw(b"%PDF-1.4 a", kind="pdf")
        store.save_raw(b"%PDF-1.4 m", kind="pdf")
        paths = store.list_raw("pdf")
        assert paths == sorted(paths)

    def test_list_raw_by_kind(self, store: FileStore):
        store.save_raw(b"%PDF-1.4 doc", kind="pdf")
        store.save_raw(b"\xff\xd8\xff", kind="image", subdir="uploaded")
        store.save_raw(b"\xff\xd8\xff", kind="image", subdir="extracted")
        assert len(store.list_raw("pdf")) == 1
        assert len(store.list_raw("image_uploaded")) == 1
        assert len(store.list_raw("image_extracted")) == 1

    def test_list_processed(self, store: FileStore):
        store.save_processed(b"text", category="text", name="a.txt")
        store.save_processed(b"vlm", category="vlm_desc", name="b.txt")
        assert store.list_processed("text") == ["data/files/processed/text/a.txt"]
        assert store.list_processed("vlm_desc") == ["data/files/processed/vlm_desc/b.txt"]


# ── 路径安全 ──────────────────────────────────────────────────────

class TestPathSafety:

    def test_path_traversal_blocked(self, store: FileStore):
        with pytest.raises(ValueError, match="Path traversal"):
            store._resolve("../../etc/passwd")

    def test_absolute_path_traversal_blocked(self, store: FileStore):
        with pytest.raises(ValueError):
            store._resolve("/etc/passwd")


# ── 扩展名推断 ────────────────────────────────────────────────────

class TestGuessExt:

    def test_pdf_magic(self):
        assert FileStore._guess_ext(b"%PDF-1.7", "pdf") == ".pdf"

    def test_pdf_no_magic(self):
        assert FileStore._guess_ext(b"hello", "pdf") == ".bin"

    def test_jpeg_magic(self):
        assert FileStore._guess_ext(b"\xff\xd8\xff\xe0", "image") == ".jpg"

    def test_png_magic(self):
        assert FileStore._guess_ext(b"\x89PNG\r\n\x1a\n", "image") == ".png"

    def test_unknown_image(self):
        assert FileStore._guess_ext(b"unknown data", "image") == ".bin"


# ── 绝对路径 data_dir（外部存储 / 数据导入导出） ─────────────────

class TestAbsoluteDataDir:

    @staticmethod
    def _make_external_store(tmp_path: Path) -> FileStore:
        """模拟绝对路径 data_dir：文件写在 external_data/ 下，effective_root 为其父目录。

        目录布局：
            tmp_path/              ← effective_root（data_dir 的父目录）
            ├── external_data/    ← 模拟 data_dir（raw 和 processed 都在这里）
            │   ├── files/raw/
            │   └── files/processed/
        """
        external = tmp_path / "external_data"
        # base_dir = tmp_path，raw_dir 解析为 tmp_path / "data/files/raw"
        store = FileStore(base_dir=str(tmp_path))
        # 手动将 base 指向 external，模拟 data_dir = str(external) 的效果
        store.base = external.resolve()
        store._subdirs = {
            "raw_pdf":           store.base / "pdfs",
            "raw_img_uploaded":  store.base / "images" / "uploaded",
            "raw_img_extracted": store.base / "images" / "extracted",
            "processed_text":    store.base.parent / "processed" / "text",
            "processed_vlm_desc": store.base.parent / "processed" / "vlm_desc",
        }
        for d in store._subdirs.values():
            d.mkdir(parents=True, exist_ok=True)
        return store

    def test_external_data_dir_creates_subdirs(self, tmp_path: Path):
        """外部 data_dir 时，所有子目录应正确创建。"""
        store = self._make_external_store(tmp_path)
        expected_dirs = [
            "pdfs",
            "images/uploaded",
            "images/extracted",
            "../processed/text",
            "../processed/vlm_desc",
        ]
        for rel in expected_dirs:
            target = store.base / rel
            assert target.is_dir(), f"目录未创建: {target}"

    def test_external_data_dir_save_raw(self, tmp_path: Path):
        """外部 data_dir 时，save_raw 写入 external 目录。"""
        store = self._make_external_store(tmp_path)
        path = store.save_raw(b"%PDF-1.4 test pdf", kind="pdf")
        # 文件在 external_data/pdfs/ 下
        assert "pdfs/" in path
        assert path.endswith(".pdf")
        # path 是 effective_root 相对路径，用 _resolve 验证磁盘上存在
        assert store._resolve(path).exists()

    def test_external_data_dir_save_processed(self, tmp_path: Path):
        """外部 data_dir 时，save_processed 写入 processed 目录。"""
        store = self._make_external_store(tmp_path)
        path = store.save_processed(b"hello", category="text", name="test.txt")
        assert "processed/text/test.txt" in path
        # path 是 effective_root 相对路径，用 _resolve 验证磁盘上存在
        assert store._resolve(path).exists()

    def test_external_data_dir_roundtrip(self, tmp_path: Path):
        """外部 data_dir 时，write → read 路径闭环。"""
        store = self._make_external_store(tmp_path)
        path = store.save_raw(b"%PDF-1.4 roundtrip", kind="pdf")
        assert store.read(path) == b"%PDF-1.4 roundtrip"

    def test_external_data_dir_delete(self, tmp_path: Path):
        """外部 data_dir 时，delete 正常工作。"""
        store = self._make_external_store(tmp_path)
        path = store.save_raw(b"data", kind="pdf")
        assert store.delete(path) is True
        assert not store.exists(path)

    def test_external_data_dir_files_under_base(self, tmp_path: Path):
        """外部 data_dir 时，所有文件都在 external_data/ 目录树下。"""
        store = self._make_external_store(tmp_path)
        store.save_raw(b"%PDF-1.4", kind="pdf")
        store.save_raw(b"\xff\xd8\xff", kind="image", subdir="uploaded")
        store.save_processed(b"text", category="text", name="t.txt")
        store.save_processed(b"vlm", category="vlm_desc", name="v.txt")

        # 所有 raw 文件在 external_data/ 下
        raw_files = store.list_raw("pdf") + store.list_raw("image_uploaded")
        assert all("external_data/" in f for f in raw_files)
        # 所有 processed 文件也在 external_data 的同级
        proc_files = store.list_processed("text") + store.list_processed("vlm_desc")
        assert all("processed/" in f for f in proc_files)
