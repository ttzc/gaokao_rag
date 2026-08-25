"""QuestionsDB 测试：覆盖 insert / 查询 / 过滤 / 更新 / 删除 / doc_id 桥接 / JSON 字段。

依赖 conftest._reset_state（每测试前清空业务表 + 重置单例），测试之间无顺序依赖。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.store.db.questions import QuestionsDB, _make_doc_id, get_questions_db


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture()
def db() -> QuestionsDB:
    """QuestionsDB 实例（共享连接，数据由 conftest 每测试前清空）。"""
    return get_questions_db()


@pytest.fixture()
def sample_question(db: QuestionsDB) -> int:
    """预插入一条题目记录，返回 question_id。"""
    return db.insert(
        source_type="exam",
        subject="数学",
        content_text="已知函数 f(x) = x² + 2x - 3，求 f(x) 的最小值。",
        question_type="解答题",
        exam_regions=["深圳", "广东", "全国一卷"],
        exam_year=2026,
        exam_month=3,
        question_number="第15题",
        answer_text="f(x) = (x+1)² - 4，最小值为 -4。",
        analysis_text="配方法：f(x) = x² + 2x - 3 = (x+1)² - 4。",
        image_file_ids=[10, 11],
    )


@pytest.fixture()
def sample_question_simple(db: QuestionsDB) -> int:
    """预插入一条最小字段题目（仅必填项），返回 question_id。"""
    return db.insert(
        source_type="homework",
        subject="数学",
        content_text="计算 1+1=？",
        question_type="填空题",
    )


# ── _make_doc_id ────────────────────────────────────────────────────

class TestMakeDocId:

    def test_format_small_id(self):
        assert _make_doc_id(1) == "q_1"

    def test_format_large_id(self):
        assert _make_doc_id(42) == "q_42"

    def test_format_zero(self):
        assert _make_doc_id(0) == "q_0"

    def test_idempotent(self):
        """同 id 恒生成同 doc_id。"""
        assert _make_doc_id(7) == _make_doc_id(7)

    def test_two_segment_format(self):
        """doc_id 为两段式（无后缀）。"""
        assert _make_doc_id(99) == "q_99"
        # q_ 后无下划线
        assert "_" not in _make_doc_id(99)[2:]


# ── 初始化 ──────────────────────────────────────────────────────────

class TestInit:

    def test_creates_table(self, db: QuestionsDB):
        conn = db._connect()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='questions'"
        ).fetchone()
        assert row is not None

    def test_creates_indexes(self, db: QuestionsDB):
        conn = db._connect()
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='questions'"
        ).fetchall()
        index_names = {r["name"] for r in indexes}
        assert "idx_questions_source" in index_names
        assert "idx_questions_subject" in index_names
        assert "idx_questions_exam" in index_names
        assert "idx_questions_type" in index_names

    def test_idempotent_init(self, db: QuestionsDB):
        """两次 _connect 不报错（IF NOT EXISTS 幂等）。"""
        db._connect()
        db._connect()  # 第二次应静默通过


# ── insert ──────────────────────────────────────────────────────────

class TestInsert:

    def test_returns_int_id(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题目文本",
            question_type="单选题",
        )
        assert type(qid) is int
        assert qid > 0
        # doc_id 自动生成（两段式）
        row = db.get_by_id(qid)
        assert row["doc_id"] == f"q_{qid}"

    def test_stores_required_fields(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题目文本",
            question_type="单选题",
        )
        row = db.get_by_id(qid)
        # doc_id 两段式：q_{id}
        assert row["doc_id"] == f"q_{qid}"
        assert row["source_type"] == "exam"
        assert row["subject"] == "数学"
        assert row["content_text"] == "题目文本"
        assert row["question_type"] == "单选题"

    def test_stores_optional_fields(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题目文本",
            question_type="解答题",
            exam_regions=["深圳", "广东"],
            exam_year=2026,
            exam_month=3,
            question_number="第15题",
            answer_text="答案",
            analysis_text="解析",
            image_file_ids=[1, 2, 3],
        )
        row = db.get_by_id(qid)
        assert row["doc_id"] == f"q_{qid}"
        assert row["source_type"] == "exam"
        assert row["subject"] == "数学"
        assert json.loads(row["exam_regions"]) == ["深圳", "广东"]
        assert row["exam_year"] == 2026
        assert row["exam_month"] == 3
        assert row["question_number"] == "第15题"
        assert row["answer_text"] == "答案"
        assert row["analysis_text"] == "解析"
        assert json.loads(row["image_file_ids"]) == [1, 2, 3]
        assert row["file_id"] is None  # 未传 file_id，默认 NULL

    def test_sets_created_at(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题目文本",
            question_type="单选题",
        )
        row = db.get_by_id(qid)
        assert row["created_at"] is not None
        assert "202" in row["created_at"]  # 时间戳格式

    def test_nullable_fields_default_to_null(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题目文本",
            question_type="单选题",
        )
        row = db.get_by_id(qid)
        assert row["file_id"] is None
        assert row["exam_regions"] is None
        assert row["exam_year"] is None
        assert row["exam_month"] is None
        assert row["question_number"] is None
        assert row["answer_text"] is None
        assert row["analysis_text"] is None
        assert row["image_file_ids"] is None

    def test_doc_id_uniqueness(self, db: QuestionsDB):
        """重复 doc_id 会触发 UNIQUE 约束报错。"""
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="第一题",
            question_type="单选题",
        )
        doc_id = _make_doc_id(qid)
        # 直接 INSERT 同 doc_id 触发 UNIQUE 约束
        conn = db._connect()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint"):
            conn.execute(
                "INSERT INTO questions (doc_id, source_type, subject, content_text, question_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (doc_id, "exam", "物理", "第二题（同 doc_id）", "单选题"),
            )
            conn.commit()

    def test_insert_without_file_id(self, db: QuestionsDB):
        """file_id 可空——作业/错题不一定有 files 记录。"""
        qid = db.insert(
            source_type="error_book",
            subject="数学",
            content_text="错题内容",
            question_type="填空题",
        )
        row = db.get_by_id(qid)
        assert row["file_id"] is None
        assert row["source_type"] == "error_book"

    def test_insert_exam_regions_empty_list(self, db: QuestionsDB):
        """空列表 exam_regions 存为 NULL（falsy 值序列化为 None）。"""
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题目",
            question_type="单选题",
            exam_regions=[],
        )
        row = db.get_by_id(qid)
        assert row["exam_regions"] is None

    def test_insert_image_file_ids_empty_list(self, db: QuestionsDB):
        """空列表 image_file_ids 存为 NULL。"""
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="无图题目",
            question_type="单选题",
            image_file_ids=[],
        )
        row = db.get_by_id(qid)
        assert row["image_file_ids"] is None


# ── 单条查询 ────────────────────────────────────────────────────────

class TestQuery:

    def test_get_by_id_existing(self, db: QuestionsDB, sample_question: int):
        row = db.get_by_id(sample_question)
        assert row is not None
        assert row["id"] == sample_question

    def test_get_by_id_missing(self, db: QuestionsDB):
        assert db.get_by_id(9999) is None

    def test_get_by_id_has_all_columns(self, db: QuestionsDB, sample_question: int):
        row = db.get_by_id(sample_question)
        expected = {
            "id", "doc_id", "source_type", "subject", "file_id",
            "exam_regions", "exam_year", "exam_month", "question_number",
            "question_type", "content_text", "answer_text", "analysis_text",
            "image_file_ids", "created_at",
        }
        assert expected.issubset(row.keys())

    def test_get_by_doc_id_existing(self, db: QuestionsDB, sample_question: int):
        # sample_question fixture 自动生成 doc_id，通过 _make_doc_id 计算
        expected_doc_id = _make_doc_id(sample_question)
        row = db.get_by_doc_id(expected_doc_id)
        assert row is not None
        assert row["id"] == sample_question

    def test_get_by_doc_id_missing(self, db: QuestionsDB):
        assert db.get_by_doc_id("q_9999") is None

    def test_get_by_doc_id_bridge(self, db: QuestionsDB):
        """doc_id 是 SQLite ↔ Chroma 的桥，格式需一致。"""
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="桥接测试",
            question_type="单选题",
        )
        doc_id = _make_doc_id(qid)
        row = db.get_by_doc_id(doc_id)
        assert row is not None
        assert row["id"] == qid
        assert row["doc_id"] == doc_id  # 两段式

    def test_get_by_file_id_no_file(self, db: QuestionsDB, sample_question: int):
        """题目无 file_id 时，get_by_file_id 不返回该题。"""
        row = db.get_by_id(sample_question)
        assert row["file_id"] is None
        assert db.get_by_file_id(1) == []

    def test_get_by_file_id_with_file(self, db: QuestionsDB):
        """有 file_id 的题目通过 get_by_file_id 正确返回。"""
        from src.store.db.files import get_files_db
        file_id = get_files_db().register(
            file_path="data/files/raw/pdfs/exam.pdf",
            sha256="f" * 64,
            size=2048,
            kind="pdf",
        )
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="试卷题目",
            question_type="单选题",
            file_id=file_id,
        )
        rows = db.get_by_file_id(file_id)
        assert len(rows) == 1
        assert rows[0]["id"] == qid
        assert rows[0]["file_id"] == file_id

    def test_get_by_file_id_multiple(self, db: QuestionsDB):
        """无 file_id 的题目不属任何试卷，get_by_file_id 恒空。"""
        db.insert(source_type="exam", subject="数学", content_text="题1", question_type="单选题")
        db.insert(source_type="exam", subject="数学", content_text="题2", question_type="单选题")
        # 无 file_id 的题目不会被 get_by_file_id 匹配
        assert db.get_by_file_id(99) == []

    def test_get_by_file_id_empty(self, db: QuestionsDB):
        rows = db.get_by_file_id(9999)
        assert rows == []


# ── 列表查询 ────────────────────────────────────────────────────────

class TestList:

    def test_list_all_empty(self, db: QuestionsDB):
        assert db.list_all() == []

    def test_list_all_sorted_by_id(self, db: QuestionsDB):
        db.insert(source_type="exam", subject="数学", content_text="A", question_type="单选题")
        db.insert(source_type="exam", subject="数学", content_text="B", question_type="单选题")
        db.insert(source_type="exam", subject="数学", content_text="C", question_type="单选题")
        ids = [r["id"] for r in db.list_all()]
        assert ids == sorted(ids)

    def test_list_all_filter_by_subject(self, db: QuestionsDB):
        db.insert(source_type="exam", subject="数学", content_text="M", question_type="单选题")
        db.insert(source_type="exam", subject="物理", content_text="P", question_type="单选题")
        rows = db.list_all(subject="数学")
        assert len(rows) == 1
        assert rows[0]["subject"] == "数学"

    def test_list_all_filter_by_source_type(self, db: QuestionsDB):
        db.insert(source_type="exam", subject="数学", content_text="E", question_type="单选题")
        db.insert(source_type="homework", subject="数学", content_text="H", question_type="单选题")
        rows = db.list_all(source_type="exam")
        assert len(rows) == 1
        assert rows[0]["source_type"] == "exam"

    def test_list_all_filter_by_exam_year(self, db: QuestionsDB):
        db.insert(source_type="exam", subject="数学", content_text="25", question_type="单选题", exam_year=2025)
        db.insert(source_type="exam", subject="数学", content_text="26", question_type="单选题", exam_year=2026)
        rows = db.list_all(exam_year=2026)
        assert len(rows) == 1
        assert rows[0]["exam_year"] == 2026

    def test_list_all_filter_by_question_type(self, db: QuestionsDB):
        db.insert(source_type="exam", subject="数学", content_text="S", question_type="单选题")
        db.insert(source_type="exam", subject="数学", content_text="S", question_type="解答题")
        rows = db.list_all(question_type="解答题")
        assert len(rows) == 1
        assert rows[0]["question_type"] == "解答题"

    def test_list_all_combined_filters(self, db: QuestionsDB):
        q_match = db.insert(source_type="exam", subject="数学", content_text="M", question_type="解答题", exam_year=2026)
        db.insert(source_type="exam", subject="数学", content_text="O", question_type="单选题", exam_year=2026)
        db.insert(source_type="exam", subject="物理", content_text="P", question_type="解答题", exam_year=2026)
        rows = db.list_all(subject="数学", question_type="解答题", exam_year=2026)
        assert len(rows) == 1
        assert rows[0]["id"] == q_match

    def test_count_empty(self, db: QuestionsDB):
        assert db.count() == 0

    def test_count_all(self, db: QuestionsDB):
        db.insert(source_type="exam", subject="数学", content_text="A", question_type="单选题")
        db.insert(source_type="exam", subject="物理", content_text="B", question_type="单选题")
        assert db.count() == 2

    def test_count_by_subject(self, db: QuestionsDB):
        db.insert(source_type="exam", subject="数学", content_text="A", question_type="单选题")
        db.insert(source_type="exam", subject="物理", content_text="B", question_type="单选题")
        assert db.count(subject="数学") == 1
        assert db.count(subject="物理") == 1
        assert db.count(subject="化学") == 0

    def test_count_by_source_type(self, db: QuestionsDB):
        db.insert(source_type="exam", subject="数学", content_text="A", question_type="单选题")
        db.insert(source_type="homework", subject="数学", content_text="B", question_type="单选题")
        assert db.count(source_type="exam") == 1
        assert db.count(source_type="homework") == 1


# ── 更新 ────────────────────────────────────────────────────────────

class TestUpdate:

    def test_update_single_field(self, db: QuestionsDB, sample_question: int):
        db.update(sample_question, answer_text="新答案")
        row = db.get_by_id(sample_question)
        assert row["answer_text"] == "新答案"

    def test_update_multiple_fields(self, db: QuestionsDB, sample_question: int):
        db.update(
            sample_question,
            answer_text="新答案",
            analysis_text="新解析",
            question_number="第16题",
        )
        row = db.get_by_id(sample_question)
        assert row["answer_text"] == "新答案"
        assert row["analysis_text"] == "新解析"
        assert row["question_number"] == "第16题"

    def test_update_missing_raises(self, db: QuestionsDB):
        with pytest.raises(ValueError, match="不存在"):
            db.update(9999, answer_text="答案")

    def test_update_noop_when_no_fields(self, db: QuestionsDB, sample_question: int):
        """不传任何字段时应静默返回，不报错。"""
        db.update(sample_question)
        row = db.get_by_id(sample_question)
        assert row is not None  # 记录仍在

    def test_update_clear_answer_with_empty_string(self, db: QuestionsDB, sample_question: int):
        """传 "" 清空 answer_text（不是 None）。"""
        db.update(sample_question, answer_text="")
        row = db.get_by_id(sample_question)
        assert row["answer_text"] == ""

    def test_update_clear_analysis_with_empty_string(self, db: QuestionsDB, sample_question: int):
        db.update(sample_question, analysis_text="")
        row = db.get_by_id(sample_question)
        assert row["analysis_text"] == ""

    def test_update_json_exam_regions(self, db: QuestionsDB, sample_question: int):
        db.update(sample_question, exam_regions=["南昌", "江西", "全国一卷"])
        row = db.get_by_id(sample_question)
        assert json.loads(row["exam_regions"]) == ["南昌", "江西", "全国一卷"]

    def test_update_json_exam_regions_clear(self, db: QuestionsDB, sample_question: int):
        """传 [] 清空 exam_regions。"""
        db.update(sample_question, exam_regions=[])
        row = db.get_by_id(sample_question)
        assert row["exam_regions"] is None

    def test_update_json_image_file_ids(self, db: QuestionsDB, sample_question: int):
        db.update(sample_question, image_file_ids=[20, 21])
        row = db.get_by_id(sample_question)
        assert json.loads(row["image_file_ids"]) == [20, 21]

    def test_update_json_image_file_ids_clear(self, db: QuestionsDB, sample_question: int):
        db.update(sample_question, image_file_ids=[])
        row = db.get_by_id(sample_question)
        assert row["image_file_ids"] is None

    def test_update_year_and_month(self, db: QuestionsDB, sample_question: int):
        db.update(sample_question, exam_year=2025, exam_month=12)
        row = db.get_by_id(sample_question)
        assert row["exam_year"] == 2025
        assert row["exam_month"] == 12

    def test_update_question_type(self, db: QuestionsDB, sample_question: int):
        db.update(sample_question, question_type="多选题")
        row = db.get_by_id(sample_question)
        assert row["question_type"] == "多选题"

    def test_update_content_text(self, db: QuestionsDB, sample_question: int):
        original = db.get_by_id(sample_question)["content_text"]
        db.update(sample_question, content_text="更新后的题目文本")
        row = db.get_by_id(sample_question)
        assert row["content_text"] == "更新后的题目文本"
        assert row["content_text"] != original


# ── 删除 ────────────────────────────────────────────────────────────

class TestDelete:

    def test_delete_existing(self, db: QuestionsDB, sample_question: int):
        result = db.delete(sample_question)
        assert result is True
        assert db.get_by_id(sample_question) is None

    def test_delete_missing_returns_false(self, db: QuestionsDB):
        assert db.delete(9999) is False

    def test_delete_removes_from_list(self, db: QuestionsDB, sample_question: int):
        db.delete(sample_question)
        assert db.list_all() == []

    def test_delete_doc_id_orphan(self, db: QuestionsDB, sample_question: int):
        """删除后 get_by_doc_id 也查不到（桥接断掉）。"""
        db.delete(sample_question)
        assert db.get_by_doc_id(_make_doc_id(sample_question)) is None


# ── 单例 factory ───────────────────────────────────────────────────

class TestSingleton:

    def test_get_questions_db_returns_instance(self):
        db = get_questions_db()
        assert isinstance(db, QuestionsDB)

    def test_get_questions_db_is_same_instance(self):
        db1 = get_questions_db()
        db2 = get_questions_db()
        assert db1 is db2


# ── JSON 字段往返 ──────────────────────────────────────────────────

class TestJsonFields:

    def test_exam_regions_roundtrip_chinese(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题",
            question_type="单选题",
            exam_regions=["深圳", "广东", "全国一卷"],
        )
        row = db.get_by_id(qid)
        regions = json.loads(row["exam_regions"])
        assert regions == ["深圳", "广东", "全国一卷"]

    def test_exam_regions_single_level(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题",
            question_type="单选题",
            exam_regions=["南昌"],
        )
        row = db.get_by_id(qid)
        assert json.loads(row["exam_regions"]) == ["南昌"]

    def test_image_file_ids_roundtrip(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题",
            question_type="单选题",
            image_file_ids=[1, 2, 3],
        )
        row = db.get_by_id(qid)
        assert json.loads(row["image_file_ids"]) == [1, 2, 3]

    def test_image_file_ids_single(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题",
            question_type="单选题",
            image_file_ids=[42],
        )
        row = db.get_by_id(qid)
        assert json.loads(row["image_file_ids"]) == [42]

    def test_exam_regions_none_when_empty(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题",
            question_type="单选题",
            exam_regions=[],
        )
        row = db.get_by_id(qid)
        assert row["exam_regions"] is None

    def test_image_file_ids_none_when_empty(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题",
            question_type="单选题",
            image_file_ids=[],
        )
        row = db.get_by_id(qid)
        assert row["image_file_ids"] is None

    def test_exam_regions_none_when_not_provided(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题",
            question_type="单选题",
        )
        row = db.get_by_id(qid)
        assert row["exam_regions"] is None

    def test_image_file_ids_none_when_not_provided(self, db: QuestionsDB):
        qid = db.insert(
            source_type="exam",
            subject="数学",
            content_text="题",
            question_type="单选题",
        )
        row = db.get_by_id(qid)
        assert row["image_file_ids"] is None
