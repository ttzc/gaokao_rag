"""原子化单题摄入测试：覆盖 ingest_question() 四层封装流程。

测试策略：
- 镜像 tests/test_store_file.py 的隔离手法：临时 sqlite + 给 VectorStore 注入 FakeEmbeddings
- VectorStore.__init__ 接受 embedding_function 参数，测试场景传 FakeEmbeddings
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.store.db.files import get_files_db
from src.store.db.questions import get_questions_db
from src.store.db.question_topics import get_question_topics_db
from src.store.db.topics import get_topics_db
from src.store.vector.vector_store import VectorStore
from src.ingestion.question import ingest_question


# ── Fake Embeddings（用于测试隔离）───────────────────────────────────

class FakeEmbeddings:
    """伪嵌入模型：返回固定维度的零向量，避免真实 API 调用。"""

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dimension for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * self.dimension


# ── Fixture：临时 SQLite + 隔离的 VectorStore ─────────────────────

@pytest.fixture
def isolated_vector_store(tmp_path: Path) -> VectorStore:
    """基于临时目录的 VectorStore 实例，注入 FakeEmbeddings。"""
    chroma_dir = tmp_path / "chroma_db"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    # 直接构造 VectorStore，不使用 get_vector_store() 单例
    return VectorStore(
        collection_name="test_gaokao",
        persist_dir=str(chroma_dir),
        expected_dim=1024,
        embedding_function=FakeEmbeddings(dimension=1024),
    )


@pytest.fixture
def isolated_file_store(tmp_path: Path):
    """基于临时目录的 FileStore 实例（用于 raw_file_path 测试）。"""
    from src.store.file_store import FileStore
    return FileStore(base_dir=str(tmp_path))


# ── 辅助：为 ingest_question 注入隔离的 vector_store ───────────────

@pytest.fixture(autouse=True)
def _patch_get_vector_store(monkeypatch, isolated_vector_store: VectorStore):
    """将 get_vector_store() 单例替换为隔离的 VectorStore 实例。"""
    import src.store.vector.vector_store as vs_module
    monkeypatch.setattr(vs_module, "_instance", isolated_vector_store)
    
    def mock_get_vector_store() -> VectorStore:
        return isolated_vector_store
    
    monkeypatch.setattr(vs_module, "get_vector_store", mock_get_vector_store)


# ── 用例 1：基本入库 → questions 有 1 行、question_topics 有行、Chroma count+1 ──

class TestIngestQuestionBasic:

    def test_basic_ingest_returns_question_id_and_doc_id(
        self, isolated_vector_store: VectorStore, isolated_file_store
    ):
        """用例 (1)：入库 → questions 有 1 行、question_topics 有行、Chroma count+1。"""
        # 先初始化 tables（通过访问任意 DB 类触发 schema 初始化）
        get_questions_db()
        
        result = ingest_question(
            question_text="已知函数 f(x) = x^2 + 1，求最小值。",
            answer_text="1",
            analysis_text="当 x=0 时取得最小值。",
            subject="数学",
            source_type="exam",
            question_type="填空题",
            exam_year=2026,
            topic_names=["函数最值", "二次函数"],
            vlm_descriptions=None,
        )

        # 返回值只有 question_id/doc_id 两个键
        assert set(result.keys()) == {"question_id", "doc_id"}
        assert isinstance(result["question_id"], int)
        assert result["doc_id"] == f"q_{result['question_id']}"

        # questions 表有 1 行
        qdb = get_questions_db()
        row = qdb.get_by_id(result["question_id"])
        assert row is not None
        assert row["content_text"] == "已知函数 f(x) = x^2 + 1，求最小值。"
        assert row["answer_text"] == "1"
        assert row["analysis_text"] == "当 x=0 时取得最小值。"
        assert row["subject"] == "数学"
        assert row["source_type"] == "exam"
        assert row["question_type"] == "填空题"
        assert row["exam_year"] == 2026

        # question_topics 表有行
        qt_db = get_question_topics_db()
        qt_rows = qt_db.get_by_question(result["question_id"])
        assert len(qt_rows) == 2
        topic_names = {r["topic_name"] for r in qt_rows}
        assert topic_names == {"函数最值", "二次函数"}

        # Chroma count+1
        assert isolated_vector_store.count() == 1
        doc = isolated_vector_store.get(result["doc_id"])
        assert doc is not None
        assert doc["doc_id"] == result["doc_id"]
        assert "x^2 + 1" in doc["text"]
        assert doc["metadata"]["subject"] == "数学"
        assert doc["metadata"]["topic_tags"] == ["函数最值", "二次函数"]


# ── 用例 2：topic 归位：传入已存在的 topic 名 → 复用不新建 ──

class TestTopicResolution:

    def test_reuse_existing_topic_does_not_create_duplicate(
        self, isolated_vector_store: VectorStore, isolated_file_store
    ):
        """用例 (2)：topic 归位：传入已存在的 topic 名 → 复用不新建（topics 表行数不变）。"""
        # 预先创建一个知识点
        topics_db = get_topics_db()
        topics_db.create("椭圆", aliases=["椭圆定义"])
        initial_count = len(topics_db.list_all())

        # 第一次摄入使用 "椭圆"
        result1 = ingest_question(
            question_text="椭圆离心率 e 的取值范围是？",
            topic_names=["椭圆"],
        )

        # topics 表只增加 0 行（复用已有）
        after_first_count = len(topics_db.list_all())
        assert after_first_count == initial_count

        # 第二次摄入也使用 "椭圆"
        result2 = ingest_question(
            question_text="椭圆标准方程是什么？",
            topic_names=["椭圆"],
        )

        # topics 表仍然只增加 0 行
        after_second_count = len(topics_db.list_all())
        assert after_second_count == initial_count

        # 两道题都关联到同一个知识点
        qt_db = get_question_topics_db()
        qt1 = qt_db.get_by_question(result1["question_id"])
        qt2 = qt_db.get_by_question(result2["question_id"])
        assert qt1[0]["topic_name"] == "椭圆"
        assert qt2[0]["topic_name"] == "椭圆"


# ── 用例 3：raw_file_path=None 不报错 ──

class TestRawFilePathNone:

    def test_no_raw_file_path_works_fine(
        self, isolated_vector_store: VectorStore, isolated_file_store
    ):
        """用例 (3)：raw_file_path=None 不报错（file_id=None，题目正常入库）。"""
        result = ingest_question(
            question_text="无源文件的单题拍照。",
            raw_file_path=None,
            topic_names=[],
        )

        assert result["question_id"] > 0
        assert result["doc_id"] == f"q_{result['question_id']}"

        qdb = get_questions_db()
        row = qdb.get_by_id(result["question_id"])
        assert row is not None
        assert row["file_id"] is None
        assert row["content_text"] == "无源文件的单题拍照。"


# ── 用例 4：返回值只有 question_id/doc_id 两个键，无 error_id ──

class TestReturnValueShape:

    def test_return_value_has_only_two_keys(
        self, isolated_vector_store: VectorStore, isolated_file_store
    ):
        """用例 (4)：返回值只有 question_id/doc_id 两个键，无 error_id（验证原子化、零 errors 依赖）。"""
        result = ingest_question(
            question_text="验证返回值形状。",
            answer_text="",
            analysis_text="",
            topic_names=[],
        )

        # 严格验证返回值只有两个键
        assert len(result) == 2
        assert "question_id" in result
        assert "doc_id" in result
        assert "error_id" not in result
        assert "errors" not in result


# ── 用例 5：文件层 lookup（raw_file_path 给定且存在）──

class TestFileLayerLookup:

    def test_raw_file_path_lookup_success(
        self, isolated_vector_store: VectorStore, isolated_file_store
    ):
        """raw_file_path 给定且 files 表存在该路径 → file_id 正确关联。"""
        # 先注册一个文件
        content = b"%PDF-1.4 fake pdf"
        rel_path = isolated_file_store.save_raw(content, kind="pdf")
        files_db = get_files_db()
        file_id = files_db.register(
            file_path=rel_path,
            sha256="abc123",
            size=len(content),
            kind="pdf",
            title="测试试卷",
        )

        # 摄入题目时使用该 raw_file_path
        result = ingest_question(
            question_text="关联源文件的题目。",
            raw_file_path=rel_path,
            topic_names=[],
        )

        qdb = get_questions_db()
        row = qdb.get_by_id(result["question_id"])
        assert row is not None
        assert row["file_id"] == file_id

        # metadata title 应使用文件 title
        doc = isolated_vector_store.get(result["doc_id"])
        assert doc is not None
        assert doc["metadata"]["title"] == "测试试卷"

    def test_raw_file_path_not_found_file_id_is_none(
        self, isolated_vector_store: VectorStore, isolated_file_store
    ):
        """raw_file_path 给定但 files 表不存在该路径 → file_id=None。"""
        result = ingest_question(
            question_text="文件路径不存在的题目。",
            raw_file_path="data/files/raw/pdfs/nonexistent.pdf",
            topic_names=[],
        )

        qdb = get_questions_db()
        row = qdb.get_by_id(result["question_id"])
        assert row is not None
        assert row["file_id"] is None
