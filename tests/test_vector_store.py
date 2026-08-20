# tests/test_vector_store.py
"""src/store/vector/vector_store.py 单元测试。

覆盖：
- upsert 幂等：同 doc_id 第二次调用覆盖不重复
- upsert_many 批量写入 + 幂等
- search 返回 (Document, score)，支持 where 过滤（含 $contains 数组过滤）
- delete 后 get 返回 None
- get 单条查询
- count() 正确性
- 维度防呆：初始化时若现有向量维度 != expected_dim，raise RuntimeError
- metadata 完整保留（含数组字段 exam_regions / topic_tags）
- 边界：空列表、空 collection、missing doc_id
"""

from __future__ import annotations

import pytest

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from src.store.vector.vector_store import VectorStore, get_vector_store


# ═══════════════════════════════════════════════════════════════════════════════
# FakeEmbeddings — 定长 1024 维，不真调 API
# ═══════════════════════════════════════════════════════════════════════════════


class FakeEmbeddings(Embeddings):
    """测试用假嵌入模型，返回固定 1024 维向量。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1024 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * 1024


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def vector_store(tmp_path):
    """VectorStore 实例（tmp_path 持久化 + FakeEmbeddings）。

    每个测试获得独立的 Chroma 目录，保证数据隔离。
    """
    store_dir = tmp_path / "chroma_db"
    store_dir.mkdir()
    return VectorStore(
        collection_name="gaokao",
        persist_dir=str(store_dir),
        expected_dim=1024,
        embedding_function=FakeEmbeddings(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════════════════════════════════


class TestInit:

    def test_creates_chroma_instance(self, vector_store: VectorStore):
        assert vector_store.vectorstore is not None

    def test_empty_collection_count_zero(self, vector_store: VectorStore):
        assert vector_store.count() == 0

    def test_empty_collection_get_returns_none(self, vector_store: VectorStore):
        assert vector_store.get("q_1") is None

    def test_collection_name_persisted(self, tmp_path):
        store_dir = tmp_path / "chroma_db"
        store_dir.mkdir()
        vs = VectorStore(
            collection_name="my_collection",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=FakeEmbeddings(),
        )
        assert vs._collection_name == "my_collection"

    def test_dimension_guard_passes_on_empty_collection(self, tmp_path):
        """空 collection 不触发维度检查。"""
        store_dir = tmp_path / "chroma_db"
        store_dir.mkdir()
        # 不报错即通过
        vs = VectorStore(
            collection_name="gaokao",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=FakeEmbeddings(),
        )
        assert vs.count() == 0


# ═══════════════════════════════════════════════════════════════════════════════
# upsert（单条 + 幂等）
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpsert:

    def test_upsert_single_stores_text(self, vector_store: VectorStore):
        vector_store.upsert("q_1", "已知函数 f(x) = x² + 2x - 3，求最小值。", {"subject": "数学"})
        result = vector_store.get("q_1")
        assert result is not None
        assert result["text"] == "已知函数 f(x) = x² + 2x - 3，求最小值。"

    def test_upsert_injects_doc_id_into_metadata(self, vector_store: VectorStore):
        vector_store.upsert("q_1", "题目内容", {"subject": "数学"})
        result = vector_store.get("q_1")
        assert result["metadata"]["doc_id"] == "q_1"

    def test_upsert_idempotent_same_doc_id(self, vector_store: VectorStore):
        """同 doc_id 第二次 upsert 覆盖，不重复。"""
        vector_store.upsert("q_1", "第一次内容", {"subject": "数学"})
        vector_store.upsert("q_1", "第二次内容", {"subject": "数学"})
        assert vector_store.count() == 1
        result = vector_store.get("q_1")
        assert result["text"] == "第二次内容"

    def test_upsert_idempotent_metadata_updated(self, vector_store: VectorStore):
        """第二次 upsert 时 metadata 也更新。"""
        vector_store.upsert("q_1", "内容", {"subject": "数学", "source_type": "exam"})
        vector_store.upsert("q_1", "内容", {"subject": "数学", "source_type": "homework"})
        result = vector_store.get("q_1")
        assert result["metadata"]["source_type"] == "homework"

    def test_upsert_different_ids_distinct(self, vector_store: VectorStore):
        """不同 doc_id 产生不同 document。"""
        vector_store.upsert("q_1", "题目A", {"subject": "数学"})
        vector_store.upsert("q_2", "题目B", {"subject": "数学"})
        assert vector_store.count() == 2
        assert vector_store.get("q_1")["text"] == "题目A"
        assert vector_store.get("q_2")["text"] == "题目B"

    def test_upsert_preserves_full_metadata(self, vector_store: VectorStore):
        """题目 metadata 完整保留（所有字段）。"""
        metadata = {
            "doc_type": "question",
            "subject": "数学",
            "source_type": "exam",
            "title": "2026 南昌一模数学卷",
            "topic_tags": ["椭圆", "离心率"],
            "exam_regions": ["南昌", "江西", "全国一卷"],
            "exam_year": 2026,
            "question_type": "解答题",
            "has_image": True,
        }
        vector_store.upsert("q_42", "题干+答案+解析", metadata)
        result = vector_store.get("q_42")
        assert result["metadata"]["doc_type"] == "question"
        assert result["metadata"]["subject"] == "数学"
        assert result["metadata"]["source_type"] == "exam"
        assert result["metadata"]["title"] == "2026 南昌一模数学卷"
        assert result["metadata"]["topic_tags"] == ["椭圆", "离心率"]
        assert result["metadata"]["exam_regions"] == ["南昌", "江西", "全国一卷"]
        assert result["metadata"]["exam_year"] == 2026
        assert result["metadata"]["question_type"] == "解答题"
        assert result["metadata"]["has_image"] is True

    def test_upsert_knowledge_note_metadata(self, vector_store: VectorStore):
        """讲解 document（kn_*）只有通用字段，无 exam_* 字段。"""
        metadata = {
            "doc_type": "note",
            "subject": "数学",
            "source_type": "notes",
            "title": "椭圆知识点讲义",
            "topic_tags": ["椭圆", "离心率"],
        }
        vector_store.upsert("kn_7", "椭圆是平面上到定点距离等于定长的点的轨迹...", metadata)
        result = vector_store.get("kn_7")
        assert result["metadata"]["doc_type"] == "note"
        assert result["metadata"]["topic_tags"] == ["椭圆", "离心率"]
        assert "exam_regions" not in result["metadata"]
        assert "exam_year" not in result["metadata"]
        assert "has_image" not in result["metadata"]

    def test_upsert_two_segment_doc_id(self, vector_store: VectorStore):
        """doc_id 为两段式，无多余下划线。"""
        vector_store.upsert("q_42", "题", {"subject": "数学"})
        vector_store.upsert("kn_7", "讲", {"doc_type": "note"})
        assert vector_store.get("q_42") is not None
        assert vector_store.get("kn_7") is not None
        # 两段式：q_ 后无下划线
        assert "_" not in "q_42"[2:]
        assert "_" not in "kn_7"[3:]


# ═══════════════════════════════════════════════════════════════════════════════
# upsert_many（批量 + 幂等）
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpsertMany:

    def test_upsert_many_basic(self, vector_store: VectorStore):
        docs = [
            {"doc_id": "q_1", "text": "题目1", "metadata": {"subject": "数学"}},
            {"doc_id": "q_2", "text": "题目2", "metadata": {"subject": "数学"}},
            {"doc_id": "kn_1", "text": "讲解1", "metadata": {"doc_type": "note"}},
        ]
        vector_store.upsert_many(docs)
        assert vector_store.count() == 3

    def test_upsert_many_idempotent(self, vector_store: VectorStore):
        """重复调用 upsert_many 不产生重复。"""
        docs = [
            {"doc_id": "q_1", "text": "题目1", "metadata": {"subject": "数学"}},
            {"doc_id": "q_2", "text": "题目2", "metadata": {"subject": "数学"}},
        ]
        vector_store.upsert_many(docs)
        vector_store.upsert_many(docs)
        assert vector_store.count() == 2

    def test_upsert_many_overwrites_existing(self, vector_store: VectorStore):
        """upsert_many 覆盖已存在的 document。"""
        vector_store.upsert_many([
            {"doc_id": "q_1", "text": "旧内容", "metadata": {"subject": "数学"}},
        ])
        vector_store.upsert_many([
            {"doc_id": "q_1", "text": "新内容", "metadata": {"subject": "物理"}},
        ])
        assert vector_store.count() == 1
        assert vector_store.get("q_1")["text"] == "新内容"
        assert vector_store.get("q_1")["metadata"]["subject"] == "物理"

    def test_upsert_many_empty_list(self, vector_store: VectorStore):
        """空列表静默返回，不影响已有数据。"""
        vector_store.upsert("q_1", "题", {"subject": "数学"})
        vector_store.upsert_many([])
        assert vector_store.count() == 1

    def test_upsert_many_preserves_metadata(self, vector_store: VectorStore):
        """批量 upsert 保留完整 metadata。"""
        docs = [
            {
                "doc_id": "q_1",
                "text": "椭圆题",
                "metadata": {
                    "subject": "数学",
                    "source_type": "exam",
                    "topic_tags": ["椭圆", "离心率"],
                    "exam_regions": ["南昌", "江西"],
                    "exam_year": 2026,
                    "question_type": "解答题",
                    "has_image": True,
                },
            },
        ]
        vector_store.upsert_many(docs)
        result = vector_store.get("q_1")
        assert result["metadata"]["topic_tags"] == ["椭圆", "离心率"]
        assert result["metadata"]["exam_regions"] == ["南昌", "江西"]
        assert result["metadata"]["exam_year"] == 2026


# ═══════════════════════════════════════════════════════════════════════════════
# upsert_document / upsert_documents（Document 对象接口）
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpsertDocument:
    """测试 Document 对象直接传入接口。"""

    def test_upsert_document_single(self, tmp_path):
        """单个 Document 可 upsert，随后 search 能命中。"""
        fake_embedding = FakeEmbeddings()
        store_dir = tmp_path / "chroma_db"
        store_dir.mkdir()
        vs = VectorStore(
            collection_name="test_doc",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=fake_embedding,
        )
        doc = Document(page_content="椭圆离心率", metadata={"doc_id": "q_1", "subject": "数学"})
        vs.upsert_document(doc)
        results = vs.search("离心率", k=1)
        assert len(results) == 1
        assert results[0][0].page_content == "椭圆离心率"

    def test_upsert_documents_batch(self, tmp_path):
        """批量 Document 可 upsert，count 正确。"""
        fake_embedding = FakeEmbeddings()
        store_dir = tmp_path / "chroma_db"
        store_dir.mkdir()
        vs = VectorStore(
            collection_name="test_doc_batch",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=fake_embedding,
        )
        docs = [
            Document(page_content="题1", metadata={"doc_id": "q_1"}),
            Document(page_content="题2", metadata={"doc_id": "q_2"}),
        ]
        vs.upsert_documents(docs)
        assert vs.count() == 2

    def test_upsert_many_accepts_documents(self, tmp_path):
        """upsert_many 同时支持 list[dict] 和 list[Document]。"""
        fake_embedding = FakeEmbeddings()
        store_dir = tmp_path / "chroma_db"
        store_dir.mkdir()
        vs = VectorStore(
            collection_name="test_mixed",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=fake_embedding,
        )
        # Document 输入
        vs.upsert_many([Document(page_content="doc", metadata={"doc_id": "q_d"})])
        # dict 输入（原有行为）
        vs.upsert_many([{"doc_id": "q_e", "text": "entry", "metadata": {}}])
        assert vs.count() == 2

    def test_upsert_document_missing_doc_id_raises(self, tmp_path):
        """Document 缺少 doc_id 时 raise ValueError。"""
        fake_embedding = FakeEmbeddings()
        store_dir = tmp_path / "chroma_db"
        store_dir.mkdir()
        vs = VectorStore(
            collection_name="test_no_id",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=fake_embedding,
        )
        doc = Document(page_content="no id", metadata={"subject": "数学"})
        with pytest.raises(ValueError, match="doc_id"):
            vs.upsert_document(doc)

    def test_upsert_document_does_not_mutate_metadata(self, tmp_path):
        """upsert_document 不应修改传入 Document 的 metadata。"""
        fake_embedding = FakeEmbeddings()
        store_dir = tmp_path / "chroma_db"
        store_dir.mkdir()
        vs = VectorStore(
            collection_name="test_no_mutate",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=fake_embedding,
        )
        meta = {"doc_id": "q_1", "tags": ["椭圆"]}
        doc = Document(page_content="text", metadata=meta)
        vs.upsert_document(doc)
        assert meta == {"doc_id": "q_1", "tags": ["椭圆"]}  # 未被 vs 修改


class TestSearch:

    def test_search_returns_document_and_score(self, vector_store: VectorStore):
        """search 返回 (Document, float) 列表。"""
        vector_store.upsert("q_1", "已知函数 f(x) = x² + 2x - 3，求最小值。", {"subject": "数学"})
        vector_store.upsert("q_2", "计算 1+1 等于几？", {"subject": "数学"})

        results = vector_store.search("求函数最小值", k=2)
        assert len(results) == 2
        for doc, score in results:
            assert isinstance(doc, Document)
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0

    def test_search_respects_k(self, vector_store: VectorStore):
        """search k 参数限制返回数量。"""
        for i in range(10):
            vector_store.upsert(f"q_{i}", f"数学题目{i}：关于函数的性质", {"subject": "数学"})

        results = vector_store.search("函数", k=3)
        assert len(results) == 3

    def test_search_empty_collection(self, vector_store: VectorStore):
        """空 collection 搜索返回空列表。"""
        results = vector_store.search("任意查询", k=5)
        assert results == []

    def test_search_with_where_source_type(self, vector_store: VectorStore):
        """where 按 source_type 过滤。"""
        vector_store.upsert("q_1", "椭圆离心率考试题", {"subject": "数学", "source_type": "exam"})
        vector_store.upsert("q_2", "椭圆离心率作业题", {"subject": "数学", "source_type": "homework"})
        vector_store.upsert("kn_1", "椭圆知识点", {"doc_type": "note", "subject": "数学"})

        results = vector_store.search("椭圆离心率", k=5, where={"source_type": "exam"})
        assert len(results) == 1
        assert results[0][0].metadata["doc_id"] == "q_1"

    def test_search_with_where_doc_type(self, vector_store: VectorStore):
        """where 按 doc_type 过滤（区分题目和讲解）。"""
        vector_store.upsert("q_1", "函数最小值题目", {"doc_type": "question", "subject": "数学"})
        vector_store.upsert("kn_1", "函数最值方法讲解", {"doc_type": "note", "subject": "数学"})

        results = vector_store.search("函数最值", k=5, where={"doc_type": "note"})
        assert len(results) == 1
        assert results[0][0].metadata["doc_id"] == "kn_1"

    def test_search_with_contains_filter_exam_regions(self, vector_store: VectorStore):
        """chromadb 原生 $contains 过滤数组字段（exam_regions）。"""
        vector_store.upsert("q_1", "南昌一模椭圆题", {
            "subject": "数学",
            "exam_regions": ["南昌", "江西", "全国一卷"],
        })
        vector_store.upsert("q_2", "深圳一模椭圆题", {
            "subject": "数学",
            "exam_regions": ["深圳", "广东", "全国一卷"],
        })

        results = vector_store.search(
            "椭圆", k=5, where={"exam_regions": {"$contains": "南昌"}}
        )
        assert len(results) == 1
        assert results[0][0].metadata["doc_id"] == "q_1"

    def test_search_with_contains_filter_topic_tags(self, vector_store: VectorStore):
        """chromadb 原生 $contains 过滤数组字段（topic_tags）。"""
        vector_store.upsert("q_1", "椭圆离心率题", {
            "subject": "数学",
            "topic_tags": ["椭圆", "离心率"],
        })
        vector_store.upsert("q_2", "抛物线题", {
            "subject": "数学",
            "topic_tags": ["抛物线"],
        })

        results = vector_store.search(
            "几何", k=5, where={"topic_tags": {"$contains": "椭圆"}}
        )
        assert len(results) == 1
        assert results[0][0].metadata["doc_id"] == "q_1"

    def test_search_with_where_subject(self, vector_store: VectorStore):
        """where 按 subject 过滤（MVP 固定数学，扩科后生效）。"""
        vector_store.upsert("q_1", "数学题", {"subject": "数学"})
        vector_store.upsert("q_2", "物理题", {"subject": "物理"})

        results = vector_store.search("题", k=5, where={"subject": "数学"})
        assert len(results) == 1
        assert results[0][0].metadata["subject"] == "数学"

    def test_search_with_combined_where(self, vector_store: VectorStore):
        """多条件 where（$and 组合）。"""
        vector_store.upsert("q_1", "南昌椭圆2026", {
            "subject": "数学",
            "source_type": "exam",
            "exam_regions": ["南昌", "江西"],
            "exam_year": 2026,
        })
        vector_store.upsert("q_2", "深圳椭圆2025", {
            "subject": "数学",
            "source_type": "exam",
            "exam_regions": ["深圳", "广东"],
            "exam_year": 2025,
        })
        vector_store.upsert("q_3", "南昌椭圆作业", {
            "subject": "数学",
            "source_type": "homework",
            "exam_regions": ["南昌", "江西"],
        })

        # subject=数学 AND source_type=exam
        results = vector_store.search(
            "椭圆", k=5, where={"$and": [{"subject": "数学"}, {"source_type": "exam"}]}
        )
        assert len(results) == 2
        doc_ids = {r[0].metadata["doc_id"] for r in results}
        assert doc_ids == {"q_1", "q_2"}


# ═══════════════════════════════════════════════════════════════════════════════
# delete / get / count
# ═══════════════════════════════════════════════════════════════════════════════


class TestDelete:

    def test_delete_existing(self, vector_store: VectorStore):
        vector_store.upsert("q_1", "题目内容", {"subject": "数学"})
        assert vector_store.count() == 1
        vector_store.delete(["q_1"])
        assert vector_store.count() == 0

    def test_delete_then_get_returns_none(self, vector_store: VectorStore):
        """删除后 get 返回 None。"""
        vector_store.upsert("q_1", "题目内容", {"subject": "数学"})
        vector_store.delete(["q_1"])
        assert vector_store.get("q_1") is None

    def test_delete_missing_is_noop(self, vector_store: VectorStore):
        """删除不存在的 doc_id 不报错。"""
        vector_store.delete(["q_9999"])
        assert vector_store.count() == 0

    def test_delete_multiple(self, vector_store: VectorStore):
        vector_store.upsert("q_1", "题1", {"subject": "数学"})
        vector_store.upsert("q_2", "题2", {"subject": "数学"})
        vector_store.upsert("q_3", "题3", {"subject": "数学"})
        vector_store.delete(["q_1", "q_3"])
        assert vector_store.count() == 1
        assert vector_store.get("q_1") is None
        assert vector_store.get("q_3") is None
        assert vector_store.get("q_2") is not None

    def test_delete_empty_list(self, vector_store: VectorStore):
        """空 doc_ids 列表静默返回。"""
        vector_store.upsert("q_1", "题", {"subject": "数学"})
        vector_store.delete([])
        assert vector_store.count() == 1

    def test_delete_mixed_existing_and_missing(self, vector_store: VectorStore):
        """混合存在/不存在的 doc_id 只删除存在的。"""
        vector_store.upsert("q_1", "题1", {"subject": "数学"})
        vector_store.upsert("q_2", "题2", {"subject": "数学"})
        vector_store.delete(["q_1", "q_9999", "q_8888"])
        assert vector_store.count() == 1
        assert vector_store.get("q_1") is None
        assert vector_store.get("q_2") is not None


class TestGet:

    def test_get_existing_returns_dict(self, vector_store: VectorStore):
        vector_store.upsert("q_1", "题目内容", {"subject": "数学", "source_type": "exam"})
        result = vector_store.get("q_1")
        assert result is not None
        assert isinstance(result, dict)
        assert result["doc_id"] == "q_1"
        assert result["text"] == "题目内容"
        assert result["metadata"]["subject"] == "数学"
        assert result["metadata"]["source_type"] == "exam"

    def test_get_missing_returns_none(self, vector_store: VectorStore):
        assert vector_store.get("q_9999") is None

    def test_get_after_delete_returns_none(self, vector_store: VectorStore):
        vector_store.upsert("q_1", "题", {"subject": "数学"})
        vector_store.delete(["q_1"])
        assert vector_store.get("q_1") is None


class TestCount:

    def test_count_empty(self, vector_store: VectorStore):
        assert vector_store.count() == 0

    def test_count_after_upserts(self, vector_store: VectorStore):
        vector_store.upsert("q_1", "题1", {"subject": "数学"})
        vector_store.upsert("q_2", "题2", {"subject": "数学"})
        assert vector_store.count() == 2

    def test_count_after_delete(self, vector_store: VectorStore):
        vector_store.upsert("q_1", "题1", {"subject": "数学"})
        vector_store.upsert("q_2", "题2", {"subject": "数学"})
        vector_store.delete(["q_1"])
        assert vector_store.count() == 1

    def test_count_after_upsert_overwrite(self, vector_store: VectorStore):
        """覆盖更新不增加计数。"""
        vector_store.upsert("q_1", "第一次", {"subject": "数学"})
        vector_store.upsert("q_1", "第二次", {"subject": "数学"})
        assert vector_store.count() == 1

    def test_count_after_mixed_upsert_many_and_single(self, vector_store: VectorStore):
        vector_store.upsert_many([
            {"doc_id": "q_1", "text": "题1", "metadata": {"subject": "数学"}},
            {"doc_id": "q_2", "text": "题2", "metadata": {"subject": "数学"}},
        ])
        vector_store.upsert("q_3", "题3", {"subject": "数学"})
        assert vector_store.count() == 3
        # 再 upsert 已有
        vector_store.upsert("q_2", "题2更新", {"subject": "数学"})
        assert vector_store.count() == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 维度防呆
# ═══════════════════════════════════════════════════════════════════════════════


class TestDimensionGuard:

    def test_dimension_mismatch_raises(self, tmp_path):
        """collection 已有向量但维度 != expected_dim 时 raise RuntimeError。"""
        store_dir = tmp_path / "chroma_db"
        store_dir.mkdir()

        # 第一实例：写入 1024 维向量
        store1 = VectorStore(
            collection_name="gaokao_dim_test",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=FakeEmbeddings(),
        )
        store1.upsert("q_1", "题目内容", {"subject": "数学"})

        # 关闭底层 chroma 连接，释放文件锁
        store1.vectorstore._client.close()

        # 第二实例：声称 expected_dim=512，实际 collection 是 1024 → RuntimeError
        with pytest.raises(RuntimeError, match="维度.*不一致"):
            VectorStore(
                collection_name="gaokao_dim_test",
                persist_dir=str(store_dir),
                expected_dim=512,
                embedding_function=FakeEmbeddings(),
            )

    def test_dimension_match_passes(self, tmp_path):
        """维度一致时不报错。"""
        store_dir = tmp_path / "chroma_db"
        store_dir.mkdir()

        store1 = VectorStore(
            collection_name="gaokao_dim_ok",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=FakeEmbeddings(),
        )
        store1.upsert("q_1", "题目", {"subject": "数学"})
        store1.vectorstore._client.close()

        # 同维度重建 → 不报错
        store2 = VectorStore(
            collection_name="gaokao_dim_ok",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=FakeEmbeddings(),
        )
        assert store2.count() == 1

    def test_dimension_error_message_content(self, tmp_path):
        """RuntimeError 信息包含 collection 名和实际/期望维度。"""
        store_dir = tmp_path / "chroma_db"
        store_dir.mkdir()

        store1 = VectorStore(
            collection_name="my_collection",
            persist_dir=str(store_dir),
            expected_dim=1024,
            embedding_function=FakeEmbeddings(),
        )
        store1.upsert("q_1", "题", {"subject": "数学"})
        store1.vectorstore._client.close()

        with pytest.raises(RuntimeError, match="my_collection") as exc_info:
            VectorStore(
                collection_name="my_collection",
                persist_dir=str(store_dir),
                expected_dim=512,
                embedding_function=FakeEmbeddings(),
            )
        assert "1024" in str(exc_info.value)
        assert "512" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════════════════
# 单例 factory（get_vector_store）
# ═══════════════════════════════════════════════════════════════════════════════


class TestSingletonFactory:

    def test_get_vector_store_returns_instance(self):
        """get_vector_store() 返回 VectorStore 实例。"""
        import src.store.vector.vector_store as vs_module

        vs_module._instance = None
        try:
            vs = get_vector_store()
            assert isinstance(vs, VectorStore)
        finally:
            vs_module._instance = None

    def test_get_vector_store_is_same_instance(self):
        """多次调用返回同一实例。"""
        import src.store.vector.vector_store as vs_module

        vs_module._instance = None
        try:
            vs1 = get_vector_store()
            vs2 = get_vector_store()
            assert vs1 is vs2
        finally:
            vs_module._instance = None


# ═══════════════════════════════════════════════════════════════════════════════
# 向量属性（供 LangchainKnowledge 注入）
# ═══════════════════════════════════════════════════════════════════════════════


class TestVectorstoreProperty:

    def test_vectorstore_is_chroma_instance(self, vector_store: VectorStore):
        """vectorstore 属性是 langchain_chroma.Chroma 实例。"""
        from langchain_chroma import Chroma
        assert isinstance(vector_store.vectorstore, Chroma)

    def test_vectorstore_supports_asearch(self, vector_store: VectorStore):
        """底层 Chroma 支持 asearch（LangchainKnowledge 消费）。"""
        assert hasattr(vector_store.vectorstore, "asearch")

    def test_vectorstore_has_collection_name(self, vector_store: VectorStore):
        """底层 collection 名与构造参数一致。"""
        assert vector_store.vectorstore._collection_name == "gaokao"


# ═══════════════════════════════════════════════════════════════════════════════
# 端到端场景
# ═══════════════════════════════════════════════════════════════════════════════


class TestEndToEnd:

    def test_full_lifecycle(self, vector_store: VectorStore):
        """完整的 upsert → search → delete 生命周期。"""
        # 1. 写入题目 + 讲解
        vector_store.upsert("q_1", "椭圆离心率题目", {
            "doc_type": "question",
            "subject": "数学",
            "source_type": "exam",
            "topic_tags": ["椭圆", "离心率"],
            "exam_regions": ["南昌"],
            "exam_year": 2026,
            "question_type": "解答题",
            "has_image": True,
        })
        vector_store.upsert("kn_1", "椭圆定义：平面上到定点距离等于定长的点的轨迹", {
            "doc_type": "note",
            "subject": "数学",
            "source_type": "notes",
            "topic_tags": ["椭圆"],
        })

        assert vector_store.count() == 2

        # 2. 语义检索
        results = vector_store.search("椭圆离心率", k=5)
        assert len(results) == 2
        doc_ids = {r[0].metadata["doc_id"] for r in results}
        assert doc_ids == {"q_1", "kn_1"}

        # 3. 过滤检索
        question_results = vector_store.search("椭圆", k=5, where={"doc_type": "question"})
        assert len(question_results) == 1
        assert question_results[0][0].metadata["doc_id"] == "q_1"

        # 4. 删除题目
        vector_store.delete(["q_1"])
        assert vector_store.count() == 1
        assert vector_store.get("q_1") is None
        assert vector_store.get("kn_1") is not None

        # 5. 剩余讲解仍可检索
        results = vector_store.search("椭圆", k=5)
        assert len(results) == 1
        assert results[0][0].metadata["doc_id"] == "kn_1"

    def test_overwrite_preserves_searchability(self, vector_store: VectorStore):
        """更新题目后，新内容可被检索到。"""
        vector_store.upsert("q_1", "抛物线焦点弦长", {
            "subject": "数学",
            "topic_tags": ["抛物线"],
        })
        # 搜索抛物线相关内容
        results = vector_store.search("抛物线", k=1)
        assert len(results) == 1
        assert results[0][0].metadata["doc_id"] == "q_1"

        # 更新题目内容为椭圆
        vector_store.upsert("q_1", "椭圆离心率计算", {
            "subject": "数学",
            "topic_tags": ["椭圆", "离心率"],
        })
        assert vector_store.count() == 1

        # 再次搜索，应命中更新后的内容
        results = vector_store.search("椭圆离心率", k=1)
        assert len(results) == 1
        assert results[0][0].metadata["doc_id"] == "q_1"
        assert results[0][0].metadata["topic_tags"] == ["椭圆", "离心率"]
