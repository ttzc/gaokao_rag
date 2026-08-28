# src/retrieval/knowledge.py
# 读门面·知识检索组件：语义检索的查询侧——将 ``embedder`` + ``vectorstore`` 注入
# 框架 ``LangchainKnowledge``，重写 ``build_search_extra_params`` 将
# ``KnowledgeFilterExpr`` 翻译为 Chroma 原生 where dict。
# （2026-08-28 组件归位：原 src/store/vector/knowledge.py 迁入读门面，见 docs/retrieval/knowledge.md）
#
# 设计：
#   - _instance   — 模块级懒单例（GaokaoKnowledge 实例）
#   - GaokaoKnowledge — LangchainKnowledge 子类，注入 embedder + vectorstore
#   - get_knowledge() — 懒初始化单例
#
# 核心约束：
#   - 不设 ``prompt_template``（避免污染检索向量）
#   - ``vectorstore`` 传持久化单例 ``get_vector_store().vectorstore``
#   - 过滤翻译递归处理 ``and``/``or`` 嵌套
#   - MVP 知识点是扁平 tag，无树展开逻辑
#   - ``contains`` 操作符框架当前未验证，但防御性实现以应对未来扩展
#
# 用法：
#     from src.retrieval.knowledge import get_knowledge
#     knowledge = get_knowledge()
#     # search 由框架调用，build_search_extra_params 由框架在 search 前调用

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.knowledge._filter_expr import KnowledgeFilterExpr
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

from src.api.embedding import get_embedding_model
from src.store.vector.vector_store import get_vector_store

# ═══════════════════════════════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════════════════════════════

_instance: GaokaoKnowledge | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# 核心类
# ═══════════════════════════════════════════════════════════════════════════════


class GaokaoKnowledge(LangchainKnowledge):
    """高考知识库语义检索组件。

    将 ``embedder`` + ``vectorstore`` 注入框架 ``LangchainKnowledge``，
    重写 ``build_search_extra_params`` 将 ``KnowledgeFilterExpr`` 翻译为
    Chroma 原生 where dict（支持 ``$contains`` 数组过滤）。

    不设 ``prompt_template``（避免污染检索向量）。
    """

    def __init__(self) -> None:
        """初始化，注入 embedder + vectorstore 单例。"""
        super().__init__(
            embedder=get_embedding_model(),
            vectorstore=get_vector_store().vectorstore,
        )

    # ── 框架接口 ──────────────────────────────────────────────────────────────

    def build_search_extra_params(
        self, filter_expr: KnowledgeFilterExpr | None
    ) -> dict[str, Any]:
        """将框架 ``KnowledgeFilterExpr`` 翻译为 Chroma where dict。

        Args:
            filter_expr: 框架统一的过滤表达式（可为 None）。

        Returns:
            ``{"filter": chroma_where_dict}``，供 ``LangchainKnowledge.search``
            通过 ``langchain_kwargs`` 透传给 ``vectorstore.asearch``。
            无过滤条件时返回 ``{}``。

        Note:
            虽然 Chroma 原生用 ``where`` 作为查询参数名，但 langchain_chroma
            在 ``similarity_search_with_score`` 中会将 ``filter`` 参数映射为
            ``where`` 传入底层 collection。若直接在 ``**kwargs`` 中传 ``where``
            会与内部 ``where=filter`` 参数冲突。因此返回 ``{"filter": ...}``。
        """
        if filter_expr is None:
            return {}
        return {"filter": self._translate(filter_expr)}

    # ── 递归翻译 ──────────────────────────────────────────────────────────────

    def _translate(self, expr: KnowledgeFilterExpr) -> Any:
        """递归翻译 ``KnowledgeFilterExpr`` → Chroma where dict。

        Chroma where dict 支持的运算符：
            ``$eq`` ``$ne`` ``$gt`` ``$gte`` ``$lt`` ``$lte``
            ``$in`` ``$nin`` ``$contains``
            以及 ``$and`` ``$or`` 逻辑组合。

        Args:
            expr: 框架过滤表达式。

        Returns:
            Chroma where dict（或嵌套 dict）。

        Raises:
            ValueError: 不支持的过滤操作符。
        """
        op = expr.operator
        if op in ("and", "or"):
            return self._translate_logical(expr)
        if op in ("eq", "ne"):
            return self._translate_binary(expr)
        if op in ("gt", "gte", "lt", "lte"):
            return self._translate_range(expr)
        if op in ("in", "not in"):
            return self._translate_in(expr)
        if op in ("like", "not like"):
            return self._translate_like(expr)
        if op == "between":
            return self._translate_between(expr)
        if op == "contains":
            return self._translate_contains(expr)
        raise ValueError(f"不支持的过滤表达式操作符: {op!r}")

    def _translate_logical(self, expr: KnowledgeFilterExpr) -> dict[str, Any]:
        """翻译 ``and`` / ``or`` 逻辑操作符。

        ``expr.value`` 是 ``list[KnowledgeFilterExpr]``（框架 model_validator 转换）。
        """
        key = f"${expr.operator}"
        return {key: [self._translate(child) for child in expr.value]}

    def _translate_binary(self, expr: KnowledgeFilterExpr) -> dict[str, Any]:
        """翻译 ``eq`` / ``ne`` 二元比较操作符。

        ``metadata.subject`` → ``subject``（去掉 ``metadata.`` 前缀）。
        """
        key = expr.field.replace("metadata.", "", 1)
        op = "$ne" if expr.operator == "ne" else "$eq"
        return {key: {op: expr.value}}

    def _translate_range(self, expr: KnowledgeFilterExpr) -> dict[str, Any]:
        """翻译 ``gt`` / ``gte`` / ``lt`` / ``lte`` 范围操作符。"""
        key = expr.field.replace("metadata.", "", 1)
        op_map = {
            "gt": "$gt",
            "gte": "$gte",
            "lt": "$lt",
            "lte": "$lte",
        }
        return {key: {op_map[expr.operator]: expr.value}}

    def _translate_in(self, expr: KnowledgeFilterExpr) -> dict[str, Any]:
        """翻译 ``in`` / ``not in`` 集合操作符。

        ``not in`` → ``$nin``。
        """
        key = expr.field.replace("metadata.", "", 1)
        op = "$nin" if expr.operator == "not in" else "$in"
        return {key: {op: expr.value}}

    def _translate_like(self, expr: KnowledgeFilterExpr) -> dict[str, Any]:
        """翻译 ``like`` / ``not like``——Chroma 原生不支持 LIKE。

        MVP 降级为精确匹配（eq），够用。
        """
        key = expr.field.replace("metadata.", "", 1)
        return {key: expr.value}

    def _translate_between(self, expr: KnowledgeFilterExpr) -> dict[str, Any]:
        """翻译 ``between`` 范围操作符。

        ``expr.value`` 是 ``[low, high]`` 列表。
        """
        key = expr.field.replace("metadata.", "", 1)
        low, high = expr.value
        return {key: {"$gte": low, "$lte": high}}

    def _translate_contains(self, expr: KnowledgeFilterExpr) -> dict[str, Any]:
        """翻译 ``contains`` 数组包含操作符（Chroma 原生 ``$contains``）。

        用于 ``topic_tags`` / ``exam_regions`` 等数组字段的包含匹配。
        注意：框架当前未验证 ``contains`` 操作符（_filter_expr.py），
        防御性实现以应对未来扩展。
        """
        key = expr.field.replace("metadata.", "", 1)
        return {key: {"$contains": expr.value}}


# ═══════════════════════════════════════════════════════════════════════════════
# 单例工厂
# ═══════════════════════════════════════════════════════════════════════════════


def get_knowledge() -> GaokaoKnowledge:
    """返回缓存的 GaokaoKnowledge 单例，懒初始化。

    Returns:
        GaokaoKnowledge 单例实例。
    """
    global _instance
    if _instance is None:
        _instance = GaokaoKnowledge()
    return _instance
