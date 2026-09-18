"""src/retrieval/error.py 单元测试：get_error_stats / get_error_details。

覆盖：空库统计（除零安全）/ 三计数口径 / 掌握率计算 / 明细 JSON 解析、
pending 标记、resolved int→bool / 脏 JSON 容错 / 无记录空列表 / 只读不动存储。

数据用真实 SQLite 经 store 层直种（questions_db / errors_db，不经写门面）；
依赖 conftest._reset_state（每测试前清空业务表 + 重置单例），测试间无顺序依赖。
纯 SQLite 读取，无向量检索。
"""

from __future__ import annotations

import json

import pytest

from src.retrieval.error import (
    ErrorDetail,
    ErrorStats,
    _loads_summary,
    get_error_details,
    get_error_stats,
)
from src.store.db.errors import ErrorsDB, get_errors_db
from src.store.vector import get_vector_store


SUMMARY = {
    "error_type": "知识盲区",
    "cause": "记混 e=c/a 与 b²=a²-c²",
    "knowledge_gap": "椭圆离心率定义",
    "fix_suggestion": "复习焦点三角形模型",
}


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def errors_db() -> ErrorsDB:
    """``errors`` 表单例（连接统一走共享 SQLite）。"""
    return get_errors_db()


@pytest.fixture()
def seeded(questions_db, errors_db) -> dict:
    """预置 3 道错题三种状态：完整已掌握 / 待补 / 只有口述未掌握。

    返回 ``{"q_full", "q_pending", "q_refl", "e_full", "e_pending", "e_refl"}``。
    """
    def _qid(text: str) -> int:
        return questions_db.insert(
            source_type="exam", subject="数学",
            content_text=text, question_type="解答题",
        )

    q_full, q_pending, q_refl = _qid("题A"), _qid("题B"), _qid("题C")
    e_full = errors_db.insert(
        question_id=q_full,
        user_reflection="把 b/a 当离心率了",
        error_summary=json.dumps(SUMMARY, ensure_ascii=False),
    )
    e_pending = errors_db.insert(question_id=q_pending)  # 两列均 NULL
    e_refl = errors_db.insert(question_id=q_refl, user_reflection="看漏了 a>b>0")
    errors_db.update(q_full, resolved=True)  # 唯一已掌握的一条
    return {
        "q_full": q_full, "q_pending": q_pending, "q_refl": q_refl,
        "e_full": e_full, "e_pending": e_pending, "e_refl": e_refl,
    }


# ── get_error_stats ─────────────────────────────────────────────────


class TestErrorStats:

    def test_empty_db_zero_safe(self):
        """空库：total=0 且 resolve_rate=0.0（除零安全），不抛异常。"""
        stats = get_error_stats()
        assert isinstance(stats, ErrorStats)
        assert stats == ErrorStats(total=0, resolved=0, resolve_rate=0.0, pending_count=0)

    def test_basic_counts(self, seeded: dict):
        """3 道错题（1 已掌握、1 待补、1 普通）→ 四个数字全对。"""
        stats = get_error_stats()
        assert stats.total == 3
        assert stats.resolved == 1
        assert stats.pending_count == 1

    def test_resolve_rate_fraction(self, seeded: dict):
        """掌握率 = resolved / total = 1/3。"""
        assert get_error_stats().resolve_rate == pytest.approx(1 / 3)


# ── get_error_details ───────────────────────────────────────────────


class TestErrorDetails:

    def test_full_row_parsed(self, seeded: dict):
        """正常行：单条返回、四键已解析为 dict、pending=False、resolved 是 bool True。"""
        details = get_error_details(seeded["q_full"])
        assert len(details) == 1
        d = details[0]
        assert isinstance(d, ErrorDetail)
        assert d.error_id == seeded["e_full"]
        assert d.question_id == seeded["q_full"]
        assert d.user_reflection == "把 b/a 当离心率了"
        assert d.error_summary == SUMMARY  # 已解析，非 JSON 字符串
        assert d.pending is False
        assert d.resolved is True  # int 0/1 → bool（is 断言校验真 bool）
        assert d.first_seen.startswith("202")
        assert d.last_seen.startswith("202")

    def test_pending_row(self, seeded: dict):
        """待补行：两列 NULL → pending=True。"""
        d = get_error_details(seeded["q_pending"])[0]
        assert d.pending is True
        assert d.user_reflection is None
        assert d.error_summary is None
        assert d.resolved is False

    def test_reflection_only_row_not_pending(self, seeded: dict):
        """只有口述：pending=False（任一列非 NULL 即不算待补），summary 为 None。"""
        d = get_error_details(seeded["q_refl"])[0]
        assert d.pending is False
        assert d.user_reflection == "看漏了 a>b>0"
        assert d.error_summary is None

    def test_dirty_json_tolerated(self, seeded: dict, errors_db: ErrorsDB):
        """error_summary 被改成非法 JSON → 明细容错 None（不抛）、与统计口径一致。"""
        errors_db.update(seeded["q_full"], error_summary="{bad")
        d = get_error_details(seeded["q_full"])[0]
        assert d.error_summary is None
        # 列值非 NULL → pending False / count_pending 不数它：两处判定同一套规则
        assert d.pending is False
        assert get_error_stats().pending_count == 1

    def test_no_record_returns_empty_list(self, seeded: dict):
        """无错题记录 → 空列表（查询无结果不是错误），不抛异常。"""
        assert get_error_details(999999) == []
        # 题目存在但没进过错题本，同样空
        assert get_error_details(seeded["q_full"] + 10_000) == []


# ── _loads_summary 容错（与写门面语义对齐）──────────────────────────


class TestLoadsSummary:

    def test_valid_dict(self):
        assert _loads_summary(json.dumps(SUMMARY, ensure_ascii=False)) == SUMMARY

    @pytest.mark.parametrize("raw", [None, "", "{bad", "[]", '"str"', "42"])
    def test_bad_or_empty_returns_none(self, raw):
        assert _loads_summary(raw) is None


# ── 只读验证 ────────────────────────────────────────────────────────


class TestReadOnly:

    def test_functions_change_nothing(self, seeded: dict, errors_db: ErrorsDB):
        """调完两个读函数：SQLite 行数与时间戳、Chroma 数量全部原样。"""
        db = get_errors_db()
        before_rows = {
            r["question_id"]: r
            for r in (db.get_by_question_id(q) for q in (seeded["q_full"], seeded["q_pending"], seeded["q_refl"]))
        }
        vs_before = get_vector_store().count()
        errors_before = db.count()

        get_error_stats()
        for q in ("q_full", "q_pending", "q_refl"):
            get_error_details(seeded[q])

        assert db.count() == errors_before
        assert get_vector_store().count() == vs_before
        for q, row in before_rows.items():
            assert db.get_by_question_id(q) == row  # 含 last_seen 未被刷新
