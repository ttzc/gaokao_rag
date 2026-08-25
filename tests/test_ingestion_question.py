"""ingest_question 测试：覆盖入库 → 知识点归位 → 向量写入 → 返回值原子化。

依赖 conftest._reset_state（每测试前清空 SQLite + Chroma + FileStore + 重置单例），
测试之间无顺序依赖。测试直接使用 config 真实路径（data/gaokao.db、data/chroma_db）。
"""

from __future__ import annotations

from src.store.db.files import get_files_db
from src.store.db.questions import get_questions_db
from src.store.db.question_topics import get_question_topics_db
from src.store.db.topics import get_topics_db
from src.store.vector import get_vector_store
from src.ingestion.question import ingest_question


# ── 基础入库 ────────────────────────────────────────────────────────

class TestBasicIngest:

    def test_returns_question_id_and_doc_id(self):
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

    def test_questions_table_has_one_row(self):
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

    def test_chroma_count_increments(self):
        vs = get_vector_store()
        assert vs.count() == 0
        ingest_question(
            question_text="一道测试题",
            question_type="单选题",
        )
        assert vs.count() == 1

    def test_no_error_id_in_return(self):
        """验证返回值只有 question_id/doc_id，无 error_id（原子化、零 errors 依赖）。"""
        result = ingest_question(
            question_text="题目",
            question_type="单选题",
        )
        assert set(result.keys()) == {"question_id", "doc_id"}


# ── 知识点归位 ──────────────────────────────────────────────────────

class TestTopicResolution:

    def test_existing_topic_reused_no_new_row(self):
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

    def test_new_topic_created(self):
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

    def test_question_topics_populated(self):
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

    def test_primary_is_first_topic(self):
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

    def test_mixed_existing_and_new_topics(self):
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

    def test_ingest_without_raw_file(self):
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

    def test_metadata_fields(self):
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

    def test_no_image_has_image_false(self):
        result = ingest_question(
            question_text="无图题",
            question_type="单选题",
        )
        doc = get_vector_store().get(result["doc_id"])
        assert doc["metadata"]["has_image"] is False


# ── 全流程端到端 ────────────────────────────────────────────────────

class TestEndToEnd:

    def test_full_ingest_with_file_and_topics(self):
        """端到端：注册文件 → 入库题目 → 知识点关联 → 向量写入。

        raw_file_path 用相对路径即可（ingest_question 只查 files 表 + 写 processed 文本，
        不真正读取 raw 目录文件）。
        """
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

    def test_multiple_questions_independent(self):
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
