# tests/conftest.py
# 测试隔离体系（唯一入口）：每测试【前】清空全部业务数据 + 重置单例 + patch 假嵌入。
#
# 数据策略（2026-08 决策）：
#   - 不做"测试数据隔离"——测试直接使用 config 真实路径（data/gaokao.db、
#     data/chroma_db、data/files）。开发阶段 data/ 不会有重要数据，测试清掉是
#     预期行为，放心清理重置、不再提示；生产环境不跑测试。
#   - _reset_state 在【测试前】执行，每个测试从确定状态开始，测试间无顺序依赖。
#   - 绝对路径功能测试（TestAbsoluteDataDir）例外保留 tmp_path——它需要"项目外
#     绝对目录"作输入，tmp_path 是测试输入沙箱而非数据隔离，不写 data/ 不冲突。

from __future__ import annotations

import os
import shutil
import sqlite3
import time

import chromadb
import pytest
from langchain_core.embeddings import Embeddings

import src.store.db.files as _files_mod
import src.store.db.questions as _questions_mod
import src.store.db.topics as _topics_mod
import src.store.db.question_topics as _qt_mod
import src.store.file_store as _file_store_mod
import src.retrieval.knowledge as _knowledge_mod
import src.store.vector.vector_store as _vector_store_mod
from src.config import config
from src.store.db import get_shared_conn, reset_schema_tracking
from src.store.file_store import FileStore
from src.store.vector.vector_store import VectorStore


class FakeEmbeddings(Embeddings):
    """测试用假嵌入模型，维度取自 ``config.embedding.dimension``，不真调 DashScope API。"""

    def __init__(self, size: int | None = None) -> None:
        self._dim = size if size is not None else config.embedding.dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self._dim for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * self._dim


# 清空顺序 = 外键依赖逆序（子表先删；foreign_keys=ON 下先删父表会报错）
_BUSINESS_TABLES = (
    "errors",
    "question_topics",
    "questions",
    "exam_attempts",
    "topics",
    "review_plans",
    "periodic_reports",
    "files",
)


def _clear_file_store_subdirs() -> None:
    """清空 FileStore 5 个子目录下的文件（保留目录本身）。"""
    fs = FileStore()
    for d in fs._subdirs.values():
        for p in d.iterdir():
            if p.is_file():
                p.unlink()


# 所有 VectorStore 实例登记册：chroma 的 sqlite 文件锁在 GC 时不释放（实测
# del + gc.collect 后仍 LOCKED），只有显式 client.close() 才释放。直接构造的实例
# （TestUpsertDocument / TestDimensionGuard 的 store1/store2 等）不入 _instance 单例，
# 必须入册，reset 时才能统一关闭，rmtree 才能成功。
_vector_store_instances: list[VectorStore] = []


def _close_tracked_chroma_clients() -> None:
    """关闭本次 reset 前创建过的全部 chroma client（Windows 文件锁下 rmtree 需要先释放）。

    对 mock（test_knowledge 的 mock_vs）无副作用；已 close 的 client 幂等跳过。
    """
    for holder in list(_vector_store_instances):
        vectorstore = getattr(holder, "vectorstore", None)
        client = getattr(vectorstore, "_client", None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    # 单例兜底：GaokaoKnowledge._instance 可能持有未入册的 vectorstore
    knowledge = _knowledge_mod._instance
    vectorstore = getattr(knowledge, "vectorstore", None)
    client = getattr(vectorstore, "_client", None)
    if client is not None:
        try:
            client.close()
        except Exception:
            pass
    _vector_store_instances.clear()


def _wipe_chroma_dir() -> None:
    """rmtree 整体清空 chroma 目录。

    规避 chroma 1.5.9 的 bug：delete_collection 删同名 collection 再建时残留旧
    segment 元数据（count=0 但新 doc 合并旧字段）。带 3 次重试（Windows 偶发瞬时
    锁）；全失败才回退 delete_collection（文件锁异常环境下的兜底，对锁不敏感）。
    """
    if os.path.isdir(config.store.chroma_dir):
        for _ in range(3):
            try:
                shutil.rmtree(config.store.chroma_dir)
                break
            except OSError:
                time.sleep(0.2)
        else:
            client = chromadb.PersistentClient(path=config.store.chroma_dir)
            for col in list(client.list_collections()):
                client.delete_collection(col.name)
            client.close()
    os.makedirs(config.store.chroma_dir, exist_ok=True)


@pytest.fixture(scope="session", autouse=True)
def _track_vector_store_instances() -> None:
    """给 ``VectorStore.__init__`` 挂登记钩子：所有构造的实例入册，reset 时统一 close()。

    只做簿记，不改行为；会话结束还原 __init__。
    """
    original_init = VectorStore.__init__

    def tracked_init(self, *args, **kwargs):
        try:
            original_init(self, *args, **kwargs)
        finally:
            _vector_store_instances.append(self)  # 含 __init__ 中途 raise 的部分实例

    VectorStore.__init__ = tracked_init
    try:
        yield
    finally:
        VectorStore.__init__ = original_init


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """每个测试【前】清空全部业务数据 + 重置单例 + 条件 patch 假嵌入。

    带 integration 标记的测试（真实 API，会计费）不 patch 嵌入层，让嵌入真打
    云端 API；其余照常 patch FakeEmbeddings。数据清空/单例重置对两类测试都执行，
    保证每次从干净状态开始。不关闭共享 SQLite 连接（保持 WAL + foreign_keys
    配置），仅清数据保 schema。
    """
    # 1. SQLite：清空全部业务表（缺失表静默跳过）
    conn = get_shared_conn()
    for table in _BUSINESS_TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass  # 表尚未创建
    conn.commit()

    # 2. Chroma：先关闭全部残留 client（GC 不释放 chroma 的 sqlite 文件锁，必须显式
    #    close()），再整体清空目录重建 —— 规避 chroma 1.5.9 删同名 collection 的
    #    segment 元数据残留 bug（count=0 但新 doc 合并旧字段）
    _close_tracked_chroma_clients()
    _wipe_chroma_dir()

    # 3. 重置全部单例 + 基类 schema 初始化记录（保证下次构造基于干净状态；
    #    schema 追踪已集中到 SQLiteTableDB._initialized，一处 reset 顶原来逐模块 clear）
    _files_mod._files_db = None
    _questions_mod._questions_db = None
    _topics_mod._topics_db = None
    _qt_mod._question_topics_db = None
    reset_schema_tracking()
    _vector_store_mod._instance = None
    _knowledge_mod._instance = None
    _file_store_mod._file_store = None

    # 3b. 确保 SQLite schema 存在：clean env（CI 无残留 data/gaokao.db）下标从未
    #     被创建，而第 1 步的 DELETE 对缺失表静默跳过、无法兜底建表。questions /
    #     question_topics 的 FK 又引用 files/questions，插入前依赖表必须先存在。
    #     注意 get_*_db() 只返回空壳单例（构造不建表），必须真实触碰 _connect()
    #     才会触发基类 SQLiteTableDB._init_schema 的 CREATE TABLE IF NOT EXISTS。
    #     顺序 = FK 依赖序：files → questions → topics → question_topics。
    _files_mod.get_files_db()._connect()
    _questions_mod.get_questions_db()._connect()
    _topics_mod.get_topics_db()._connect()
    _qt_mod.get_question_topics_db()._connect()

    # 4. FileStore：清空 5 个子目录下的文件（保留目录本身）
    _clear_file_store_subdirs()

    # 5. patch 假嵌入：除 integration 测试（真调云端嵌入，会计费）外，任何无参
    #    get_vector_store() / get_knowledge() 构造都用 FakeEmbeddings，不真调 API。
    #    三个目标都要替换（缺一即漏）：
    #       - src.api.embedding：真实工厂入口（防未来模块 from-import 时绑定）
    #       - src.store.vector.vector_store / src.retrieval.knowledge：两个消费方模块的
    #         from-import 绑定名 —— Python 在 import 时把函数对象绑进各模块全局，
    #         只 patch 源模块不会重绑已导入模块的全局名（实测验证）
    if not request.node.get_closest_marker("integration"):
        fake_factory = lambda: FakeEmbeddings(config.embedding.dimension)
        monkeypatch.setattr("src.api.embedding.get_embedding_model", fake_factory)
        monkeypatch.setattr(
            "src.store.vector.vector_store.get_embedding_model", fake_factory
        )
        monkeypatch.setattr(
            "src.retrieval.knowledge.get_embedding_model", fake_factory
        )


# ── 公共 fixtures ──────────────────────────────────────────────────


@pytest.fixture()
def files_db():
    """``files`` 表单例（连接统一走共享 SQLite）。"""
    return _files_mod.get_files_db()


@pytest.fixture()
def questions_db():
    """``questions`` 表单例（连接统一走共享 SQLite）。"""
    return _questions_mod.get_questions_db()


@pytest.fixture()
def topics_db():
    """``topics`` 表单例（连接统一走共享 SQLite）。"""
    return _topics_mod.get_topics_db()


@pytest.fixture()
def question_topics_db():
    """``question_topics`` 表单例（连接统一走共享 SQLite）。"""
    return _qt_mod.get_question_topics_db()


@pytest.fixture()
def file_store() -> FileStore:
    """FileStore 实例（config 真实目录，conftest 每测试前清空）。"""
    return FileStore()


@pytest.fixture(scope="session")
def fake_embeddings() -> FakeEmbeddings:
    """共享假嵌入实例（无状态，维度 = config.embedding.dimension）。"""
    return FakeEmbeddings()


@pytest.fixture()
def vector_store() -> VectorStore:
    """VectorStore 实例（config 目录持久化 + FakeEmbeddings）。

    同时写入 ``_instance``，使测试内 ``get_vector_store()`` 复用同一实例。
    """
    vs = VectorStore(
        collection_name=config.store.collection_name,
        persist_dir=config.store.chroma_dir,
        expected_dim=config.embedding.dimension,
        embedding_function=FakeEmbeddings(config.embedding.dimension),
    )
    _vector_store_mod._instance = vs
    return vs
