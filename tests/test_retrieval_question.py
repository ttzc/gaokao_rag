# tests/test_retrieval_question.py
"""src/retrieval/question.py 单元测试：get_question_detail / browse_questions。

覆盖：
- browse_questions：无过滤全量 / 年份 / 题型 / 考区包含 / 知识点反查 / 整卷 file_id /
  组合过滤 / limit / 未知键报错 / 空命中 / 摘要截断 / hit 字段封装
- get_question_detail：完整详情 / 不存在抛错 / 悬空知识点跳过 / 悬空图片 id 跳过
- 辅助逻辑：_json_list 容错

依赖 conftest._reset_state（每测试前清空业务表 + 重置单例），测试之间无顺序依赖。
纯 SQLite 读取，无向量检索、不 patch 嵌入。
"""

from __future__ import annotations

import pytest

from src.retrieval.question import (
    _SUMMARY_LEN,
    _json_list,
    browse_questions,
    get_question_detail,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def paper_id(files_db) -> int:
    """一份试卷的 files.id（"2026 南昌一模数学卷"）。"""
    return files_db.register(
        file_path="data/files/raw/pdfs/nanchang_mock.pdf",
        sha256="a" * 64,
        size=1024,
        kind="pdf",
        title="2026 南昌一模数学卷",
    )


@pytest.fixture()
def image_ids(files_db) -> list[int]:
    """两张题目图片的 files.id 列表。"""
    return [
        files_db.register(
            file_path=f"data/files/images/q{i}.png",
            sha256=f"{i}" * 64,
            size=100,
            kind="image",
        )
        for i in (1, 2)
    ]


@pytest.fixture()
def seeded(questions_db, topics_db, question_topics_db, paper_id, image_ids) -> dict:
    """预置 3 道题 + 知识点关联，返回 {q1, q2, q3, img1, img2, paper}。

    q1: 2026 南昌一模 解答题（带 2 图，知识点：椭圆/离心率）
    q2: 2025 深圳 单选题（无图，知识点：椭圆）
    q3: 2026 无来源文件 填空题（无图，无知识点）
    """
    q1 = questions_db.insert(
        source_type="exam",
        subject="数学",
        content_text="已知椭圆 C 的离心率为 1/2，求其标准方程。" * 10,  # 超长，验证摘要截断
        question_type="解答题",
        file_id=paper_id,
        exam_regions=["南昌", "江西", "全国一卷"],
        exam_year=2026,
        exam_month=3,
        question_number="第21题",
        answer_text="x²/4 + y²/3 = 1",
        analysis_text="由 e=c/a=1/2 设 a=2c 代入求解。",
        image_file_ids=image_ids,
    )
    q2 = questions_db.insert(
        source_type="exam",
        subject="数学",
        content_text="sin15°·cos15° 的值为（　）。",
        question_type="单选题",
        exam_regions=["深圳", "广东", "全国一卷"],
        exam_year=2025,
        exam_month=6,
        question_number="第3题",
    )
    q3 = questions_db.insert(
        source_type="homework",
        subject="数学",
        content_text="数列求和练习。",
        question_type="填空题",
        exam_year=2026,
    )
    topics_db.create("椭圆")
    topics_db.create("离心率")
    question_topics_db.add_many(q1, ["椭圆", "离心率"])
    question_topics_db.add_many(q2, ["椭圆"])
    return {"q1": q1, "q2": q2, "q3": q3, "img1": image_ids[0], "img2": image_ids[1], "paper": paper_id}


# ═════════════════════════════════════════════════════════════════════════════
# browse_questions
# ═════════════════════════════════════════════════════════════════════════════


class TestBrowseQuestions:
    """结构化浏览：纯 SQLite 过滤。"""

    def test_no_filters_returns_all(self, seeded):
        hits = browse_questions({})
        assert [h.question_id for h in hits] == [seeded["q1"], seeded["q2"], seeded["q3"]]

    def test_filter_by_exam_year(self, seeded):
        hits = browse_questions({"exam_year": 2026})
        assert {h.question_id for h in hits} == {seeded["q1"], seeded["q3"]}

    def test_filter_by_question_type(self, seeded):
        hits = browse_questions({"question_type": "解答题"})
        assert [h.question_id for h in hits] == [seeded["q1"]]

    def test_filter_by_exam_region_contains(self, seeded):
        # 单值考区对层级列表做包含匹配："南昌" 命中 ["南昌","江西","全国一卷"]
        hits = browse_questions({"exam_region": "南昌"})
        assert [h.question_id for h in hits] == [seeded["q1"]]
        # "全国一卷" 两道题共有
        hits = browse_questions({"exam_region": "全国一卷"})
        assert {h.question_id for h in hits} == {seeded["q1"], seeded["q2"]}

    def test_filter_by_topic_name(self, seeded):
        hits = browse_questions({"topic_name": "离心率"})
        assert [h.question_id for h in hits] == [seeded["q1"]]
        hits = browse_questions({"topic_name": "椭圆"})
        assert {h.question_id for h in hits} == {seeded["q1"], seeded["q2"]}

    def test_topic_no_relation_returns_empty(self, seeded):
        assert browse_questions({"topic_name": "不存在的知识点"}) == []

    def test_filter_by_file_id(self, seeded):
        hits = browse_questions({"file_id": seeded["paper"]})
        assert [h.question_id for h in hits] == [seeded["q1"]]

    def test_combined_filters(self, seeded):
        # "2026 南昌一模所有解答题"（文档示例场景）
        hits = browse_questions({"exam_year": 2026, "exam_region": "南昌", "question_type": "解答题"})
        assert [h.question_id for h in hits] == [seeded["q1"]]
        # 组合无命中
        assert browse_questions({"exam_year": 2025, "question_type": "解答题"}) == []

    def test_file_id_with_extra_equality_filter(self, seeded):
        """file_id 路径下等值过滤仍生效（不会把整卷全返回）。"""
        assert browse_questions({"file_id": seeded["paper"], "question_type": "单选题"}) == []
        hits = browse_questions({"file_id": seeded["paper"], "exam_year": 2026})
        assert [h.question_id for h in hits] == [seeded["q1"]]

    def test_filter_by_source_type(self, seeded):
        hits = browse_questions({"source_type": "homework"})
        assert [h.question_id for h in hits] == [seeded["q3"]]

    def test_limit(self, seeded):
        hits = browse_questions({"limit": 2})
        assert [h.question_id for h in hits] == [seeded["q1"], seeded["q2"]]
        assert browse_questions({"limit": 0}) == []

    def test_unknown_filter_raises(self):
        with pytest.raises(ValueError, match="不支持"):
            browse_questions({"难度": "高"})

    def test_hit_fields_wrapped(self, seeded):
        hit = browse_questions({"exam_year": 2026, "question_type": "解答题"})[0]
        assert hit.doc_id == f"q_{seeded['q1']}"
        assert hit.question_number == "第21题"
        assert (hit.exam_year, hit.exam_month) == (2026, 3)
        assert hit.exam_regions == ["南昌", "江西", "全国一卷"]
        assert hit.has_image is True
        assert hit.score is None  # 浏览无语义相关度
        # 摘要截断：超长题干 → _SUMMARY_LEN + 省略号
        assert len(hit.content_text) == _SUMMARY_LEN + 1
        assert hit.content_text.endswith("…")

    def test_short_content_not_truncated(self, seeded):
        hit = browse_questions({"question_type": "填空题"})[0]
        assert hit.content_text == "数列求和练习。"
        assert hit.has_image is False


# ═════════════════════════════════════════════════════════════════════════════
# get_question_detail
# ═════════════════════════════════════════════════════════════════════════════


class TestGetQuestionDetail:
    """完整详情：题目 + 关联知识点 + 图片 file_id。"""

    def test_full_detail(self, seeded):
        d = get_question_detail(seeded["q1"])
        assert d.question_id == seeded["q1"]
        assert d.doc_id == f"q_{seeded['q1']}"
        assert d.subject == "数学"
        assert d.source_type == "exam"
        assert d.file_id == seeded["paper"]
        assert d.question_number == "第21题"
        assert d.question_type == "解答题"
        assert d.exam_regions == ["南昌", "江西", "全国一卷"]
        assert (d.exam_year, d.exam_month) == (2026, 3)
        # 详情是全文，不截断
        assert len(d.content_text) > _SUMMARY_LEN
        assert d.answer_text == "x²/4 + y²/3 = 1"
        assert d.analysis_text == "由 e=c/a=1/2 设 a=2c 代入求解。"
        assert d.topic_names == ["椭圆", "离心率"]
        assert d.image_file_ids == [seeded["img1"], seeded["img2"]]

    def test_minimal_question(self, seeded):
        d = get_question_detail(seeded["q3"])
        assert d.answer_text is None
        assert d.topic_names == []
        assert d.image_file_ids == []
        assert d.file_id is None

    def test_not_found_raises(self):
        with pytest.raises(ValueError, match="不存在"):
            get_question_detail(999999)

    def test_dangling_topic_skipped(self, seeded, question_topics_db):
        """question_topics 里未登记到 topics 的悬空 tag → 跳过并 warning。"""
        question_topics_db.add(seeded["q1"], "幽灵知识点")  # 不建 topics 记录
        d = get_question_detail(seeded["q1"])
        assert d.topic_names == ["椭圆", "离心率"]

    def test_dangling_image_skipped(self, seeded, questions_db, image_ids):
        """image_file_ids 引用不存在的 files.id → 跳过。"""
        questions_db.update(seeded["q2"], image_file_ids=[999999, image_ids[0]])
        d = get_question_detail(seeded["q2"])
        assert d.image_file_ids == [image_ids[0]]


# ═════════════════════════════════════════════════════════════════════════════
# 辅助逻辑
# ═════════════════════════════════════════════════════════════════════════════


class TestJsonList:
    """_json_list 容错。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, []),
            ("", []),
            ('["南昌", "江西"]', ["南昌", "江西"]),
            ("[1, 2]", [1, 2]),
            ("非法JSON", []),
            ('{"k": 1}', []),   # 合法 JSON 但非列表
            ("[]", []),
        ],
    )
    def test_parse(self, raw, expected):
        assert _json_list(raw) == expected
