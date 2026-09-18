"""ErrorsDB 测试：覆盖 insert / 查询 / 一题一行 / 部分更新 / 删除 / 待补统计 / FK。

依赖 conftest._reset_state（每测试前清空业务表 + 重置单例），测试之间无顺序依赖。

读取形态说明（SQLite 无 BOOLEAN 类型）：
    ``resolved`` 读出恒为 int ``0/1``——DEFAULT 0 与 bind ``True`` 均落库为整数，
    判空/比较请用 ``== 0`` / ``== 1``，不要依赖 ``is True``。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.store.db.errors import ErrorsDB, get_errors_db
from src.store.db.questions import get_questions_db


# ── 常量与 Fixtures ─────────────────────────────────────────────────

SAMPLE_SUMMARY = json.dumps(
    {
        "error_type": "知识盲区",
        "cause": "忘记讨论 a=0 的情况",
        "knowledge_gap": "含参讨论",
        "fix_suggestion": "做题时先列参数分类清单",
    },
    ensure_ascii=False,
)


@pytest.fixture()
def db() -> ErrorsDB:
    """ErrorsDB 实例（共享连接，数据由 conftest 每测试前清空）。"""
    return get_errors_db()


@pytest.fixture()
def sample_question() -> int:
    """预插入一条题目（errors 的 FK 父行），返回 question_id。"""
    return get_questions_db().insert(
        source_type="exam",
        subject="数学",
        content_text="已知函数 f(x) = x² + 2x - 3，求 f(x) 的最小值。",
        question_type="解答题",
    )


@pytest.fixture()
def sample_error(db: ErrorsDB, sample_question: int) -> int:
    """预插入一条完整错题记录（口述 + 结构化总结），返回 error_id。"""
    return db.insert(
        question_id=sample_question,
        user_reflection="我当时没讨论 a=0 的情况",
        error_summary=SAMPLE_SUMMARY,
    )


# ── 初始化 ──────────────────────────────────────────────────────────

class TestInit:

    def test_creates_table(self, db: ErrorsDB):
        conn = db._connect()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='errors'"
        ).fetchone()
        assert row is not None

    def test_creates_unique_index(self, db: ErrorsDB):
        """idx_errors_question 必须存在且为 UNIQUE（一题一行的约束来源）。"""
        conn = db._connect()
        row = conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index' AND name='idx_errors_question'"
        ).fetchone()
        assert row is not None
        assert "UNIQUE" in row["sql"].upper()

    def test_idempotent_init(self, db: ErrorsDB):
        """两次 _connect 不报错（IF NOT EXISTS 幂等）。"""
        db._connect()
        db._connect()


# ── insert / get 往返 ───────────────────────────────────────────────

class TestInsertGet:

    def test_returns_int_id(self, db: ErrorsDB, sample_question: int):
        eid = db.insert(question_id=sample_question)
        assert type(eid) is int
        assert eid > 0

    def test_roundtrip_full(self, db: ErrorsDB, sample_question: int, sample_error: int):
        row = db.get_by_id(sample_error)
        assert row is not None
        assert row["id"] == sample_error
        assert row["question_id"] == sample_question
        assert row["user_reflection"] == "我当时没讨论 a=0 的情况"

    def test_error_summary_json_stored_verbatim(self, db: ErrorsDB, sample_error: int):
        """error_summary 的 JSON 字符串原样存取，DB 层不解析、不序列化。"""
        row = db.get_by_id(sample_error)
        assert row["error_summary"] == SAMPLE_SUMMARY  # 字符串精确相等
        # 门面层若要消费，自行 loads
        assert json.loads(row["error_summary"])["error_type"] == "知识盲区"

    def test_resolved_default_zero(self, db: ErrorsDB, sample_error: int):
        row = db.get_by_id(sample_error)
        assert row["resolved"] == 0

    def test_seen_timestamps_set_on_insert(self, db: ErrorsDB, sample_error: int):
        row = db.get_by_id(sample_error)
        assert row["first_seen"] is not None
        assert row["last_seen"] is not None
        assert "202" in row["first_seen"]  # datetime('now') 格式

    def test_get_by_id_missing(self, db: ErrorsDB):
        assert db.get_by_id(9999) is None

    def test_get_by_question_id_existing(self, db: ErrorsDB, sample_question: int, sample_error: int):
        row = db.get_by_question_id(sample_question)
        assert row is not None
        assert row["id"] == sample_error

    def test_get_by_question_id_missing(self, db: ErrorsDB, sample_question: int):
        assert db.get_by_question_id(sample_question) is None


# ── 错因待补 ────────────────────────────────────────────────────────

class TestPending:

    def test_insert_pending_row(self, db: ErrorsDB, sample_question: int):
        """两列错因都不传 → 插入成功、读出均为 None。"""
        eid = db.insert(question_id=sample_question)
        row = db.get_by_id(eid)
        assert row["user_reflection"] is None
        assert row["error_summary"] is None

    def test_count_pending(self, db: ErrorsDB, sample_question: int):
        eid = db.insert(question_id=sample_question)
        assert db.count_pending() == 1
        # 补齐口述后不再是待补
        db.update(sample_question, user_reflection="看漏了符号")
        assert db.count_pending() == 0

    def test_count_pending_mixed(self, db: ErrorsDB, sample_question: int, sample_error: int):
        """完整错题 + 待补错题共存：count 统计总数，count_pending 只数两 NULL。"""
        qid2 = get_questions_db().insert(
            source_type="homework", subject="数学",
            content_text="计算 1+1=？", question_type="填空题",
        )
        db.insert(question_id=qid2)  # 待补行
        assert db.count() == 2
        assert db.count_pending() == 1

    def test_empty_string_does_not_count_as_pending(self, db: ErrorsDB, sample_question: int):
        """DB 层只认 NULL："" 落进来就按"传了就写"，不算待补（空串规范化是门面层职责）。"""
        db.insert(question_id=sample_question, user_reflection="")
        assert db.count_pending() == 0


# ── 一题一行 ────────────────────────────────────────────────────────

class TestOneRowPerQuestion:

    def test_duplicate_insert_raises_value_error(self, db: ErrorsDB, sample_question: int, sample_error: int):
        with pytest.raises(ValueError, match=str(sample_question)):
            db.insert(question_id=sample_question)

    def test_duplicate_insert_keeps_original_row(self, db: ErrorsDB, sample_question: int, sample_error: int):
        """冲突插入失败后原记录不受影响。"""
        with pytest.raises(ValueError):
            db.insert(question_id=sample_question)
        assert db.count() == 1
        assert db.get_by_question_id(sample_question)["id"] == sample_error

    def test_different_questions_coexist(self, db: ErrorsDB, sample_question: int):
        """不同题目各一行，不受 UNIQUE 索引影响。"""
        qid2 = get_questions_db().insert(
            source_type="homework", subject="数学",
            content_text="计算 1+1=？", question_type="填空题",
        )
        db.insert(question_id=sample_question)
        db.insert(question_id=qid2)
        assert db.count() == 2


# ── update ──────────────────────────────────────────────────────────

class TestUpdate:

    def _age_last_seen(self, db: ErrorsDB, question_id: int) -> None:
        """把 last_seen 改成远古时间，用于断言 update 会刷新（datetime('now') 只到秒，
        不预置旧值则同秒更新无法区分刷没刷）。"""
        db._connect().execute(
            "UPDATE errors SET last_seen = '2000-01-01 00:00:00' WHERE question_id = ?",
            (question_id,),
        )
        db._connect().commit()

    def test_update_resolved_only_keeps_other_fields(self, db: ErrorsDB, sample_question: int, sample_error: int):
        """只传 resolved=True → 其它字段不变。"""
        before = db.get_by_question_id(sample_question)
        db.update(sample_question, resolved=True)
        after = db.get_by_question_id(sample_question)
        assert after["resolved"] == 1
        assert after["user_reflection"] == before["user_reflection"]
        assert after["error_summary"] == before["error_summary"]
        assert after["first_seen"] == before["first_seen"]  # first_seen 不动

    def test_update_refreshes_last_seen(self, db: ErrorsDB, sample_question: int, sample_error: int):
        """任何字段变更都顺带把 last_seen 刷成 datetime('now')。"""
        self._age_last_seen(db, sample_question)
        db.update(sample_question, resolved=True)
        row = db.get_by_question_id(sample_question)
        assert row["last_seen"] != "2000-01-01 00:00:00"
        assert "202" in row["last_seen"]

    def test_update_user_reflection(self, db: ErrorsDB, sample_question: int, sample_error: int):
        db.update(sample_question, user_reflection="新口述：公式记混了")
        row = db.get_by_question_id(sample_question)
        assert row["user_reflection"] == "新口述：公式记混了"

    def test_update_error_summary(self, db: ErrorsDB, sample_question: int, sample_error: int):
        new_summary = json.dumps({"error_type": "计算错误"}, ensure_ascii=False)
        db.update(sample_question, error_summary=new_summary)
        row = db.get_by_question_id(sample_question)
        assert row["error_summary"] == new_summary

    def test_update_resolved_false_writes_zero(self, db: ErrorsDB, sample_question: int, sample_error: int):
        """resolved=False 是"传了"（与 None=不改 区分），写入 0。"""
        db.update(sample_question, resolved=True)
        db.update(sample_question, resolved=False)
        assert db.get_by_question_id(sample_question)["resolved"] == 0

    def test_update_noop_when_no_fields(self, db: ErrorsDB, sample_question: int, sample_error: int):
        """全不传 → 不报错、数据不变（last_seen 也不刷）。"""
        self._age_last_seen(db, sample_question)
        before = db.get_by_question_id(sample_question)
        db.update(sample_question)  # 不抛异常
        after = db.get_by_question_id(sample_question)
        assert after == before

    def test_update_missing_question_id_raises(self, db: ErrorsDB):
        with pytest.raises(ValueError, match="不存在"):
            db.update(9999, resolved=True)

    def test_update_empty_string_written_verbatim(self, db: ErrorsDB, sample_question: int, sample_error: int):
        """DB 层不做空值特殊处理："" 传了就写（清空语义归门面层）。"""
        db.update(sample_question, user_reflection="")
        assert db.get_by_question_id(sample_question)["user_reflection"] == ""


# ── delete ──────────────────────────────────────────────────────────

class TestDelete:

    def test_delete_existing(self, db: ErrorsDB, sample_question: int, sample_error: int):
        assert db.delete_by_question_id(sample_question) is True
        assert db.get_by_question_id(sample_question) is None
        assert db.count() == 0

    def test_delete_missing_returns_false(self, db: ErrorsDB):
        assert db.delete_by_question_id(9999) is False

    def test_delete_twice_second_false(self, db: ErrorsDB, sample_question: int, sample_error: int):
        assert db.delete_by_question_id(sample_question) is True
        assert db.delete_by_question_id(sample_question) is False

    def test_reinsert_after_delete(self, db: ErrorsDB, sample_question: int, sample_error: int):
        """删掉后可重新 insert（一题一行不阻止"删了再记"）。"""
        db.delete_by_question_id(sample_question)
        new_id = db.insert(question_id=sample_question, user_reflection="又错了")
        assert new_id != sample_error
        assert db.count() == 1


# ── FK 约束 ─────────────────────────────────────────────────────────

class TestForeignKey:

    def test_insert_nonexistent_question_raises(self, db: ErrorsDB):
        """question_id 指向不存在的题 → FK 约束报错（共享连接 PRAGMA foreign_keys=ON 生效）。

        FK 属"先题后错"铁律被违反（调用方 bug），原样抛 sqlite3.IntegrityError，
        不转 ValueError——与 UNIQUE 冲突（可预期业务冲突）区分。
        """
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            db.insert(question_id=999999, user_reflection="无题有错")
        assert db.count() == 0


# ── resolved 读取形态 ───────────────────────────────────────────────

class TestResolvedReadShape:

    def test_default_and_updated_are_int(self, db: ErrorsDB, sample_question: int, sample_error: int):
        """SQLite 无 BOOLEAN 类型：resolved 读出恒为 int 0/1（bind True 也落为 1）。"""
        assert type(db.get_by_question_id(sample_question)["resolved"]) is int
        db.update(sample_question, resolved=True)
        row = db.get_by_question_id(sample_question)
        assert type(row["resolved"]) is int
        assert row["resolved"] == 1


# ── count ───────────────────────────────────────────────────────────

class TestCount:

    def test_count_empty(self, db: ErrorsDB):
        assert db.count() == 0

    def test_count_rows(self, db: ErrorsDB, sample_error: int):
        qid2 = get_questions_db().insert(
            source_type="homework", subject="数学",
            content_text="计算 1+1=？", question_type="填空题",
        )
        db.insert(question_id=qid2)
        assert db.count() == 2


# ── 单例 factory ───────────────────────────────────────────────────

class TestSingleton:

    def test_get_errors_db_returns_instance(self):
        assert isinstance(get_errors_db(), ErrorsDB)

    def test_get_errors_db_is_same_instance(self):
        assert get_errors_db() is get_errors_db()
