"""ingest_question / update_question / delete_question 测试。

ingest：入库 → 知识点归位 → 向量写入 → 返回值原子化；
update：DB 字段同步 + 部分更新语义 + 知识点全量替换 + 向量重建（含 VLM 回读）；
delete：级联三处（question_topics + Chroma + 主行）+ 幂等 + files 表不动。

依赖 conftest._reset_state（每测试前清空 SQLite + Chroma + FileStore + 重置单例），
测试之间无顺序依赖。测试直接使用 config 真实路径（data/gaokao.db、data/chroma_db）。
"""

from __future__ import annotations

import json

import pytest

from src.store.db.files import get_files_db
from src.store.db.questions import get_questions_db
from src.store.db.question_topics import get_question_topics_db
from src.store.db.topics import get_topics_db
from src.store.file_store import FileStore
from src.store.vector import get_vector_store
from src.ingestion.question import delete_question, ingest_question, update_question


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


# ── update_question ─────────────────────────────────────────────────

class TestUpdateQuestion:

    def test_update_text_fields_sync_sqlite_and_chroma(self):
        """改答案/解析/题号 → SQLite、updated_fields、Chroma 文档内容三处同步。"""
        r = ingest_question(
            question_text="已知集合 A={1,2}，求元素个数。",
            answer_text="旧答案",
            analysis_text="旧解析",
            question_type="单选题",
        )
        res = update_question(
            question_id=r["question_id"],
            answer_text="新答案",
            analysis_text="新解析",
            question_number="第3题",
        )
        assert res["question_id"] == r["question_id"]
        assert res["doc_id"] == r["doc_id"]
        assert res["updated_fields"] == ["answer_text", "analysis_text", "question_number"]

        row = get_questions_db().get_by_id(r["question_id"])
        assert row["answer_text"] == "新答案"
        assert row["analysis_text"] == "新解析"
        assert row["question_number"] == "第3题"

        vs = get_vector_store()
        assert vs.count() == 1  # 同 doc_id 覆盖，不是新增
        doc = vs.get(r["doc_id"])
        assert "新答案" in doc["text"] and "旧答案" not in doc["text"]
        assert "新解析" in doc["text"] and "旧解析" not in doc["text"]

    def test_partial_update_keeps_other_fields(self):
        """只传部分字段 → 其余字段不变（None = 不动语义）。"""
        r = ingest_question(
            question_text="题干AAA",
            answer_text="答案BBB",
            analysis_text="解析CCC",
            question_type="填空题",
            exam_year=2025,
        )
        update_question(question_id=r["question_id"], question_number="第1题")

        row = get_questions_db().get_by_id(r["question_id"])
        assert row["content_text"] == "题干AAA"
        assert row["answer_text"] == "答案BBB"
        assert row["analysis_text"] == "解析CCC"
        assert row["question_type"] == "填空题"
        assert row["exam_year"] == 2025

    def test_clear_answer_text(self):
        """传 "" 清空 answer_text → SQLite 置空、Chroma 文本同步剔除旧答案。"""
        r = ingest_question(
            question_text="题干",
            answer_text="答案XYZ",
            question_type="填空题",
        )
        res = update_question(question_id=r["question_id"], answer_text="")
        assert res["updated_fields"] == ["answer_text"]
        row = get_questions_db().get_by_id(r["question_id"])
        assert not row["answer_text"]  # "" 或 NULL 均视为已清空
        assert "答案XYZ" not in get_vector_store().get(r["doc_id"])["text"]

    def test_topic_names_full_replace(self):
        """topic_names 全量替换：旧关联清零、新关联建立、metadata topic_tags 同步。"""
        r = ingest_question(
            question_text="求导数题",
            question_type="解答题",
            topic_names=["导数", "极限"],
        )
        res = update_question(question_id=r["question_id"], topic_names=["新tag"])
        assert res["updated_fields"] == ["topic_names"]

        rows = get_question_topics_db().get_by_question(r["question_id"])
        assert [x["topic_name"] for x in rows] == ["新tag"]
        assert get_topics_db().get_by_name("新tag") is not None  # 归位：未命中自动 create
        meta = get_vector_store().get(r["doc_id"])["metadata"]
        assert meta["topic_tags"] == ["新tag"]

    def test_topic_names_none_keeps_associations(self):
        """topic_names=None → 知识点关联不动（只改题号）。"""
        r = ingest_question(
            question_text="题干",
            question_type="填空题",
            topic_names=["导数"],
        )
        res = update_question(question_id=r["question_id"], question_number="第2题")
        assert res["updated_fields"] == ["question_number"]
        rows = get_question_topics_db().get_by_question(r["question_id"])
        assert [x["topic_name"] for x in rows] == ["导数"]

    def test_topic_names_empty_clears_associations(self):
        """topic_names=[] → 清空关联；metadata 重建后无 topic_tags 残留。"""
        r = ingest_question(
            question_text="题干",
            question_type="填空题",
            topic_names=["导数", "极限"],
        )
        res = update_question(question_id=r["question_id"], topic_names=[])
        assert res["updated_fields"] == ["topic_names"]
        assert get_question_topics_db().get_by_question(r["question_id"]) == []
        doc = get_vector_store().get(r["doc_id"])
        assert "topic_tags" not in doc["metadata"]

    def test_nonexistent_question_raises(self):
        with pytest.raises(ValueError, match="不存在，无法修改"):
            update_question(question_id=999, answer_text="x")

    def test_metadata_only_year_syncs_chroma(self):
        """只改 exam_year（元数据字段）→ 不抛错，SQLite 与 Chroma metadata 同步。

        实现层 VectorStore.upsert 无 metadata-only 通道，统一走重嵌（见
        question.py「实现偏差说明」），此处只验同步结果。
        """
        r = ingest_question(
            question_text="题干",
            question_type="单选题",
            exam_year=2020,
        )
        res = update_question(question_id=r["question_id"], exam_year=2026)
        assert res["updated_fields"] == ["exam_year"]
        assert get_questions_db().get_by_id(r["question_id"])["exam_year"] == 2026
        meta = get_vector_store().get(r["doc_id"])["metadata"]
        assert meta["exam_year"] == 2026

    def test_noop_update_returns_empty_fields(self):
        """传入值与现值相同 → 无实际变更，updated_fields 为空、向量不动。"""
        r = ingest_question(
            question_text="题干",
            answer_text="答案",
            question_type="填空题",
        )
        before = get_vector_store().get(r["doc_id"])["text"]
        res = update_question(question_id=r["question_id"], answer_text="答案")
        assert res["updated_fields"] == []
        assert get_vector_store().get(r["doc_id"])["text"] == before
        assert get_vector_store().count() == 1

    def test_vlm_desc_readback_on_reembed(self, file_store: FileStore):
        """重嵌时图形描述从 processed/vlm_desc/ 自动回读（门面不暴露该参数）。"""
        sha = "c" * 64
        fid = get_files_db().register(
            file_path="data/files/raw/images/uploaded/circle.png",
            sha256=sha,
            size=1,
            kind="image",
        )
        file_store.save_processed(
            json.dumps({"description": "图示：半径为1的圆O"}, ensure_ascii=False).encode("utf-8"),
            category="vlm_desc",
            name=f"{sha}.json",
        )
        r = ingest_question(
            question_text="如图，求圆面积。",
            answer_text="旧答案",
            question_type="解答题",
            image_file_ids=[fid],
            vlm_descriptions=["图示：半径为1的圆O"],
        )
        update_question(question_id=r["question_id"], answer_text="新答案")

        doc = get_vector_store().get(r["doc_id"])
        assert "图示：半径为1的圆O" in doc["text"]  # 来自缓存回读，非调用方传入
        assert "新答案" in doc["text"]
        assert doc["metadata"]["has_image"] is True

    def test_missing_vlm_cache_does_not_raise(self):
        """image_file_ids 指向无缓存描述的图片 → 仅 warning 跳过，重嵌不阻断。"""
        fid = get_files_db().register(
            file_path="data/files/raw/images/uploaded/nocache.png",
            sha256="d" * 64,
            size=1,
            kind="image",
        )
        r = ingest_question(
            question_text="题干",
            question_type="填空题",
            image_file_ids=[fid],
        )
        res = update_question(question_id=r["question_id"], question_number="第1题")
        assert res["updated_fields"] == ["question_number"]
        doc = get_vector_store().get(r["doc_id"])
        assert doc["text"] == "题干"
        assert doc["metadata"]["has_image"] is True


# ── delete_question ─────────────────────────────────────────────────

class TestDeleteQuestion:

    def test_delete_cascades_three_targets(self):
        """级联三处：question_topics 计数 + 主行消失 + Chroma 文档消失。"""
        r = ingest_question(
            question_text="求椭圆离心率最值。",
            question_type="解答题",
            topic_names=["椭圆"],
        )
        qid = r["question_id"]
        get_question_topics_db().add_many(qid, ["椭圆", "离心率"])  # 共 2 条关联

        res = delete_question(question_id=qid)
        assert res["question_id"] == qid
        assert res["doc_id"] == f"q_{qid}"
        assert res["deleted"] is True
        assert res["cascade"] == {
            "question_topics": 2,
            "errors": 0,        # 阶段 1 恒 0，契约形状固定
            "exam_attempts": 0,  # 同上
            "vector": True,
        }
        assert get_questions_db().get_by_id(qid) is None
        assert get_vector_store().get(f"q_{qid}") is None
        assert get_vector_store().count() == 0

    def test_delete_nonexistent_is_idempotent(self):
        """不存在的 id → deleted=False，不抛异常，cascade 各键 0/False。"""
        res = delete_question(question_id=999)
        assert res["question_id"] == 999
        assert res["deleted"] is False
        assert res["cascade"] == {
            "question_topics": 0,
            "errors": 0,
            "exam_attempts": 0,
            "vector": False,
        }

    def test_associations_empty_after_delete(self):
        """删除后 get_by_question 关联清空（知识点行不残留）。"""
        r = ingest_question(
            question_text="题干",
            question_type="填空题",
            topic_names=["导数"],
        )
        delete_question(question_id=r["question_id"])
        assert get_question_topics_db().get_by_question(r["question_id"]) == []

    def test_delete_keeps_files_table(self):
        """raw 永不删：删题不动 files 表登记行（源数据不可再生）。"""
        fid = get_files_db().register(
            file_path="data/files/raw/pdfs/exam.pdf",
            sha256="e" * 64,
            size=1024,
            kind="pdf",
            title="2026 模拟卷",
        )
        r = ingest_question(
            question_text="题干",
            question_type="单选题",
            raw_file_path="data/files/raw/pdfs/exam.pdf",
        )
        assert get_questions_db().get_by_id(r["question_id"])["file_id"] == fid
        delete_question(question_id=r["question_id"])
        # 题没了，源文件登记行保留（注册表是事实记录）
        assert get_files_db().get_by_id(fid) is not None
