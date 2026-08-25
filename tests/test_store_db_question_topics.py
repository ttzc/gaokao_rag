"""QuestionTopicsDB 测试：覆盖 add / add_many / 查询 / 聚合 / 删除 / 与 questions/topics 联动。

依赖 conftest._reset_state（每测试前清空业务表 + 重置单例），测试之间无顺序依赖。
"""

from __future__ import annotations

import pytest

from src.store.db.questions import get_questions_db
from src.store.db.question_topics import QuestionTopicsDB, get_question_topics_db
from src.store.db.topics import get_topics_db


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture()
def db() -> QuestionTopicsDB:
    """QuestionTopicsDB 实例（共享连接，数据由 conftest 每测试前清空）。"""
    return get_question_topics_db()


@pytest.fixture()
def sample_question() -> int:
    """预插入一条题目记录，返回 question_id。"""
    return get_questions_db().insert(
        source_type="exam",
        subject="数学",
        content_text="已知函数 f(x) = x² + 2x - 3，求 f(x) 的最小值。",
        question_type="解答题",
    )


@pytest.fixture()
def sample_question2() -> int:
    """第二条题目，用于多题关联测试。"""
    return get_questions_db().insert(
        source_type="exam",
        subject="数学",
        content_text="求椭圆 x²/a² + y²/b² = 1 的离心率。",
        question_type="解答题",
    )


@pytest.fixture()
def topic_ellipse() -> int:
    """预创建"椭圆"知识点，返回 topic_id。"""
    return get_topics_db().create(name="椭圆", aliases=["椭圆曲线"])


@pytest.fixture()
def topic_eccentricity() -> int:
    """预创建"离心率"知识点，返回 topic_id。"""
    return get_topics_db().create(name="离心率")


@pytest.fixture()
def topic_derivative() -> int:
    """预创建"导数"知识点，返回 topic_id。"""
    return get_topics_db().create(name="导数")


# ── 初始化 ──────────────────────────────────────────────────────────

class TestInit:

    def test_creates_table(self, db: QuestionTopicsDB):
        conn = db._connect()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='question_topics'"
        ).fetchone()
        assert row is not None

    def test_creates_indexes(self, db: QuestionTopicsDB):
        conn = db._connect()
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='question_topics'"
        ).fetchall()
        index_names = {r["name"] for r in indexes}
        assert "idx_qt_question" in index_names
        assert "idx_qt_topic" in index_names

    def test_composite_pk_enforced(self, db: QuestionTopicsDB, sample_question: int):
        """联合主键 (question_id, topic_name) 天然防重复。"""
        from src.store.db.topics import get_topics_db
        topic_name = "椭圆"
        get_topics_db().create(name=topic_name)
        db.add(sample_question, topic_name, is_primary=True)
        # 第二次插入同一条关联应被 IGNORE
        db.add(sample_question, topic_name, is_primary=True)
        rows = db.get_by_question(sample_question)
        assert len(rows) == 1


# ── add ─────────────────────────────────────────────────────────────

class TestAdd:

    def test_inserts_record(self, db: QuestionTopicsDB, sample_question: int, topic_ellipse: int):
        db.add(sample_question, "椭圆", is_primary=True)
        rows = db.get_by_question(sample_question)
        assert len(rows) == 1
        assert rows[0]["topic_name"] == "椭圆"
        assert rows[0]["is_primary"] == 1
        assert rows[0]["question_id"] == sample_question

    def test_default_is_primary_false(self, db: QuestionTopicsDB, sample_question: int, topic_derivative: int):
        db.add(sample_question, "导数")
        rows = db.get_by_question(sample_question)
        assert rows[0]["is_primary"] == 0

    def test_idempotent_duplicate(self, db: QuestionTopicsDB, sample_question: int, topic_ellipse: int):
        """INSERT OR IGNORE：重复插入静默跳过。"""
        db.add(sample_question, "椭圆", is_primary=True)
        db.add(sample_question, "椭圆", is_primary=True)
        rows = db.get_by_question(sample_question)
        assert len(rows) == 1

    def test_idempotent_only_one_row(self, db: QuestionTopicsDB, sample_question: int, topic_ellipse: int):
        db.add(sample_question, "椭圆", is_primary=True)
        db.add(sample_question, "椭圆", is_primary=True)
        assert db.count_by_topic("椭圆") == 1

    def test_sets_created_at(self, db: QuestionTopicsDB, sample_question: int, topic_ellipse: int):
        db.add(sample_question, "椭圆")
        rows = db.get_by_question(sample_question)
        assert rows[0]["created_at"] is not None

    def test_multiple_topics_same_question(self, db: QuestionTopicsDB, sample_question: int,
                                           topic_ellipse: int, topic_eccentricity: int, topic_derivative: int):
        db.add(sample_question, "椭圆", is_primary=True)
        db.add(sample_question, "离心率", is_primary=False)
        db.add(sample_question, "导数", is_primary=False)
        rows = db.get_by_question(sample_question)
        assert len(rows) == 3
        topic_names = {r["topic_name"] for r in rows}
        assert topic_names == {"椭圆", "离心率", "导数"}


# ── add_many ────────────────────────────────────────────────────────

class TestAddMany:

    def test_basic_batch(self, db: QuestionTopicsDB, sample_question: int):
        get_topics_db().create(name="A")
        get_topics_db().create(name="B")
        get_topics_db().create(name="C")
        db.add_many(sample_question, ["A", "B", "C"])
        rows = db.get_by_question(sample_question)
        assert len(rows) == 3

    def test_primary_index_zero(self, db: QuestionTopicsDB, sample_question: int):
        get_topics_db().create(name="X")
        get_topics_db().create(name="Y")
        db.add_many(sample_question, ["X", "Y"], primary_index=0)
        rows = db.get_by_question(sample_question)
        primary = [r for r in rows if r["is_primary"] == 1]
        assert len(primary) == 1
        assert primary[0]["topic_name"] == "X"

    def test_primary_index_middle(self, db: QuestionTopicsDB, sample_question: int):
        get_topics_db().create(name="A")
        get_topics_db().create(name="B")
        get_topics_db().create(name="C")
        db.add_many(sample_question, ["A", "B", "C"], primary_index=1)
        rows = db.get_by_question(sample_question)
        primary = [r for r in rows if r["is_primary"] == 1]
        assert len(primary) == 1
        assert primary[0]["topic_name"] == "B"

    def test_primary_index_last(self, db: QuestionTopicsDB, sample_question: int):
        get_topics_db().create(name="A")
        get_topics_db().create(name="B")
        db.add_many(sample_question, ["A", "B"], primary_index=1)
        rows = db.get_by_question(sample_question)
        primary = [r for r in rows if r["is_primary"] == 1]
        assert primary[0]["topic_name"] == "B"

    def test_primary_index_out_of_range(self, db: QuestionTopicsDB, sample_question: int):
        """超出范围的 primary_index 静默无主知识点。"""
        get_topics_db().create(name="A")
        db.add_many(sample_question, ["A"], primary_index=99)
        rows = db.get_by_question(sample_question)
        primary = [r for r in rows if r["is_primary"] == 1]
        assert len(primary) == 0

    def test_primary_index_negative(self, db: QuestionTopicsDB, sample_question: int):
        """负数索引：只有 index == 0 时 is_primary=1，负数不会命中。"""
        get_topics_db().create(name="A")
        get_topics_db().create(name="B")
        db.add_many(sample_question, ["A", "B"], primary_index=-1)
        rows = db.get_by_question(sample_question)
        primary = [r for r in rows if r["is_primary"] == 1]
        assert len(primary) == 0

    def test_empty_list_is_noop(self, db: QuestionTopicsDB, sample_question: int):
        """空列表不插入任何记录。"""
        db.add_many(sample_question, [])
        rows = db.get_by_question(sample_question)
        assert rows == []

    def test_single_topic_batch(self, db: QuestionTopicsDB, sample_question: int):
        get_topics_db().create(name="唯一知识点")
        db.add_many(sample_question, ["唯一知识点"])
        rows = db.get_by_question(sample_question)
        assert len(rows) == 1
        assert rows[0]["topic_name"] == "唯一知识点"
        assert rows[0]["is_primary"] == 1  # primary_index=0 默认

    def test_idempotent_add_many(self, db: QuestionTopicsDB, sample_question: int):
        """重复 add_many 不增加行数（INSERT OR IGNORE）。"""
        get_topics_db().create(name="A")
        get_topics_db().create(name="B")
        db.add_many(sample_question, ["A", "B"])
        db.add_many(sample_question, ["A", "B"])
        rows = db.get_by_question(sample_question)
        assert len(rows) == 2

    def test_logs_count_and_primary(self, db: QuestionTopicsDB, sample_question: int, caplog):
        """add_many 记录日志（包含 count 和 primary）。"""
        import logging
        get_topics_db().create(name="三角")
        get_topics_db().create(name="向量")
        with caplog.at_level(logging.INFO):
            db.add_many(sample_question, ["三角", "向量"], primary_index=1)
        assert "count=2" in caplog.text
        assert "向量" in caplog.text


# ── get_by_question ─────────────────────────────────────────────────

class TestGetByQuestion:

    def test_empty_question(self, db: QuestionTopicsDB):
        rows = db.get_by_question(9999)
        assert rows == []

    def test_single_topic(self, db: QuestionTopicsDB, sample_question: int, topic_ellipse: int):
        db.add(sample_question, "椭圆")
        rows = db.get_by_question(sample_question)
        assert len(rows) == 1
        assert rows[0]["topic_name"] == "椭圆"
        assert rows[0]["question_id"] == sample_question

    def test_ordered_by_created_at(self, db: QuestionTopicsDB, sample_question: int):
        get_topics_db().create(name="A")
        get_topics_db().create(name="B")
        get_topics_db().create(name="C")
        db.add_many(sample_question, ["A", "B", "C"])
        rows = db.get_by_question(sample_question)
        created_ats = [r["created_at"] for r in rows]
        assert created_ats == sorted(created_ats)

    def test_returns_all_fields(self, db: QuestionTopicsDB, sample_question: int, topic_ellipse: int):
        db.add(sample_question, "椭圆", is_primary=True)
        rows = db.get_by_question(sample_question)
        row = rows[0]
        assert "question_id" in row
        assert "topic_name" in row
        assert "is_primary" in row
        assert "created_at" in row

    def test_different_questions_isolated(self, db: QuestionTopicsDB, sample_question: int,
                                          sample_question2: int, topic_ellipse: int):
        db.add(sample_question, "椭圆")
        db.add(sample_question2, "椭圆")
        rows_q1 = db.get_by_question(sample_question)
        rows_q2 = db.get_by_question(sample_question2)
        assert len(rows_q1) == 1
        assert len(rows_q2) == 1
        assert rows_q1[0]["question_id"] == sample_question
        assert rows_q2[0]["question_id"] == sample_question2


# ── get_by_topic ────────────────────────────────────────────────────

class TestGetByTopic:

    def test_empty_topic(self, db: QuestionTopicsDB):
        rows = db.get_by_topic("不存在的知识点")
        assert rows == []

    def test_single_question(self, db: QuestionTopicsDB, sample_question: int,
                             sample_question2: int, topic_ellipse: int):
        db.add(sample_question, "椭圆")
        rows = db.get_by_topic("椭圆")
        assert len(rows) == 1
        assert rows[0]["question_id"] == sample_question

    def test_multiple_questions(self, db: QuestionTopicsDB, sample_question: int,
                                sample_question2: int, topic_ellipse: int):
        db.add(sample_question, "椭圆")
        db.add(sample_question2, "椭圆")
        rows = db.get_by_topic("椭圆")
        assert len(rows) == 2
        qids = {r["question_id"] for r in rows}
        assert qids == {sample_question, sample_question2}

    def test_ordered_by_created_at(self, db: QuestionTopicsDB, sample_question: int,
                                   sample_question2: int, topic_ellipse: int):
        db.add(sample_question, "椭圆")
        db.add(sample_question2, "椭圆")
        rows = db.get_by_topic("椭圆")
        created_ats = [r["created_at"] for r in rows]
        assert created_ats == sorted(created_ats)

    def test_topic_not_in_questions(self, db: QuestionTopicsDB, sample_question: int,
                                    topic_derivative: int, topic_eccentricity: int):
        """只关联了"导数"的题，查"离心率"应空。"""
        db.add(sample_question, "导数")
        rows = db.get_by_topic("离心率")
        assert rows == []


# ── count_by_topic ──────────────────────────────────────────────────

class TestCountByTopic:

    def test_zero_for_empty(self, db: QuestionTopicsDB):
        assert db.count_by_topic("椭圆") == 0

    def test_single(self, db: QuestionTopicsDB, sample_question: int, topic_ellipse: int):
        db.add(sample_question, "椭圆")
        assert db.count_by_topic("椭圆") == 1

    def test_multiple_questions(self, db: QuestionTopicsDB, sample_question: int,
                                sample_question2: int, topic_ellipse: int):
        db.add(sample_question, "椭圆")
        db.add(sample_question2, "椭圆")
        assert db.count_by_topic("椭圆") == 2

    def test_idempotent_count(self, db: QuestionTopicsDB, sample_question: int, topic_ellipse: int):
        """重复关联不影响计数。"""
        db.add(sample_question, "椭圆")
        db.add(sample_question, "椭圆")
        assert db.count_by_topic("椭圆") == 1

    def test_only_counts_specified_topic(self, db: QuestionTopicsDB, sample_question: int,
                                         sample_question2: int, topic_ellipse: int, topic_derivative: int):
        """不同 topic 的计数独立。"""
        db.add(sample_question, "椭圆")
        db.add(sample_question2, "导数")
        assert db.count_by_topic("椭圆") == 1
        assert db.count_by_topic("导数") == 1


# ── remove ──────────────────────────────────────────────────────────

class TestRemove:

    def test_remove_existing(self, db: QuestionTopicsDB, sample_question: int, topic_ellipse: int):
        db.add(sample_question, "椭圆")
        result = db.remove(sample_question, "椭圆")
        assert result is True
        assert db.get_by_question(sample_question) == []
        assert db.count_by_topic("椭圆") == 0

    def test_remove_missing_returns_false(self, db: QuestionTopicsDB, sample_question: int):
        result = db.remove(sample_question, "不存在知识点")
        assert result is False

    def test_remove_only_target_row(self, db: QuestionTopicsDB, sample_question: int,
                                    topic_ellipse: int, topic_eccentricity: int, topic_derivative: int):
        """删除一个关联，不影响其他关联。"""
        db.add_many(sample_question, ["椭圆", "离心率", "导数"])
        db.remove(sample_question, "离心率")
        rows = db.get_by_question(sample_question)
        topic_names = {r["topic_name"] for r in rows}
        assert topic_names == {"椭圆", "导数"}

    def test_remove_topic_still_has_other_questions(self, db: QuestionTopicsDB,
                                                    sample_question: int, sample_question2: int,
                                                    topic_ellipse: int):
        db.add(sample_question, "椭圆")
        db.add(sample_question2, "椭圆")
        db.remove(sample_question, "椭圆")
        assert db.count_by_topic("椭圆") == 1
        assert db.get_by_topic("椭圆")[0]["question_id"] == sample_question2


# ── 跨表联动 ────────────────────────────────────────────────────────

class TestCrossTable:

    def test_full_workflow(self, db: QuestionTopicsDB, sample_question: int, sample_question2: int):
        """完整流程：建 topic → 关联题目 → 查询 → 聚合 → 删除。"""
        # 1. 建 topic
        topic_1 = get_topics_db().create(name="排列组合", aliases=["排列"])
        topic_2 = get_topics_db().create(name="概率")

        # 2. 关联题目
        db.add_many(sample_question, ["排列组合"], primary_index=0)
        db.add_many(sample_question2, ["排列组合", "概率"], primary_index=0)

        # 3. 按题查知识点
        q1_topics = db.get_by_question(sample_question)
        assert {r["topic_name"] for r in q1_topics} == {"排列组合"}

        q2_topics = db.get_by_question(sample_question2)
        assert {r["topic_name"] for r in q2_topics} == {"排列组合", "概率"}

        # 4. 按知识点查题
        rows = db.get_by_topic("排列组合")
        qids = {r["question_id"] for r in rows}
        assert qids == {sample_question, sample_question2}

        # 5. 聚合计数
        assert db.count_by_topic("排列组合") == 2
        assert db.count_by_topic("概率") == 1

        # 6. 删除关联
        db.remove(sample_question, "排列组合")
        assert db.count_by_topic("排列组合") == 1
        assert db.get_by_topic("排列组合")[0]["question_id"] == sample_question2

    def test_search_topic_then_link(self, db: QuestionTopicsDB, sample_question: int):
        """先 search 确认 topic 存在，再关联。"""
        results = get_topics_db().search("三角")
        if not results:
            get_topics_db().create(name="三角函数", aliases=["三角"])
        topic_name = "三角函数"
        db.add(sample_question, topic_name)
        rows = db.get_by_question(sample_question)
        assert rows[0]["topic_name"] == topic_name

    def test_question_without_topics(self, db: QuestionTopicsDB, sample_question: int):
        """新建题目默认无知识点关联。"""
        rows = db.get_by_question(sample_question)
        assert rows == []


# ── 单例 factory ────────────────────────────────────────────────────

class TestSingleton:

    def test_get_question_topics_db_returns_instance(self):
        db = get_question_topics_db()
        assert isinstance(db, QuestionTopicsDB)

    def test_get_question_topics_db_is_same_instance(self):
        db1 = get_question_topics_db()
        db2 = get_question_topics_db()
        assert db1 is db2


# ── 直接使用 ────────────────────────────────────────────────────────

class TestDirectUsage:

    def test_add_and_retrieve(self):
        db = get_question_topics_db()
        qid = get_questions_db().insert(
            source_type="exam",
            subject="数学",
            content_text="圆锥曲线题",
            question_type="解答题",
        )
        get_topics_db().create(name="圆锥曲线")
        db.add(qid, "圆锥曲线", is_primary=True)
        rows = db.get_by_question(qid)
        assert len(rows) == 1
        assert rows[0]["topic_name"] == "圆锥曲线"
        assert rows[0]["is_primary"] == 1
