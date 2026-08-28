# src/store/vector/__init__.py
# 向量存储包：Chroma 封装 + 懒单例工厂（存储层原语；知识检索组件已归位
# src/retrieval/knowledge.py，2026-08-28 组件归位）。
from src.store.vector.vector_store import VectorStore, get_vector_store

__all__ = [
    "VectorStore",
    "get_vector_store",
]
