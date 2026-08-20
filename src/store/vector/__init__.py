# src/store/vector/__init__.py
# 向量存储包：Chroma 封装 + 懒单例工厂。
from src.store.vector.vector_store import VectorStore, get_vector_store

__all__ = ["VectorStore", "get_vector_store"]
