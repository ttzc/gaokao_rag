"""ingest_question 测试：覆盖入库 → 知识点归位 → 向量写入 → 返回值原子化。"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from src.store.db import get_shared_conn
from src.store.db.questions import (
    _questions_db,
    _schema_initialized as _questions_schema_flag,
    get_questions_db,
)
from src.store.db.topics import (
    _topics_db,
    _schema_initialized as _topics_schema_flag,
    get_topics_db,
)
from src.store.db.question_topics import (
    _question_topics_db,
    _schema_initialized as _qt_schema_flag,
    get_question_topics_db,
)
from src.store.db.files import (
    _files_db,
    _schema_initialized as _files_schema_flag,
    get_files_db,
)
from src.store.vector import get_vector_store
from src.store.file_store import FileStore, get_file_store
from src.config import config
from src.ingestion.question import ingest_question


# ── 禁用 conftest 的 autouse _reset_tables ────────────────────────
# conftest._reset_tables 持有对共享连接的引用，与本文件的连接管理冲突。
# 在模块导入时覆盖 conftest._reset_tables 为 None，
# 使 pytest 不将其作为 fixture 加载。
import tests.conftest as _conftest_mod  # noqa: E402
_conftest_mod._reset_tables = None  # type: ignore[attr-defined]


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture()
def _fresh_singletons(monkeypatch, tmp_path: Path):
    """重置所有单例并指向临时 SQLite + 临时 Chroma 目录。

    每个测试独立数据：
    - SQLite：通过 monkeypatch StoreConfig._sqlite_path 指向 tmp_path 下的 db
    - Chroma：通过注入 FakeEmbeddings + 独立 persist_dir
    """
    # 1. 清空所有单例（不关闭连接——conftest._reset_tables 可能持有同一连接引用）
    global _questions_db, _topics_db, _question_topics_db, _files_db
    _questions_db = None
    _topics_db = None
    _question_topics_db = None
    _files_db = None

    # 2. 清空 schema 初始化标志（确保新连接重新建表）
    _questions_schema_flag.clear()
    _topics_schema_flag.clear()
    _qt_schema_flag.clear()
    _files_schema_flag.clear()

    # 3. 临时 SQLite：patch data_dir 使 sqlite_path property 派生出临时库路径，
    #    （不能用 _sqlite_path —— StoreConfig 只有 sqlite_path property，patch 不存在的属性不会生效，
    #     会回退到真实 data/gaokao.db 并清空真实表，造成不可逆数据风险）
    monkeypatch.setattr(config.store, "data_dir", str(tmp_path))

    # 4. 临时 Chroma 目录
    tmp_chroma = str(tmp_path / "chroma")
    Path(tmp_chroma).mkdir(parents=True, exist_ok=True)

    # 5. 创建指向 temp DB 的连接（让 conftest 的 _reset_tables 也能用）
    conn = get_shared_conn()

    # 6. 直接设置 vector_store 模块的 _instance（绕过 get_vector_store() 单例）
    from langchain_core.embeddings import FakeEmbeddings
    from src.store.vector.vector_store import VectorStore

    fake_emb = FakeEmbeddings(size=4)
    vs = VectorStore(
        collection_name="test_gaokao",
        persist_dir=tmp_chroma,
        expected_dim=4,
        embedding_function=fake_emb,
    )
    v_mod = sys.modules["src.store.vector.vector_store"]
    setattr(v_mod, "_instance", vs)

    yield

    # teardown：清空数据
    # 注意：不关闭共享连接——conftest._reset_tables 持有对同一连接的引用，
    # 此处关闭会导致 conftest teardown 报 "Cannot operate on a closed database"。
    # 连接生命周期由 pytest 进程退出时统一回收。
    try:
        c = get_shared_conn()
        for table in ("question_topics", "topics", "questions", "files"):
            try:
                c.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
        c.commit()
    except Exception:
        pass
    _questions_db = None
    _topics_db = None
    _question_topics_db = None
    _files_db = None
    if v_mod is not None:
        setattr(v_mod, "_instance", None)


@pytest.fixture()
def store(_fresh_singletons) -> FileStore:
    """基于临时目录的 FileStore 实例。"""
    return get_file_store()


# ── 基础入库 ────────────────────────────────────────────────────────

class TestBasicIngest:

    def test_returns_question_id_and_doc_id(self, _fresh_singletons):
        result = ingest_question(
            question_text="已知函数 f(x) = x² + 2x - 3，求 f(x) 的最小值。",
            answer_text="最小值为 -4。",
            analysis_text="配方法：f(x) = (x+1)² - 4。",
            question_type="解答题",
        )
        assert "question_id" in result
        assert "doc_id" in result
        assert isinstance(result["question_id"], int)
        assert result["question_id"] > 0
        assert result["doc_id"] == f"q_{result['question_id']}"

    def test_questions_table_has_one_row(self, _fresh_singletons):
        ingest_question(
            question_text="计算 1+1=?",
            answer_text="2",
            question_type="填空题",
        )
        db = get_questions_db()
        rows = db.list_all()
        assert len(rows) == 1
        assert rows[0]["content_text"] == "计算 1+1=?"
        assert rows[0]["answer_text"] == "2"

    def test_chroma_count_increments(self, _fresh_singletons):
        vs = get_vector_store()
        assert vs.count() == 0
        ingest_question(
            question_text="一道测试题",
            question_type="单选题",
        )
        assert vs.count() == 1

    def test_no_error_id_in_return(self, _fresh_singletons):
        """验证返回值只有 question_id/doc_id，无 error_id（原子化、零 errors 依赖）。"""
        result = ingest_question(
            question_text="题目",
            question_type="单选题",
        )
        assert set(result.keys()) == {"question_id", "doc_id"}


# ── 知识点归位 ──────────────────────────────────────────────────────

class TestTopicResolution:

    def test_existing_topic_reused_no_new_row(self, _fresh_singletons):
        """传入已存在的 topic 名 → 复用不新建（topics 表行数不变）。"""
        topics_db = get_topics_db()
        topics_db.create(name="椭圆")

        initial_count = len(topics_db.list_all())
        ingest_question(
            question_text="求椭圆的离心率。",
            question_type="解答题",
            topic_names=["椭圆"],
        )
        assert len(topics_db.list_all()) == initial_count

    def test_new_topic_created(self, _fresh_singletons):
        topics_db = get_topics_db()
        assert topics_db.list_all() == []

        ingest_question(
            question_text="题目",
            question_type="单选题",
            topic_names=["新知识点"],
        )
        rows = topics_db.list_all()
        assert len(rows) == 1
        assert rows[0]["name"] == "新知识点"

    def test_question_topics_populated(self, _fresh_singletons):
        ingest_question(
            question_text="题目",
            question_type="单选题",
            topic_names=["导数", "极限"],
        )
        qt_db = get_question_topics_db()
        qid = get_questions_db().list_all()[0]["id"]
        rows = qt_db.get_by_question(qid)
        names = {r["topic_name"] for r in rows}
        assert names == {"导数", "极限"}

    def test_primary_is_first_topic(self, _fresh_singletons):
        ingest_question(
            question_text="题目",
            question_type="单选题",
            topic_names=["主要知识点", "次要知识点"],
        )
        qid = get_questions_db().list_all()[0]["id"]
        qt_db = get_question_topics_db()
        rows = qt_db.get_by_question(qid)
        primary = [r for r in rows if r["is_primary"] == 1]
        assert len(primary) == 1
        assert primary[0]["topic_name"] == "主要知识点"

    def test_mixed_existing_and_new_topics(self, _fresh_singletons):
        get_topics_db().create(name="已有知识点")
        ingest_question(
            question_text="题目",
            question_type="单选题",
            topic_names=["已有知识点", "新建知识点"],
        )
        rows = get_topics_db().list_all()
        names = {r["name"] for r in rows}
        assert names == {"已有知识点", "新建知识点"}


# ── raw_file_path=None 不报错 ────────────────────────────────────────

class TestNoRawFile:

    def test_ingest_without_raw_file(self, _fresh_singletons):
        """raw_file_path=None 不报错，file_id=None，题目正常入库。"""
        result = ingest_question(
            question_text="拍照上传的题目",
            question_type="填空题",
            raw_file_path=None,
        )
        assert result["question_id"] > 0
        row = get_questions_db().get_by_id(result["question_id"])
        assert row["file_id"] is None


# ── 向量层 metadata ──────────────────────────────────────────────────

class TestVectorMetadata:

    def test_metadata_fields(self, _fresh_singletons):
        result = ingest_question(
            question_text="一道数学题",
            answer_text="答案",
            analysis_text="解析",
            subject="数学",
            source_type="exam",
            question_type="解答题",
            exam_regions=["深圳", "全国一卷"],
            exam_year=2026,
            topic_names=["椭圆"],
            image_file_ids=[1, 2],
        )
        vs = get_vector_store()
        doc = vs.get(result["doc_id"])
        assert doc is not None
        meta = doc["metadata"]
        assert meta["doc_type"] == "question"
        assert meta["subject"] == "数学"
        assert meta["source_type"] == "exam"
        assert meta["exam_year"] == 2026
        assert meta["question_type"] == "解答题"
        assert meta["has_image"] is True
        assert "椭圆" in meta["topic_tags"]

    def test_no_image_has_image_false(self, _fresh_singletons):
        result = ingest_question(
            question_text="无图题",
            question_type="单选题",
        )
        doc = get_vector_store().get(result["doc_id"])
        assert doc["metadata"]["has_image"] is False


# ── 全流程端到端 ────────────────────────────────────────────────────

class TestEndToEnd:

    def test_full_ingest_with_file_and_topics(self, _fresh_singletons, tmp_path: Path):
        """端到端：注册文件 → 入库题目 → 知识点关联 → 向量写入。"""
        # 1. 注册源文件
        files_db = get_files_db()
        file_id = files_db.register(
            file_path="data/files/raw/pdfs/exam.pdf",
            sha256="a" * 64,
            size=1024,
            kind="pdf",
            title="2026 模拟试卷",
        )

        # 2. 摄入题目
        result = ingest_question(
            question_text="已知函数 f(x) = x³ - 3x，求极值。",
            answer_text="极大值 2，极小值 -2。",
            analysis_text="求导 f'(x) = 3x² - 3 = 0 → x = ±1。",
            source_type="exam",
            question_type="解答题",
            raw_file_path="data/files/raw/pdfs/exam.pdf",
            exam_regions=["全国"],
            exam_year=2026,
            question_number="第10题",
            topic_names=["导数", "极值"],
        )

        # 3. 验证 SQLite
        q_row = get_questions_db().get_by_id(result["question_id"])
        assert q_row["file_id"] == file_id
        assert q_row["source_type"] == "exam"
        assert q_row["exam_year"] == 2026

        # 4. 验证知识点关联
        qt_db = get_question_topics_db()
        qid = result["question_id"]
        topic_rows = qt_db.get_by_question(qid)
        assert len(topic_rows) == 2

        # 5. 验证 Chroma
        vs = get_vector_store()
        assert vs.count() == 1
        chroma_doc = vs.get(result["doc_id"])
        assert chroma_doc is not None
        assert "极值" in chroma_doc["metadata"]["topic_tags"]
        assert chroma_doc["metadata"]["title"] == "2026 模拟试卷"

    def test_multiple_questions_independent(self, _fresh_singletons):
        """多题入库互不干扰，各自返回独立 id。"""
        r1 = ingest_question(
            question_text="题 A",
            question_type="单选题",
            topic_names=["代数"],
        )
        r2 = ingest_question(
            question_text="题 B",
            question_type="填空题",
            topic_names=["几何"],
        )
        assert r1["question_id"] != r2["question_id"]
        assert r1["doc_id"] == f"q_{r1['question_id']}"
        assert r2["doc_id"] == f"q_{r2['question_id']}"

        assert get_questions_db().count() == 2
        assert get_vector_store().count() == 2
