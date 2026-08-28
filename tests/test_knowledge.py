# tests/test_knowledge.py
"""src/retrieval/knowledge.py 单元测试 + 集成测试。

覆盖：
- _translate 各操作符翻译正确性（eq/ne/gt/gte/lt/lte/in/not in/like/not like/between/contains）
- and/or 嵌套递归
- metadata. 前缀正确去除
- build_search_extra_params 返回格式
- search 把 where 透传给 vectorstore.asearch（mock 验证）
- 真实集成测试（FakeEmbeddings + 真实 Chroma）

依赖 conftest._reset_state（每测试前清空全部 Chroma collection + 重置单例 + patch 假嵌入），
测试之间无顺序依赖。集成测试用 config 真实路径（data/chroma_db），单例由 conftest 复位。
"""

from __future__ import annotations

import pytest
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from unittest.mock import AsyncMock, patch

from conftest import FakeEmbeddings
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.knowledge import SearchDocument, SearchParams, SearchRequest, SearchResult
from trpc_agent_sdk.knowledge._filter_expr import KnowledgeFilterExpr
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge
from trpc_agent_sdk.types import Part

from src.config import config
from src.store.vector.vector_store import VectorStore
from src.retrieval.knowledge import GaokaoKnowledge, get_knowledge


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def mock_vs():
    """Mock 底层 vectorstore（asearch 异步返回空列表）。"""
    mock_vectorstore = AsyncMock()
    mock_vectorstore.asearch.return_value = []
    mock_vectorstore.asimilarity_search_with_relevance_scores.return_value = []
    return mock_vectorstore


@pytest.fixture()
def knowledge(mock_vs):
    """GaokaoKnowledge 实例，embedder + vectorstore 均 mock。"""
    with patch("src.retrieval.knowledge.get_embedding_model") as mock_emb, \
         patch("src.retrieval.knowledge.get_vector_store") as mock_vs_store:
        mock_emb.return_value = FakeEmbeddings()
        mock_vs_store.return_value.vectorstore = mock_vs
        knowledge = GaokaoKnowledge()
        # 保存 mock_vs 引用，方便测试断言
        knowledge._mock_vs = mock_vs  # type: ignore[attr-defined]
        return knowledge


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════


def _make_ctx(**metadata) -> "AgentContext":  # noqa: F821 — forward ref
    ctx = create_agent_context()
    for k, v in metadata.items():
        ctx.with_metadata(k, v)
    return ctx


def _make_request(
    query_text: str = "test query",
    search_type: str = "similarity",
    rank_top_k: int = 3,
    extra_params: dict | None = None,
    history: list | None = None,
) -> SearchRequest:
    params = SearchParams(
        search_type=search_type,
        rank_top_k=rank_top_k,
        extra_params=extra_params if extra_params is not None else {},
    )
    req = SearchRequest(
        query=Part.from_text(text=query_text),
        params=params,
        user_id="u1",
        session_id="s1",
        history=history or [],
    )
    return req


# ═══════════════════════════════════════════════════════════════════════════════
# _translate 单元测试
# ═══════════════════════════════════════════════════════════════════════════════


class TestTranslate:

    def _make_expr(self, operator: str, field: str = "metadata.subject", value: Any = None):
        """构造 KnowledgeFilterExpr（绕过 validator，用于测试 _translate 内部方法）。"""
        return KnowledgeFilterExpr.model_construct(
            operator=operator,
            field=field,
            value=value,
        )

    # ── eq / ne ──────────────────────────────────────────────────────────────

    def test_eq_scalar(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("eq", "metadata.subject", "数学")
        result = knowledge._translate(expr)
        assert result == {"subject": {"$eq": "数学"}}

    def test_ne_scalar(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("ne", "metadata.subject", "物理")
        result = knowledge._translate(expr)
        assert result == {"subject": {"$ne": "物理"}}

    def test_eq_integer(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("eq", "metadata.exam_year", 2026)
        result = knowledge._translate(expr)
        assert result == {"exam_year": {"$eq": 2026}}

    # ── range (gt/gte/lt/lte) ────────────────────────────────────────────────

    def test_gte(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("gte", "metadata.exam_year", 2024)
        result = knowledge._translate(expr)
        assert result == {"exam_year": {"$gte": 2024}}

    def test_lte(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("lte", "metadata.exam_year", 2026)
        result = knowledge._translate(expr)
        assert result == {"exam_year": {"$lte": 2026}}

    def test_gt(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("gt", "metadata.exam_year", 2020)
        result = knowledge._translate(expr)
        assert result == {"exam_year": {"$gt": 2020}}

    def test_lt(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("lt", "metadata.exam_year", 2030)
        result = knowledge._translate(expr)
        assert result == {"exam_year": {"$lt": 2030}}

    # ── in / not in ──────────────────────────────────────────────────────────

    def test_in(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("in", "metadata.source_type", ["exam", "homework"])
        result = knowledge._translate(expr)
        assert result == {"source_type": {"$in": ["exam", "homework"]}}

    def test_not_in(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("not in", "metadata.source_type", ["practice"])
        result = knowledge._translate(expr)
        assert result == {"source_type": {"$nin": ["practice"]}}

    # ── like / not like ──────────────────────────────────────────────────────

    def test_like_fallback_to_eq(self, knowledge: GaokaoKnowledge):
        """Chroma 不支持 LIKE，降级为精确匹配。"""
        expr = self._make_expr("like", "metadata.title", "2026%")
        result = knowledge._translate(expr)
        assert result == {"title": "2026%"}

    def test_not_like_fallback_to_eq(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("not like", "metadata.title", "%模拟%")
        result = knowledge._translate(expr)
        assert result == {"title": "%模拟%"}

    # ── between ──────────────────────────────────────────────────────────────

    def test_between(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("between", "metadata.exam_year", [2024, 2026])
        result = knowledge._translate(expr)
        assert result == {"exam_year": {"$gte": 2024, "$lte": 2026}}

    # ── contains ─────────────────────────────────────────────────────────────

    def test_contains_array_field(self, knowledge: GaokaoKnowledge):
        """数组字段 $contains 匹配（topic_tags / exam_regions）。"""
        expr = KnowledgeFilterExpr.model_construct(
            operator="contains",
            field="metadata.topic_tags",
            value="椭圆",
        )
        result = knowledge._translate(expr)
        assert result == {"topic_tags": {"$contains": "椭圆"}}

    def test_contains_exam_regions(self, knowledge: GaokaoKnowledge):
        expr = KnowledgeFilterExpr.model_construct(
            operator="contains",
            field="metadata.exam_regions",
            value="南昌",
        )
        result = knowledge._translate(expr)
        assert result == {"exam_regions": {"$contains": "南昌"}}

    # ── and / or 嵌套 ────────────────────────────────────────────────────────

    def test_and_two_conditions(self, knowledge: GaokaoKnowledge):
        expr = KnowledgeFilterExpr.model_construct(
            operator="and",
            value=[
                KnowledgeFilterExpr.model_construct(operator="eq", field="metadata.subject", value="数学"),
                KnowledgeFilterExpr.model_construct(operator="eq", field="metadata.source_type", value="exam"),
            ],
        )
        result = knowledge._translate(expr)
        assert result == {
            "$and": [
                {"subject": {"$eq": "数学"}},
                {"source_type": {"$eq": "exam"}},
            ]
        }

    def test_or_two_conditions(self, knowledge: GaokaoKnowledge):
        expr = KnowledgeFilterExpr.model_construct(
            operator="or",
            value=[
                KnowledgeFilterExpr.model_construct(operator="eq", field="metadata.source_type", value="exam"),
                KnowledgeFilterExpr.model_construct(operator="eq", field="metadata.source_type", value="homework"),
            ],
        )
        result = knowledge._translate(expr)
        assert result == {
            "$or": [
                {"source_type": {"$eq": "exam"}},
                {"source_type": {"$eq": "homework"}},
            ]
        }

    def test_nested_and_or(self, knowledge: GaokaoKnowledge):
        """三层嵌套：(A AND B) OR C。"""
        expr = KnowledgeFilterExpr.model_construct(
            operator="or",
            value=[
                KnowledgeFilterExpr.model_construct(
                    operator="and",
                    value=[
                        KnowledgeFilterExpr.model_construct(operator="eq", field="metadata.subject", value="数学"),
                        KnowledgeFilterExpr.model_construct(operator="gte", field="metadata.exam_year", value=2024),
                    ],
                ),
                KnowledgeFilterExpr.model_construct(operator="eq", field="metadata.source_type", value="notes"),
            ],
        )
        result = knowledge._translate(expr)
        assert result == {
            "$or": [
                {
                    "$and": [
                        {"subject": {"$eq": "数学"}},
                        {"exam_year": {"$gte": 2024}},
                    ]
                },
                {"source_type": {"$eq": "notes"}},
            ]
        }

    def test_and_with_contains(self, knowledge: GaokaoKnowledge):
        """AND 组合：subject=数学 AND topic_tags contains 椭圆。"""
        expr = KnowledgeFilterExpr.model_construct(
            operator="and",
            value=[
                KnowledgeFilterExpr.model_construct(operator="eq", field="metadata.subject", value="数学"),
                KnowledgeFilterExpr.model_construct(
                    operator="contains",
                    field="metadata.topic_tags",
                    value="椭圆",
                ),
            ],
        )
        result = knowledge._translate(expr)
        assert result == {
            "$and": [
                {"subject": {"$eq": "数学"}},
                {"topic_tags": {"$contains": "椭圆"}},
            ]
        }

    # ── metadata. 前缀去除 ───────────────────────────────────────────────────

    def test_metadata_prefix_stripped(self, knowledge: GaokaoKnowledge):
        expr = self._make_expr("eq", "metadata.subject", "数学")
        result = knowledge._translate(expr)
        assert "subject" in result
        assert "metadata.subject" not in result

    def test_no_metadata_prefix_passthrough(self, knowledge: GaokaoKnowledge):
        """无 metadata. 前缀的字段名直接使用。"""
        expr = self._make_expr("eq", "subject", "数学")
        result = knowledge._translate(expr)
        assert result == {"subject": {"$eq": "数学"}}


# ═══════════════════════════════════════════════════════════════════════════════
# build_search_extra_params 单元测试
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildSearchExtraParams:

    def test_none_returns_empty(self, knowledge: GaokaoKnowledge):
        result = knowledge.build_search_extra_params(None)
        assert result == {}

    def test_eq_returns_where_wrapper(self, knowledge: GaokaoKnowledge):
        expr = KnowledgeFilterExpr.model_construct(
            operator="eq", field="metadata.subject", value="数学"
        )
        result = knowledge.build_search_extra_params(expr)
        assert "filter" in result
        assert result["filter"] == {"subject": {"$eq": "数学"}}

    def test_contains_returns_where_wrapper(self, knowledge: GaokaoKnowledge):
        expr = KnowledgeFilterExpr.model_construct(
            operator="contains", field="metadata.topic_tags", value="椭圆"
        )
        result = knowledge.build_search_extra_params(expr)
        assert result == {"filter": {"topic_tags": {"$contains": "椭圆"}}}

    def test_and_returns_nested_where(self, knowledge: GaokaoKnowledge):
        expr = KnowledgeFilterExpr.model_construct(
            operator="and",
            value=[
                KnowledgeFilterExpr.model_construct(operator="eq", field="metadata.subject", value="数学"),
                KnowledgeFilterExpr.model_construct(operator="gte", field="metadata.exam_year", value=2024),
            ],
        )
        result = knowledge.build_search_extra_params(expr)
        assert result == {
            "filter": {
                "$and": [
                    {"subject": {"$eq": "数学"}},
                    {"exam_year": {"$gte": 2024}},
                ]
            }
        }

    def test_in_returns_where_wrapper(self, knowledge: GaokaoKnowledge):
        expr = KnowledgeFilterExpr.model_construct(
            operator="in", field="metadata.source_type", value=["exam", "homework"]
        )
        result = knowledge.build_search_extra_params(expr)
        assert result == {"filter": {"source_type": {"$in": ["exam", "homework"]}}}

    def test_between_returns_where_wrapper(self, knowledge: GaokaoKnowledge):
        expr = KnowledgeFilterExpr.model_construct(
            operator="between", field="metadata.exam_year", value=[2024, 2026]
        )
        result = knowledge.build_search_extra_params(expr)
        assert result == {"filter": {"exam_year": {"$gte": 2024, "$lte": 2026}}}

    def test_unsupported_operator_raises(self, knowledge: GaokaoKnowledge):
        """未知操作符应 raise ValueError。"""
        expr = KnowledgeFilterExpr.model_construct(
            operator="unknown_op", field="metadata.x", value="y"
        )
        with pytest.raises(ValueError, match="不支持的过滤表达式操作符"):
            knowledge.build_search_extra_params(expr)


# ═══════════════════════════════════════════════════════════════════════════════
# search 把 where 透传给 vectorstore.asearch（mock 验证）
# ═══════════════════════════════════════════════════════════════════════════════


class TestSearchPassesWhere:

    @pytest.mark.asyncio
    async def test_search_passes_where_to_asearch(self, knowledge: GaokaoKnowledge):
        """search 把 build_search_extra_params 的结果作为 where 传给 asearch。"""
        ctx = _make_ctx()
        req = _make_request(
            query_text="椭圆离心率",
            extra_params={
                "langchain": {
                    "filter": {"topic_tags": {"$contains": "椭圆"}}
                }
            },
        )
        await knowledge.search(ctx, req)
        call_kwargs = knowledge._mock_vs.asearch.call_args[1]
        assert call_kwargs["filter"] == {"topic_tags": {"$contains": "椭圆"}}
        assert call_kwargs["query"] == "椭圆离心率"

    @pytest.mark.asyncio
    async def test_search_passes_k_and_search_type(self, knowledge: GaokaoKnowledge):
        """search 正确传递 k 和 search_type。"""
        ctx = _make_ctx()
        req = _make_request(
            query_text="函数最值",
            search_type="similarity_score_threshold",
            rank_top_k=10,
            extra_params={},
        )
        knowledge._mock_vs.asimilarity_search_with_relevance_scores.return_value = []
        await knowledge.search(ctx, req)
        knowledge._mock_vs.asimilarity_search_with_relevance_scores.assert_awaited_once_with(
            query="函数最值", k=10
        )

    @pytest.mark.asyncio
    async def test_search_result_conversion(self, knowledge: GaokaoKnowledge):
        """search 结果正确转换为 SearchResult（含 score）。"""
        docs_with_scores = [
            (Document(page_content="椭圆离心率题", metadata={"doc_id": "q_1"}), 0.92),
            (Document(page_content="抛物线题", metadata={"doc_id": "q_2"}), 0.85),
        ]
        knowledge._mock_vs.asearch.return_value = docs_with_scores

        ctx = _make_ctx()
        req = _make_request(query_text="几何", extra_params={})
        result: SearchResult = await knowledge.search(ctx, req)

        assert len(result.documents) == 2
        assert result.documents[0].score == 0.92
        assert result.documents[0].document.page_content == "椭圆离心率题"
        assert result.documents[1].score == 0.85

    @pytest.mark.asyncio
    async def test_search_no_prompt_template(self, knowledge: GaokaoKnowledge):
        """GaokaoKnowledge 不设 prompt_template，query 不被改写。"""
        assert knowledge.prompt_template is None

    @pytest.mark.asyncio
    async def test_search_uses_vectorstore_not_chain(self, knowledge: GaokaoKnowledge):
        """GaokaoKnowledge 使用 vectorstore 路径，不走 chain。"""
        assert knowledge.chain is None
        assert knowledge.vectorstore is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 单例工厂
# ═══════════════════════════════════════════════════════════════════════════════


class TestSingletonFactory:

    def test_get_knowledge_returns_instance(self):
        """get_knowledge() 返回 GaokaoKnowledge 实例。"""
        knowledge = get_knowledge()
        assert isinstance(knowledge, GaokaoKnowledge)

    def test_get_knowledge_is_same_instance(self):
        """多次调用返回同一实例。"""
        k1 = get_knowledge()
        k2 = get_knowledge()
        assert k1 is k2


# ═══════════════════════════════════════════════════════════════════════════════
# 真实集成测试（FakeEmbeddings + 真实 Chroma）
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntegration:

    @pytest.fixture()
    def real_knowledge(self):
        """使用真实 Chroma（config 目录）+ FakeEmbeddings 的 GaokaoKnowledge。"""
        fake_emb = FakeEmbeddings()

        vector_store = VectorStore(
            collection_name="gaokao_test",
            persist_dir=config.store.chroma_dir,
            expected_dim=config.embedding.dimension,
            embedding_function=fake_emb,
        )

        with patch("src.retrieval.knowledge.get_embedding_model") as mock_emb, \
             patch("src.retrieval.knowledge.get_vector_store") as mock_vs_store:
            mock_emb.return_value = fake_emb
            mock_vs_store.return_value.vectorstore = vector_store.vectorstore
            knowledge = GaokaoKnowledge()
            # 注入真实 vectorstore（用于 search 调用）
            knowledge.vectorstore = vector_store.vectorstore  # type: ignore[attr-defined]
            yield knowledge

    @pytest.mark.asyncio
    async def test_filter_by_topic_tags_contains(self, real_knowledge: GaokaoKnowledge):
        """真实检索：topic_tags contains '椭圆' 过滤正确。"""
        # 写入数据
        docs = [
            {
                "doc_id": "q_1",
                "text": "椭圆离心率计算题",
                "metadata": {
                    "doc_type": "question",
                    "subject": "数学",
                    "source_type": "exam",
                    "topic_tags": ["椭圆", "离心率"],
                    "exam_year": 2026,
                },
            },
            {
                "doc_id": "q_2",
                "text": "抛物线焦点弦长题",
                "metadata": {
                    "doc_type": "question",
                    "subject": "数学",
                    "source_type": "exam",
                    "topic_tags": ["抛物线"],
                    "exam_year": 2025,
                },
            },
            {
                "doc_id": "kn_1",
                "text": "椭圆定义讲解",
                "metadata": {
                    "doc_type": "note",
                    "subject": "数学",
                    "source_type": "notes",
                    "topic_tags": ["椭圆"],
                },
            },
        ]
        # Write using the knowledge's vectorstore directly
        vs_vec = real_knowledge.vectorstore
        chroma_docs = [
            Document(page_content=d["text"], metadata={**d["metadata"], "doc_id": d["doc_id"]})
            for d in docs
        ]
        vs_vec.add_documents(documents=chroma_docs, ids=["q_1", "q_2", "kn_1"])

        # 构造过滤条件：topic_tags contains "椭圆"
        filter_expr = KnowledgeFilterExpr.model_construct(
            operator="and",
            value=[
                KnowledgeFilterExpr.model_construct(
                    operator="contains",
                    field="metadata.topic_tags",
                    value="椭圆",
                ),
                KnowledgeFilterExpr.model_construct(
                    operator="eq",
                    field="metadata.subject",
                    value="数学",
                ),
            ],
        )

        extra_params = real_knowledge.build_search_extra_params(filter_expr)

        ctx = _make_ctx()
        req = _make_request(
            query_text="椭圆",
            extra_params={"langchain": extra_params},
        )
        result: SearchResult = await real_knowledge.search(ctx, req)

        # 应命中 q_1（椭圆+exam）和 kn_1（椭圆+notes），不命中 q_2（抛物线）
        doc_ids = {d.document.metadata["doc_id"] for d in result.documents}
        assert "q_1" in doc_ids
        assert "kn_1" in doc_ids
        assert "q_2" not in doc_ids

    @pytest.mark.asyncio
    async def test_filter_by_exam_year_range(self, real_knowledge: GaokaoKnowledge):
        """真实检索：exam_year gte 2024 过滤正确。"""
        vs_vec = real_knowledge.vectorstore
        docs = [
            {
                "doc_id": "q_2023",
                "text": "2023年高考题",
                "metadata": {
                    "doc_type": "question",
                    "subject": "数学",
                    "source_type": "exam",
                    "exam_year": 2023,
                },
            },
            {
                "doc_id": "q_2025",
                "text": "2025年高考题",
                "metadata": {
                    "doc_type": "question",
                    "subject": "数学",
                    "source_type": "exam",
                    "exam_year": 2025,
                },
            },
            {
                "doc_id": "q_2026",
                "text": "2026年高考题",
                "metadata": {
                    "doc_type": "question",
                    "subject": "数学",
                    "source_type": "exam",
                    "exam_year": 2026,
                },
            },
        ]
        chroma_docs = [
            Document(page_content=d["text"], metadata={**d["metadata"], "doc_id": d["doc_id"]})
            for d in docs
        ]
        vs_vec.add_documents(documents=chroma_docs, ids=["q_2023", "q_2025", "q_2026"])

        filter_expr = KnowledgeFilterExpr.model_construct(
            operator="gte",
            field="metadata.exam_year",
            value=2024,
        )
        extra_params = real_knowledge.build_search_extra_params(filter_expr)

        ctx = _make_ctx()
        req = _make_request(
            query_text="高考",
            extra_params={"langchain": extra_params},
        )
        result: SearchResult = await real_knowledge.search(ctx, req)

        doc_ids = {d.document.metadata["doc_id"] for d in result.documents}
        assert "q_2025" in doc_ids
        assert "q_2026" in doc_ids
        assert "q_2023" not in doc_ids

    @pytest.mark.asyncio
    async def test_no_filter_returns_all(self, real_knowledge: GaokaoKnowledge):
        """无过滤条件时返回全部结果。"""
        vs_vec = real_knowledge.vectorstore
        docs = [
            Document(page_content="数学题1", metadata={"doc_id": "q_a", "subject": "数学"}),
            Document(page_content="数学题2", metadata={"doc_id": "q_b", "subject": "数学"}),
        ]
        vs_vec.add_documents(documents=docs, ids=["q_a", "q_b"])

        ctx = _make_ctx()
        req = _make_request(query_text="数学", extra_params={})
        result: SearchResult = await real_knowledge.search(ctx, req)

        assert len(result.documents) == 2
