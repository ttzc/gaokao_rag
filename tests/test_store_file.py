"""FileStore 测试：覆盖 CRUD、哈希命名去重、路径基准（项目根相对）。"""

import hashlib
from pathlib import Path

import pytest

from src.config import config
from src.store.file_store import FileStore


@pytest.fixture()
def store(tmp_path: Path) -> FileStore:
    """基于临时目录的 FileStore 实例（_rel 仍返回项目根相对路径）。"""
    return FileStore(base_dir=str(tmp_path))


# ── 初始化 ────────────────────────────────────────────────────────

class TestInit:

    def test_base_is_project_root_relative(self, tmp_path: Path):
        store = FileStore(base_dir=str(tmp_path))
        assert store.base.name == "raw"
        assert store.base.parent.name == "files"
        assert store.base.parent.parent.name == "data"

    def test_creates_all_subdirs(self, tmp_path: Path):
        store = FileStore(base_dir=str(tmp_path))
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

    def test_idempotent_mkdir(self, tmp_path: Path):
        store = FileStore(base_dir=str(tmp_path))
        store2 = FileStore(base_dir=str(tmp_path))
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
# 真实构造：monkeypatch config.store.data_dir 为绝对路径，直接 FileStore()

class TestAbsoluteDataDir:

    def _external_dir(self, tmp_path: Path) -> str:
        """在 tmp 下创建绝对路径 data_dir，返回其字符串值。"""
        d = tmp_path / "gaokao_data"
        d.mkdir()
        return str(d)

    def test_returns_true_absolute_path_on_save(self, tmp_path: Path, monkeypatch):
        """外部存储模式下 save_raw 应返回真绝对路径（含盘符/根）。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        path = store.save_raw(b"%PDF-1.4 external pdf", kind="pdf")
        assert Path(path).is_absolute(), f"Expected absolute path, got: {path}"
        assert Path(path).is_relative_to(tmp_path), f"Path should be under tmp: {path}"

    def test_absolute_path_contains_files_raw_pdfs(self, tmp_path: Path, monkeypatch):
        """绝对路径应包含完整的 files/raw/pdfs/ 层级。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        path = store.save_raw(b"%PDF-1.4 test", kind="pdf")
        assert "files/raw/pdfs/" in path, f"Missing expected segment in: {path}"

    def test_absolute_path_file_exists_on_disk(self, tmp_path: Path, monkeypatch):
        """返回的绝对路径指向的文件真实存在于磁盘。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        content = b"%PDF-1.4 roundtrip"
        path = store.save_raw(content, kind="pdf")
        assert Path(path).exists(), f"File not found at: {path}"
        assert Path(path).read_bytes() == content

    def test_save_image_absolute_path(self, tmp_path: Path, monkeypatch):
        """外部存储模式下的图片上传也返回绝对路径。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        path = store.save_raw(jpg, kind="image", subdir="uploaded")
        assert Path(path).is_absolute()
        assert "images/uploaded/" in path

    def test_save_processed_absolute_path(self, tmp_path: Path, monkeypatch):
        """外部存储模式下的 processed 文件也返回绝对路径。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        path = store.save_processed(b"hello", category="text", name="test.txt")
        assert Path(path).is_absolute()
        assert "processed/text/test.txt" in path

    def test_list_raw_returns_absolute_paths(self, tmp_path: Path, monkeypatch):
        """外部存储模式 list_raw 返回绝对路径列表。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        store.save_raw(b"%PDF-1.4 doc", kind="pdf")
        paths = store.list_raw("pdf")
        assert len(paths) == 1
        assert Path(paths[0]).is_absolute()

    def test_delete_works_with_absolute_return(self, tmp_path: Path, monkeypatch):
        """用 save_raw 返回的绝对路径调用 delete 应正常删除。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        path = store.save_raw(b"data", kind="pdf")
        assert store.delete(path) is True
        assert not Path(path).exists()

    def test_read_works_with_absolute_return(self, tmp_path: Path, monkeypatch):
        """用 save_raw 返回的绝对路径调用 read 应正常读取。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        content = b"%PDF-1.4 readback"
        path = store.save_raw(content, kind="pdf")
        assert store.read(path) == content

    def test_processed_read_roundtrip(self, tmp_path: Path, monkeypatch):
        """外部模式下 save_processed 返回的绝对路径可被 read 消费。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        content = b"cleaned math problem text"
        path = store.save_processed(content, category="text", name="q_001.txt")
        assert Path(path).is_absolute()
        assert store.read(path) == content

    def test_processed_delete_roundtrip(self, tmp_path: Path, monkeypatch):
        """外部模式下 save_processed 返回的绝对路径可被 delete 消费。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        path = store.save_processed(b"vlm desc", category="vlm_desc", name="q_001_vlm.txt")
        assert store.delete(path) is True
        assert not Path(path).exists()

    def test_all_files_under_data_dir(self, tmp_path: Path, monkeypatch):
        """外部存储模式下所有文件都落在 data_dir 目录树下。"""
        abs_data_dir = self._external_dir(tmp_path)
        monkeypatch.setattr(config.store, "data_dir", abs_data_dir)
        store = FileStore()
        store.save_raw(b"%PDF-1.4", kind="pdf")
        store.save_raw(b"\xff\xd8\xff", kind="image", subdir="uploaded")
        store.save_processed(b"text", category="text", name="t.txt")
        store.save_processed(b"vlm", category="vlm_desc", name="v.txt")
        # 所有文件的路径都应在 data_dir 下
        for p_str in (
            store.list_raw("pdf") + store.list_raw("image_uploaded")
            + store.list_processed("text") + store.list_processed("vlm_desc")
        ):
            assert Path(p_str).is_absolute()
            assert Path(p_str).is_relative_to(abs_data_dir), \
                f"{p_str} should be under {abs_data_dir}"
