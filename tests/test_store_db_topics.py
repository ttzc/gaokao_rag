"""TopicsDB 测试：覆盖 create / get_by_name / search / add_alias / list_all / UNIQUE 约束。

依赖 conftest._reset_state（每测试前清空业务表 + 重置单例），测试之间无顺序依赖。
"""

from __future__ import annotations

import json

import pytest

from src.store.db.topics import TopicsDB, get_topics_db


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture()
def db() -> TopicsDB:
    """TopicsDB 实例（共享连接，数据由 conftest 每测试前清空）。"""
    return get_topics_db()


@pytest.fixture()
def sample_topic(db: TopicsDB) -> int:
    """预创建一个 topic 记录，返回 topic_id。"""
    return db.create(name="椭圆离心率", aliases=["e=c/a", "离心率"])


@pytest.fixture()
def sample_topic_aliasless(db: TopicsDB) -> int:
    """预创建一个无 alias 的 topic，返回 topic_id。"""
    return db.create(name="三角函数")


# ── 初始化 ──────────────────────────────────────────────────────────

class TestInit:

    def test_creates_table(self, db: TopicsDB):
        conn = db._connect()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='topics'"
        ).fetchone()
        assert row is not None

    def test_creates_index(self, db: TopicsDB):
        conn = db._connect()
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='topics'"
        ).fetchall()
        index_names = {r["name"] for r in indexes}
        assert "idx_topics_name" in index_names

    def test_idempotent_init(self, db: TopicsDB):
        """两次 _connect 不报错（IF NOT EXISTS 幂等）。"""
        db._connect()
        db._connect()


# ── create ──────────────────────────────────────────────────────────

class TestCreate:

    def test_returns_int_id(self, db: TopicsDB):
        topic_id = db.create(name="数列")
        assert type(topic_id) is int
        assert topic_id > 0

    def test_stores_name(self, db: TopicsDB):
        topic_id = db.create(name="导数")
        row = db.get_by_name("导数")
        assert row is not None
        assert row["id"] == topic_id
        assert row["name"] == "导数"

    def test_stores_aliases(self, db: TopicsDB):
        db.create(name="椭圆离心率", aliases=["e=c/a", "离心率"])
        row = db.get_by_name("椭圆离心率")
        assert row is not None
        aliases = json.loads(row["aliases"])
        assert aliases == ["e=c/a", "离心率"]

    def test_default_aliases_empty_list(self, db: TopicsDB):
        topic_id = db.create(name="排列组合")
        row = db.get_by_name("排列组合")
        assert row is not None
        assert json.loads(row["aliases"]) == []

    def test_sets_created_at(self, db: TopicsDB):
        topic_id = db.create(name="概率")
        row = db.get_by_name("概率")
        assert row is not None
        assert row["created_at"] is not None
        assert "202" in row["created_at"]

    def test_duplicate_name_raises_value_error(self, db: TopicsDB):
        db.create(name="数列")
        with pytest.raises(ValueError, match="已存在"):
            db.create(name="数列")

    def test_duplicate_name_not_inserted(self, db: TopicsDB):
        """UNIQUE 约束冲突时不应插入新行。"""
        db.create(name="数列")
        with pytest.raises(ValueError):
            db.create(name="数列")
        rows = db.list_all()
        assert len(rows) == 1
        assert rows[0]["name"] == "数列"

    def test_none_aliases_treated_as_empty(self, db: TopicsDB):
        topic_id = db.create(name="向量", aliases=None)
        row = db.get_by_name("向量")
        assert row is not None
        assert json.loads(row["aliases"]) == []

    def test_chinese_aliases_preserved(self, db: TopicsDB):
        db.create(name="二项式定理", aliases=["二项式", "n 次展开"])
        row = db.get_by_name("二项式定理")
        aliases = json.loads(row["aliases"])
        assert "二项式" in aliases
        assert "n 次展开" in aliases


# ── get_by_name ──────────────────────────────────────────────────────

class TestGetByName:

    def test_existing_name(self, db: TopicsDB, sample_topic: int):
        row = db.get_by_name("椭圆离心率")
        assert row is not None
        assert row["id"] == sample_topic
        assert row["name"] == "椭圆离心率"

    def test_missing_name_returns_none(self, db: TopicsDB):
        assert db.get_by_name("不存在的知识点") is None

    def test_case_sensitive(self, db: TopicsDB, sample_topic: int):
        """中文无大小写概念，不存在的输入返回 None。"""
        assert db.get_by_name("不存在") is None

    def test_empty_string(self, db: TopicsDB):
        assert db.get_by_name("") is None

    def test_after_create(self, db: TopicsDB):
        db.create(name="圆锥曲线")
        row = db.get_by_name("圆锥曲线")
        assert row is not None
        assert row["name"] == "圆锥曲线"


# ── search ──────────────────────────────────────────────────────────

class TestSearch:

    def test_name_exact_match(self, db: TopicsDB, sample_topic: int):
        results = db.search("椭圆离心率")
        assert len(results) >= 1
        assert any(r["name"] == "椭圆离心率" for r in results)

    def test_alias_fuzzy_match(self, db: TopicsDB, sample_topic: int):
        results = db.search("e=c/a")
        assert len(results) >= 1
        assert any(r["name"] == "椭圆离心率" for r in results)

    def test_alias_partial_match(self, db: TopicsDB, sample_topic: int):
        results = db.search("离心")
        assert len(results) >= 1
        assert any(r["name"] == "椭圆离心率" for r in results)

    def test_no_match_returns_empty(self, db: TopicsDB, sample_topic: int):
        results = db.search("xxxxxxxx不存在xxxxxxxx")
        assert results == []

    def test_empty_db_returns_empty(self, db: TopicsDB):
        results = db.search("任何关键词")
        assert results == []

    def test_multiple_matches_combined(self, db: TopicsDB):
        db.create(name="三角函数", aliases=["三角"])
        db.create(name="三角函数恒等变换", aliases=["三角恒等"])
        results = db.search("三角")
        names = {r["name"] for r in results}
        assert "三角函数" in names
        assert "三角函数恒等变换" in names

    def test_result_contains_expected_fields(self, db: TopicsDB, sample_topic: int):
        results = db.search("离心")
        assert len(results) >= 1
        row = results[0]
        assert "id" in row
        assert "name" in row
        assert "aliases" in row
        assert "created_at" in row

    def test_results_sorted_by_id(self, db: TopicsDB):
        db.create(name="A知识点")
        db.create(name="B知识点")
        results = db.search("知识点")
        ids = [r["id"] for r in results]
        assert ids == sorted(ids)

    def test_returns_aliases_as_json_string(self, db: TopicsDB, sample_topic: int):
        results = db.search("离心")
        row = next(r for r in results if r["name"] == "椭圆离心率")
        aliases = json.loads(row["aliases"])
        assert isinstance(aliases, list)


# ── list_all ────────────────────────────────────────────────────────

class TestListAll:

    def test_empty(self, db: TopicsDB):
        assert db.list_all() == []

    def test_single_topic(self, db: TopicsDB, sample_topic: int):
        rows = db.list_all()
        assert len(rows) == 1
        assert rows[0]["id"] == sample_topic
        assert rows[0]["name"] == "椭圆离心率"

    def test_sorted_by_id(self, db: TopicsDB):
        db.create(name="B")
        db.create(name="A")
        db.create(name="C")
        ids = [r["id"] for r in db.list_all()]
        assert ids == sorted(ids)

    def test_returns_all_fields(self, db: TopicsDB, sample_topic: int):
        rows = db.list_all()
        assert len(rows) == 1
        row = rows[0]
        assert "id" in row
        assert "name" in row
        assert "aliases" in row
        assert "created_at" in row


# ── add_alias ───────────────────────────────────────────────────────

class TestAddAlias:

    def test_adds_new_alias(self, db: TopicsDB, sample_topic: int):
        db.add_alias(sample_topic, "新别名")
        row = db.get_by_name("椭圆离心率")
        aliases = json.loads(row["aliases"])
        assert "新别名" in aliases

    def test_preserves_existing_aliases(self, db: TopicsDB, sample_topic: int):
        db.add_alias(sample_topic, "保留")
        row = db.get_by_name("椭圆离心率")
        aliases = json.loads(row["aliases"])
        assert "e=c/a" in aliases
        assert "离心率" in aliases
        assert "保留" in aliases

    def test_duplicate_alias_skipped(self, db: TopicsDB, sample_topic: int):
        db.add_alias(sample_topic, "e=c/a")
        row = db.get_by_name("椭圆离心率")
        aliases = json.loads(row["aliases"])
        assert aliases.count("e=c/a") == 1

    def test_missing_topic_id_raises(self, db: TopicsDB):
        with pytest.raises(ValueError, match="不存在"):
            db.add_alias(9999, "别名")

    def test_add_to_aliasless_topic(self, db: TopicsDB, sample_topic_aliasless: int):
        db.add_alias(sample_topic_aliasless, "三角")
        row = db.get_by_name("三角函数")
        aliases = json.loads(row["aliases"])
        assert aliases == ["三角"]

    def test_multiple_aliases_appended(self, db: TopicsDB, sample_topic: int):
        db.add_alias(sample_topic, "别名1")
        db.add_alias(sample_topic, "别名2")
        db.add_alias(sample_topic, "别名3")
        row = db.get_by_name("椭圆离心率")
        aliases = json.loads(row["aliases"])
        assert len(aliases) == 5  # 原有 2 个 + 新增 3 个
        assert "别名1" in aliases
        assert "别名2" in aliases
        assert "别名3" in aliases


# ── 单例 factory ────────────────────────────────────────────────────

class TestSingleton:

    def test_get_topics_db_returns_instance(self):
        db = get_topics_db()
        assert isinstance(db, TopicsDB)

    def test_get_topics_db_is_same_instance(self):
        db1 = get_topics_db()
        db2 = get_topics_db()
        assert db1 is db2


# ── 直接使用 ────────────────────────────────────────────────────────

class TestDirectUsage:

    def test_create_and_search(self, db: TopicsDB):
        topic_id = db.create(name="复数", aliases=["复平面"])
        assert topic_id > 0
        results = db.search("复")
        assert any(r["name"] == "复数" for r in results)

    def test_full_workflow(self, db: TopicsDB):
        # 创建 topic → 查名字 → 加 alias → 再查
        topic_id = db.create(name="平面向量", aliases=["向量"])
        assert db.get_by_name("平面向量") is not None
        db.add_alias(topic_id, "矢量")
        results = db.search("矢量")
        assert len(results) >= 1
        assert results[0]["name"] == "平面向量"


# ── close ───────────────────────────────────────────────────────────

class TestClose:

    def test_close_is_noop(self, db: TopicsDB):
        """close() 不关闭共享连接（由 db/__init__.py 统一管理）。"""
        db.close()  # 不应报错
        # 共享连接仍然可用
        row = db._connect().execute("SELECT 1").fetchone()
        assert row is not None
