# src/store/vector/vector_store.py
# Layer 3a：Chroma 向量存储封装，负责语义检索的写入侧。
#
# 设计：
#   - _instance  — 模块级懒单例（VectorStore 实例）
#   - VectorStore — Chroma 封装，提供 upsert / search / delete / get / count
#   - get_vector_store() — 懒初始化单例，读 config.toml 的 [store] + [embedding]
#
# 核心约束：
#   - 只用 langchain_chroma.Chroma，不直接 import chromadb
#   - doc_id 两段式（q_42 / kn_7），幂等 upsert（先删后加）
#   - 维度防呆：初始化时若 collection 已有向量且维度 != expected_dim，raise RuntimeError
#   - metadata 只存检索快照（doc_id/doc_type/subject/source_type/title/
#     topic_tags/exam_regions/exam_year/question_type/has_image）
#   - persist_directory 必须显式传
#   - where 参数透传 langchain Filter dict 或 chromadb 原生 where dict
#     （数组 $contains 需原生透传，翻译层由 knowledge.py 子类负责）
#
# 用法：
#     from src.store.vector import get_vector_store
#     vs = get_vector_store()
#     vs.upsert("q_42", "题干+答案+解析", {"subject": "数学", "topic_tags": ["椭圆"]})
#     results = vs.search("求函数最小值", k=5, where={"subject": "数学"})

from __future__ import annotations

from typing import Any, cast

from langchain_chroma import Chroma
from langchain_core.documents import Document
from trpc_agent_sdk.log import logger

from src.api.embedding import get_embedding_model
from src.config import config

# ═══════════════════════════════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════════════════════════════

_instance: VectorStore | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# 核心类
# ═══════════════════════════════════════════════════════════════════════════════


class VectorStore:
    """Chroma 向量存储封装，负责高考知识库的语义检索写入侧。

    Collection 全科共用（默认名 "gaokao"），通过 metadata.subject 过滤学科。
    doc_id 两段式（``q_42`` / ``kn_7``），写入时先删后加实现幂等 upsert。

    Attributes:
        collection_name: Chroma collection 名。
        persist_dir: 持久化目录路径。
        expected_dim: 期望向量维度（初始化时校验一致性）。
        vectorstore: 底层 ``langchain_chroma.Chroma`` 实例，
            供 ``LangchainKnowledge`` 注入（检索侧消费）。
    """

    def __init__(
        self,
        collection_name: str | None = None,
        persist_dir: str | None = None,
        expected_dim: int | None = None,
        embedding_function: Any = None,
    ) -> None:
        """初始化 VectorStore。

        未传参时从 ``config`` 读取默认值：
        - ``collection_name`` → ``config.store.collection_name``
        - ``persist_dir``     → ``config.store.chroma_dir``
        - ``expected_dim``    → ``config.embedding.dimension``

        Args:
            collection_name: Chroma collection 名，默认 ``config.store.collection_name``。
            persist_dir: 持久化目录，默认 ``config.store.chroma_dir``。
            expected_dim: 期望向量维度，默认 ``config.embedding.dimension``。
            embedding_function: 嵌入函数实例（测试等场景可注入 ``FakeEmbeddings``；
                生产环境默认 ``None`` → 调用 ``get_embedding_model()`` 单例）。

        Raises:
            RuntimeError: 若 collection 已有向量且维度与 ``expected_dim`` 不一致。
        """
        _cn = collection_name or config.store.collection_name
        _pd = persist_dir or config.store.chroma_dir
        _ed = expected_dim if expected_dim is not None else config.embedding.dimension

        self._collection_name = _cn
        self._persist_dir = _pd
        self._expected_dim = _ed

        if embedding_function is None:
            embedding_function = get_embedding_model()

        self.vectorstore = Chroma(
            collection_name=_cn,
            embedding_function=embedding_function,
            persist_directory=_pd,
        )

        # 维度防呆：collection 已有向量时检查维度是否一致
        self._check_dimension()

    # ── 写入（幂等） ──────────────────────────────────────────────

    def upsert(self, doc_id: str, text: str, metadata: dict) -> None:
        """写入/更新单条 document（doc_id 幂等：同 id 覆盖，不重复）。

        实现：先删后加（Chroma 不支持原生 upsert）。

        Args:
            doc_id: 两段式 document id（``q_42`` / ``kn_7``）。
            text: 文档文本内容（题干+答案+解析 或 知识点讲解段）。
            metadata: 检索快照字段（仅存 str/int/float/bool/同类型数组），
                不存内容。``doc_id`` 会被自动注入 metadata。
        """
        existing = self.vectorstore.get(ids=[doc_id])
        if existing["ids"]:
            self.vectorstore.delete(ids=[doc_id])

        meta = dict(metadata)
        meta["doc_id"] = doc_id
        doc = Document(page_content=text, metadata=meta)
        self.vectorstore.add_documents(documents=[doc], ids=[doc_id])

    def upsert_many(self, docs: list[dict | Document]) -> None:
        """批量写入/更新（doc_id 幂等）。

        支持两种输入格式：
        - ``list[dict]``：``[{"doc_id": str, "text": str, "metadata": dict}, ...]``（原有行为）
        - ``list[Document]``：LangChain Document 对象列表（doc_id 必须在 metadata 中）

        Args:
            docs: dict 列表或 Document 列表。空列表静默返回。

        Raises:
            TypeError: 若列表元素类型不统一（混合 dict 和 Document）。
            ValueError: 若 Document 的 metadata 缺少 ``doc_id``。
        """
        if not docs:
            return

        if isinstance(docs[0], Document):
            doc_list = cast(list[Document], docs)
            self.upsert_documents(doc_list)
        elif isinstance(docs[0], dict):
            dict_list = cast(list[dict], docs)
            doc_ids = [d["doc_id"] for d in dict_list]
            existing = self.vectorstore.get(ids=doc_ids)
            if existing["ids"]:
                self.vectorstore.delete(ids=existing["ids"])
            documents = [
                Document(page_content=d["text"], metadata={**d["metadata"], "doc_id": d["doc_id"]})
                for d in dict_list
            ]
            self.vectorstore.add_documents(documents=documents, ids=doc_ids)
        else:
            raise TypeError(
                f"upsert_many 不支持 {type(docs[0]).__name__} 类型，"
                "请使用 list[dict] 或 list[Document]"
            )

    # ── 写入（Document 接口）───────────────────────────────────────

    def upsert_document(self, doc: Document) -> None:
        """写入/更新单条 LangChain Document（doc_id 幂等）。

        Args:
            doc: LangChain Document 对象，metadata 中必须包含 ``"doc_id"`` 字段。

        Raises:
            ValueError: 若 ``doc.metadata`` 缺少 ``"doc_id"``。
        """
        doc_id = doc.metadata.get("doc_id")
        if doc_id is None:
            raise ValueError("Document metadata must contain 'doc_id'")
        metadata = dict(doc.metadata)
        self.upsert(doc_id=doc_id, text=doc.page_content, metadata=metadata)

    def upsert_documents(self, docs: list[Document]) -> None:
        """批量写入/更新 LangChain Document 列表（doc_id 幂等）。

        直接委托给 ``add_documents``，Chroma 内部按 ``chunk_size`` 自动分批。

        Args:
            docs: Document 对象列表。空列表静默返回。
        """
        if not docs:
            return

        doc_ids: list[str] = []
        missing: list[dict] = []
        for d in docs:
            did = d.metadata.get("doc_id")
            if did is None:
                missing.append(dict(d.metadata))
            else:
                doc_ids.append(did)

        if missing:
            raise ValueError(
                f"以下 Document 缺少 doc_id: {missing}"
            )

        existing = self.vectorstore.get(ids=doc_ids)
        if existing["ids"]:
            self.vectorstore.delete(ids=existing["ids"])

        # copy metadata 后注入 doc_id，不修改原始 Document 对象
        prepared = []
        for i, d in enumerate(docs):
            meta = dict(d.metadata)
            meta.setdefault("doc_id", doc_ids[i])
            prepared.append(Document(page_content=d.page_content, metadata=meta))

        self.vectorstore.add_documents(documents=prepared, ids=doc_ids)

    # ── 查询 ───────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: int = 5,
        where: dict | None = None,
    ) -> list[tuple[Document, float]]:
        """语义检索 + metadata 过滤。

        通过 ``similarity_search_with_relevance_scores`` 实现，
        返回 (Document, relevance_score) 列表，按相似度降序。

        Args:
            query: 查询文本（会被自动嵌入后做向量相似度比较）。
            k: 返回 Top-K 结果（默认 5）。
            where: metadata 过滤条件。支持两种格式：
                - langchain Filter dict（``{"$eq": ...}`` / ``{"$in": ...}``）
                - chromadb 原生 where dict（含 ``$contains`` 数组操作）
              传 ``None`` 时不过滤。

        Returns:
            (Document, float) 列表，score 范围 [0, 1]（越高越相似）。
        """
        if where is not None:
            return self.vectorstore.similarity_search_with_relevance_scores(
                query, k=k, filter=where
            )
        return self.vectorstore.similarity_search_with_relevance_scores(query, k=k)

    # ── 删除/获取/统计 ───────────────────────────────────────────

    def delete(self, doc_ids: list[str]) -> None:
        """删除 document（按 doc_id）。

        Args:
            doc_ids: 要删除的 doc_id 列表。空列表静默返回。
        """
        if not doc_ids:
            return
        self.vectorstore.delete(ids=doc_ids)

    def get(self, doc_id: str) -> dict | None:
        """按 doc_id 获取 document。

        Args:
            doc_id: 两段式 document id。

        Returns:
            {"doc_id": str, "text": str, "metadata": dict}，
            或 ``None``（不存在时）。
        """
        result = self.vectorstore.get(ids=[doc_id])
        if not result["ids"]:
            return None
        return {
            "doc_id": result["ids"][0],
            "text": result["documents"][0],
            "metadata": result["metadatas"][0],
        }

    def count(self) -> int:
        """Collection 内 document 总数。"""
        return self.vectorstore._collection.count()

    # ── 内部工具 ───────────────────────────────────────────────────

    def _check_dimension(self) -> None:
        """检查 collection 现有向量的维度是否与 expected_dim 一致。

        仅 collection 非空时检查；空 collection 跳过。
        Chroma 返回的 embeddings 是 numpy array，需用 len() 判断非空。
        """
        try:
            raw = self.vectorstore._collection.get(include=["embeddings"])
        except Exception:
            return
        embeddings = raw.get("embeddings")
        # embeddings 可能是 list 或 numpy array；空值时跳过检查
        if embeddings is None:
            return
        try:
            count = len(embeddings)
        except TypeError:
            return
        if count > 0:
            actual_dim = len(embeddings[0])
            if actual_dim != self._expected_dim:
                raise RuntimeError(
                    f"Chroma collection '{self._collection_name}' 现有向量维度 "
                    f"{actual_dim} 与 config embedding.dimension={self._expected_dim} 不一致。"
                    "更换维度需先删除 data/chroma_db 重建。"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton factory
# ═══════════════════════════════════════════════════════════════════════════════


def get_vector_store() -> VectorStore:
    """返回缓存的 VectorStore 单例，读 config.toml 初始化。

    从 ``config.store`` 读取 ``collection_name`` + ``chroma_dir``，
    从 ``config.embedding`` 读取 ``dimension``。

    Returns:
        VectorStore 单例实例。
    """
    global _instance
    if _instance is None:
        _instance = VectorStore(
            collection_name=config.store.collection_name,
            persist_dir=config.store.chroma_dir,
            expected_dim=config.embedding.dimension,
        )
        logger.info(
            "VectorStore initialized: collection=%s, dir=%s, dim=%d",
            config.store.collection_name,
            config.store.chroma_dir,
            config.embedding.dimension,
        )
    return _instance
