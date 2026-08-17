# src/store/file_store.py
# Layer 1 文件管理：原始文件（哈希命名）+ 处理后中间产物（描述性命名）。

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from trpc_agent_sdk.log import logger

from src.config import config

# 项目根目录（从本文件位置推算：src/store/file_store.py → 上溯 3 级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _raw_dir_posix() -> str:
    """返回 ``config.store.raw_dir`` 的 POSIX 格式（反斜杠统一为正斜杠）。"""
    return config.store.raw_dir.replace("\\", "/")


class FileStore:
    """管理 ``data/files/`` 下的原始文件和处理后文件。

    目录结构：

    ::

        data/files/
        ├── raw/
        │   ├── pdfs/               # 原始 PDF（sha256 哈希命名，只读源）
        │   └── images/
        │       ├── uploaded/       # 学生上传的照片/截图（QQ 上传、作业拍照等）
        │       └── extracted/      # 从 PDF 提取的插图
        └── processed/
            ├── text/               # 清洗后的文本（可重建）
            └── vlm_desc/           # VLM 图形描述缓存（可重建）

    raw 文件以内容 sha256 为文件名（天然去重），processed 文件用描述性命名。
    路径基准 = **项目根目录**（与 config.store.raw_dir 同基准，data/ 目录可整体搬迁），
    与 SQLite ``files.file_path`` 字段保持一致。

    例如 ``"data/files/raw/pdfs/3f9a2c81.pdf"``。

    读取边界 = **整个项目根目录**（``read()`` 可访问项目内任意文件，穿越防护阻止出
    项目根）。MVP 阶段不做 data/ 收窄，调用方可信。

    Attributes:
        base: ``data/files/`` 的绝对路径。
    """

    def __init__(self, base_dir: str | None = None):
        raw_dir = Path(_raw_dir_posix())

        if base_dir is not None:
            # 测试等场景：显式传入基准目录（模拟 data_dir 的根）
            effective_root = Path(base_dir).resolve()
            self.effective_root = effective_root
            self.base = (effective_root / raw_dir).resolve()
        elif raw_dir.is_absolute():
            # 绝对路径 data_dir：effective_root = data_dir 的父目录
            # 使得 raw/ 和 processed/ 都能通过 relative_to 校验
            self.effective_root = raw_dir.parent.parent.parent.resolve()
            self.base = raw_dir.resolve()
        else:
            # 相对路径：相对于项目根目录
            self.effective_root = _PROJECT_ROOT
            self.base = (_PROJECT_ROOT / raw_dir).resolve()

        # data_dir 根 = base 上溯两级（raw/ 和 processed/ 的公共父目录）
        self.data_dir = self.base.parent.parent

        # 子目录映射（self.base 已含 config.store.raw_dir，不再追加 "raw"）
        self._subdirs: dict[str, Path] = {
            "raw_pdf":           self.base / "pdfs",
            "raw_img_uploaded":  self.base / "images" / "uploaded",
            "raw_img_extracted": self.base / "images" / "extracted",
            "processed_text":    self.base.parent / "processed" / "text",
            "processed_vlm_desc": self.base.parent / "processed" / "vlm_desc",
        }
        for d in self._subdirs.values():
            d.mkdir(parents=True, exist_ok=True)

    # ── Raw 文件（哈希命名，只写一次） ──────────────────────────────

    def save_raw(
        self,
        content: bytes,
        kind: Literal["pdf", "image"],
        subdir: Literal["uploaded", "extracted"] = "uploaded",
    ) -> str:
        """保存原始文件，以内容 sha256 哈希为文件名。

        同内容自动去重：文件已存在则跳过写入，直接返回路径。

        Args:
            content: 文件原始字节。
            kind: 文件类型，目前支持 "pdf"（试卷 PDF）和 "image"（学生上传的照片/截图）。
            subdir: 图片类型时指定 "uploaded"（QQ 上传）或 "extracted"（PDF 提取插图）。

        Returns:
            相对路径 data_dir（如 ``"data/files/raw/pdfs/abc.pdf"``），
            或绝对路径（当 ``config.store.data_dir`` 为绝对路径时）。
        """
        ext = self._guess_ext(content, kind)
        digest = hashlib.sha256(content).hexdigest()
        filename = digest + ext

        if kind == "pdf":
            target = self._subdirs["raw_pdf"] / filename
        elif kind == "image":
            target = self._subdirs[f"raw_img_{subdir}"] / filename
        else:
            raise ValueError(f"Unknown kind: {kind!r}")

        if not target.exists():
            target.write_bytes(content)
            logger.info("Raw file saved: %s", self._rel(target))
        else:
            logger.debug("Raw file dedup (already exists): %s", self._rel(target))

        return self._rel(target)

    # ── Processed 文件（描述性命名，可重建） ────────────────────────

    def save_processed(
        self,
        content: bytes,
        category: Literal["text", "vlm_desc"],
        name: str,
    ) -> str:
        """保存处理后中间产物。文件已存在则直接覆盖。

        保存路径：
        - ``category="text"``     → ``data/files/processed/text/<name>``
        - ``category="vlm_desc"`` → ``data/files/processed/vlm_desc/<name>``

        Args:
            content: 文件内容。
            category: "text"（清洗文本）或 "vlm_desc"（VLM 描述）。
            name: 描述性文件名，如 ``"q_001_cleaned.txt"``。

        Returns:
            从项目根起的相对路径。
        """
        # Path(name).name 已拦截含 / 或 \ 的名称；此处精确拦截 ".." 路径组件
        # （不用 ".." in name：会误拦合法文件名如 a..b.txt）
        if Path(name).name != name or name == ".." or name.startswith(("../", "..\\")):
            raise ValueError(f"Invalid filename (path traversal): {name!r}")

        target = self._subdirs[f"processed_{category}"] / name
        target.write_bytes(content)
        logger.info("Processed file saved: %s", self._rel(target))
        return self._rel(target)

    # ── 通用读写 ───────────────────────────────────────────────────

    def read(self, relative_path: str) -> bytes | None:
        """按相对路径读取文件内容（二进制）。

        Args:
            relative_path: 从项目根起的路径，如 ``"data/files/raw/pdfs/abc.pdf"``。

        Returns:
            文件字节内容，不存在时返回 ``None``。
        """
        path = self._resolve(relative_path)
        if not path.exists():
            logger.warning("File not found: %s", relative_path)
            return None
        return path.read_bytes()

    def read_text(self, relative_path: str, encoding: str = "utf-8") -> str | None:
        """按相对路径读取文本文件。

        Args:
            relative_path: 从项目根起的路径，如 ``"data/files/processed/text/q_001_cleaned.txt"``。
            encoding: 文本编码（默认 ``"utf-8"``）。

        Returns:
            解码后的文本字符串，文件不存在时返回 ``None``。
        """
        data = self.read(relative_path)
        return data.decode(encoding) if data is not None else None

    def delete(self, relative_path: str) -> bool:
        """删除文件。

        .. warning::
            raw 文件被业务数据（questions / knowledge_notes 等）引用时
            不应直接删除。调用方应先查询 SQLite 确认无引用。

        Args:
            relative_path: 从项目根起的路径，如 ``"data/files/raw/pdfs/abc.pdf"``。
        """
        path = self._resolve(relative_path)
        if path.exists():
            path.unlink()
            logger.info("File deleted: %s", relative_path)
            return True
        logger.warning("File not found for delete: %s", relative_path)
        return False

    def exists(self, relative_path: str) -> bool:
        """检查文件是否存在。

        Args:
            relative_path: 从项目根起的路径，如 ``"data/files/raw/pdfs/abc.pdf"``。
        """
        return self._resolve(relative_path).exists()

    def compute_hash(self, relative_path: str) -> str:
        """计算文件内容的 sha256 哈希。

        Args:
            relative_path: 从项目根起的路径，如 ``"data/files/raw/pdfs/abc.pdf"``。

        Returns:
            sha256 hex digest 字符串。

        Raises:
            FileNotFoundError: 文件不存在。
        """
        data = self.read(relative_path)
        if data is None:
            raise FileNotFoundError(f"File not found: {relative_path}")
        return hashlib.sha256(data).hexdigest()

    # ── 列表 ───────────────────────────────────────────────────────

    def list_raw(self, kind: Literal["pdf", "image_uploaded", "image_extracted"] = "pdf") -> list[str]:
        """列出 raw 子目录下的所有文件（相对路径，排序）。

        Args:
            kind: 子目录类型，如 ``"pdf"``、``"image_uploaded"``、``"image_extracted"``。

        Returns:
            排序后的相对路径列表，如 ``["data/files/raw/pdfs/abc.pdf", ...]``。
        """
        key = {
            "pdf": "raw_pdf",
            "image_uploaded": "raw_img_uploaded",
            "image_extracted": "raw_img_extracted",
        }[kind]
        return sorted(self._rel(p) for p in self._subdirs[key].iterdir() if p.is_file())

    def list_processed(self, category: Literal["text", "vlm_desc"] = "text") -> list[str]:
        """列出 processed 子目录下的所有文件。

        Args:
            category: 子目录类型，``"text"``（清洗文本）或 ``"vlm_desc"``（VLM 描述）。

        Returns:
            排序后的相对路径列表，如 ``["data/files/processed/text/q_001_cleaned.txt", ...]``。
        """
        key = f"processed_{category}"
        return sorted(self._rel(p) for p in self._subdirs[key].iterdir() if p.is_file())

    # ── 内部工具 ───────────────────────────────────────────────────

    def _resolve(self, relative_path: str) -> Path:
        """将项目根相对路径解析为绝对路径，防止路径穿越攻击。

        外部存储模式下（data_dir 为绝对路径），_rel 返回的也是绝对路径，
        本方法接受绝对路径输入——但仅当它落在 ``self.data_dir`` 目录下时。
        """
        p = Path(relative_path)
        if p.is_absolute():
            # 外部存储模式：_rel 产出绝对路径，允许——但校验锚定 self.data_dir
            # （raw/ 和 processed/ 都在 data_dir 下，self.base 仅指向 raw/）
            resolved = p.resolve()
            try:
                resolved.relative_to(self.data_dir.resolve())
            except ValueError:
                raise ValueError(f"Absolute path outside store data_dir: {relative_path}")
            return resolved
        # 相对路径：_rel 产出的是项目根相对路径（含 data/files/raw 前缀），
        # 需剥离该前缀再以 self.base 为锚解析；processed 等非 raw 子树
        # 不带此前缀，直接从 effective_root 解析
        raw_prefix = _raw_dir_posix() + "/"
        if relative_path.startswith(raw_prefix):
            tail = relative_path[len(raw_prefix):]
            resolved = (self.base / tail).resolve()
        else:
            resolved = (self.effective_root / p).resolve()
        try:
            resolved.relative_to(self.effective_root.resolve())
        except ValueError:
            raise ValueError(f"Path traversal detected: {relative_path}")
        return resolved

    def _rel(self, path: Path) -> str:
        """绝对路径 → 相对路径（POSIX 格式）。

        默认返回项目根相对路径（如 ``"data/files/raw/pdfs/abc.pdf"``）。
        当 ``data_dir`` 配置为绝对路径（外部存储）时，直接返回真绝对路径
        （如 ``"/mnt/external/files/raw/pdfs/abc.pdf"``）。
        """
        # 外部存储模式：raw_dir 为绝对路径 → 返回真绝对路径
        if Path(_raw_dir_posix()).is_absolute():
            return path.as_posix()
        effective = self.effective_root.resolve()
        rel = path.relative_to(effective).as_posix()
        return rel

    @staticmethod
    def _guess_ext(content: bytes, kind: str) -> str:
        """根据 magic bytes 或类型推断扩展名。"""
        if kind == "pdf":
            return ".pdf" if content[:5] == b"%PDF-" else ".bin"
        if kind == "image":
            if content[:8] == b"\x89PNG\r\n\x1a\n":
                return ".png"
            if content[:3] == b"\xff\xd8\xff":
                return ".jpg"
            if content[:4] == b"GIF8":
                return ".gif"
            if len(content) >= 12 and content[8:12] == b"WEBP":
                return ".webp"
            if content[:2] == b"BM":
                return ".bmp"
            return ".bin"
        return ".bin"


# ── Singleton factory ─────────────────────────────────────────────

_file_store: FileStore | None = None


def get_file_store() -> FileStore:
    """返回缓存的 FileStore 单例。"""
    global _file_store
    if _file_store is None:
        _file_store = FileStore()
    return _file_store
