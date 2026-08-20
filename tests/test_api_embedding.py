# tests/test_api_embedding.py
"""src/api/embedding.py 单元测试。

覆盖：
- 白名单 _SUPPORTED_MODELS
- get_embedding_model()         懒初始化、单例、参数透传
- embed_query("...")            单次 API 调用、dimensions == 1024
- embed_documents(25 条短文本)  每批 input 长度 ≤ 20（DashScope 限制）
- 异常路径：未解析 api_key / 不在支持列表
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from langchain_openai import OpenAIEmbeddings

from src.api.embedding import (
    _DASHSCOPE_MAX_INPUTS_PER_REQUEST,
    _SUPPORTED_MODELS,
    get_embedding_model,
)
from src.config import EmbeddingConfig


# ═══════════════════════════════════════════════════════════════════════════════
# 白名单
# ═══════════════════════════════════════════════════════════════════════════════


class TestSupportedModels:
    """模型白名单验证。"""

    def test_supported_models_returns_tuple(self) -> None:
        assert isinstance(_SUPPORTED_MODELS, tuple)

    def test_default_model_in_whitelist(self) -> None:
        """默认模型 qwen3.7-text-embedding 必须在白名单内。"""
        assert EmbeddingConfig().model in _SUPPORTED_MODELS

    def test_whitelist_contains_one_model(self) -> None:
        """当前白名单只有一个模型。"""
        assert len(_SUPPORTED_MODELS) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# get_embedding_model() 单例与参数透传
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetEmbeddingModel:
    """懒初始化单例与参数透传。"""

    def _reset_singleton(self) -> None:
        """重置模块级单例，确保测试隔离。"""
        import src.api.embedding as emb_module
        emb_module._model = None

    def _make_mock_cfg(self) -> MagicMock:
        """构造模拟 EmbeddingConfig。"""
        cfg = MagicMock()
        cfg.model = "qwen3.7-text-embedding"
        cfg.api_key = "sk-dashscope-test"
        cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        cfg.dimension = 1024
        cfg.timeout = 60.0
        return cfg

    def test_first_call_creates_instance(self) -> None:
        """首次调用返回 OpenAIEmbeddings 实例。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            result = get_embedding_model()
        assert isinstance(result, OpenAIEmbeddings)

    def test_second_call_returns_same_instance(self) -> None:
        """第二次调用返回缓存，不重建。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            first = get_embedding_model()
            second = get_embedding_model()
        assert first is second

    def test_model_name_from_config(self) -> None:
        """model 参数从 config 透传。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            result = get_embedding_model()
        assert result.model == "qwen3.7-text-embedding"

    def test_dimensions_explicitly_1024(self) -> None:
        """dimensions 显式传入 1024（硬约束，不依赖模型默认值）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            result = get_embedding_model()
        assert result.dimensions == 1024

    def test_dimensions_custom_value(self) -> None:
        """config 中自定义 dimension 值被正确传入。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_cfg.dimension = 512
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            result = get_embedding_model()
        assert result.dimensions == 512

    def test_base_url_passed(self) -> None:
        """base_url 从 config 透传（存储为 openai_api_base）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            result = get_embedding_model()
        assert result.openai_api_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_chunk_size_is_20(self) -> None:
        """chunk_size 为 20（DashScope 单次请求上限）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            result = get_embedding_model()
        assert result.chunk_size == 20

    def test_check_embedding_ctx_length_disabled(self) -> None:
        """check_embedding_ctx_length=False（跳过 token 化，直接发原始文本）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            result = get_embedding_model()
        assert result.check_embedding_ctx_length is False

    def test_api_key_passed(self) -> None:
        """API Key 从 config 透传（openai_api_key 是 SecretStr，需 get_secret_value() 取值）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            result = get_embedding_model()
        # SecretStr 需通过 get_secret_value() 获取原始值
        assert result.openai_api_key.get_secret_value() == "sk-dashscope-test"

    def test_timeout_passed(self) -> None:
        """request_timeout 从 config 透传。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_cfg.timeout = 30.0
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            result = get_embedding_model()
        assert result.request_timeout == 30.0


# ═══════════════════════════════════════════════════════════════════════════════
# embed_query() — 单文本嵌入
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmbedQuery:
    """embed_query 单文本嵌入。"""

    def _reset_singleton(self) -> None:
        import src.api.embedding as emb_module
        emb_module._model = None

    def _make_mock_cfg(self) -> MagicMock:
        cfg = MagicMock()
        cfg.model = "qwen3.7-text-embedding"
        cfg.api_key = "sk-dashscope-test"
        cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        cfg.dimension = 1024
        cfg.timeout = 60.0
        return cfg

    def _mock_openai_client(self, dims: int = 1024):
        """Patch langchain_openai 底层的 openai 模块，使 API 调用走 mock。

        Returns:
            (mock_openai_module, create_call_records)
        """
        mock_openai = MagicMock()
        call_records: list[dict] = []

        def mock_create(**kwargs):
            input_texts = kwargs.get("input", [])
            call_records.append({
                "input_len": len(input_texts),
                "dimensions": kwargs.get("dimensions"),
            })
            mock_resp = MagicMock()
            mock_resp.model_dump.return_value = {
                "data": [{"embedding": [0.1] * dims} for _ in input_texts]
            }
            return mock_resp

        mock_openai.OpenAI.return_value.embeddings.create = mock_create
        mock_openai.AsyncOpenAI.return_value.embeddings.create = mock_create
        return mock_openai, call_records

    def test_calls_api_once(self) -> None:
        """embed_query 触发 1 次 API 调用。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, call_records = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()
                result = model.embed_query("什么是导数？")

        assert len(call_records) == 1
        assert len(result) == 1024

    def test_input_length_is_1(self) -> None:
        """embed_query 传入 API 的 input 长度为 1。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, call_records = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()
                model.embed_query("测试文本")

        assert call_records[0]["input_len"] == 1

    def test_dimensions_1024_in_api_call(self) -> None:
        """API 调用中 dimensions 参数为 1024。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, call_records = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()
                model.embed_query("测试文本")

        assert call_records[0]["dimensions"] == 1024

    def test_vector_length_is_1024(self) -> None:
        """返回向量长度 == 1024。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, _ = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()
                result = model.embed_query("任意文本")

        assert len(result) == 1024


# ═══════════════════════════════════════════════════════════════════════════════
# embed_documents() — 多文本分批嵌入
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmbedDocuments:
    """embed_documents 分批验证：每批 input ≤ DashScope 限制。"""

    def _reset_singleton(self) -> None:
        import src.api.embedding as emb_module
        emb_module._model = None

    def _make_mock_cfg(self) -> MagicMock:
        cfg = MagicMock()
        cfg.model = "qwen3.7-text-embedding"
        cfg.api_key = "sk-dashscope-test"
        cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        cfg.dimension = 1024
        cfg.timeout = 60.0
        return cfg

    def _mock_openai_client(self, dims: int = 1024):
        """Patch langchain_openai 底层的 openai 模块，使 API 调用走 mock。

        Returns:
            (mock_openai_module, create_call_records)
        """
        mock_openai = MagicMock()
        call_records: list[dict] = []

        def mock_create(**kwargs):
            input_texts = kwargs.get("input", [])
            call_records.append({
                "input_len": len(input_texts),
                "dimensions": kwargs.get("dimensions"),
            })
            mock_resp = MagicMock()
            mock_resp.model_dump.return_value = {
                "data": [{"embedding": [0.1] * dims} for _ in input_texts]
            }
            return mock_resp

        mock_openai.OpenAI.return_value.embeddings.create = mock_create
        mock_openai.AsyncOpenAI.return_value.embeddings.create = mock_create
        return mock_openai, call_records

    def test_25_short_texts_batches_at_most_20(self) -> None:
        """25 条短文本嵌入时，发往 API 的每批 input 长度 ≤ 20。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, call_records = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()

                # 25 条极短文本（模拟知识点短句，每个字符 ≈ 1 token）
                texts = [f"知识点{i}" for i in range(25)]
                result = model.embed_documents(texts)

        # 每批 input 长度不超过 DashScope 限制
        for record in call_records:
            assert record["input_len"] <= _DASHSCOPE_MAX_INPUTS_PER_REQUEST, (
                f"批次大小 {record['input_len']} 超过 DashScope 限制 "
                f"({_DASHSCOPE_MAX_INPUTS_PER_REQUEST})"
            )

        # 总结果数 = 输入文本数
        assert len(result) == 25

        # 总批次数：25 / 20 → 2 批
        assert len(call_records) == 2
        assert call_records[0]["input_len"] == 20
        assert call_records[1]["input_len"] == 5

    def test_each_batch_has_dimensions_1024(self) -> None:
        """每批 API 调用都显式传入 dimensions=1024。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, call_records = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()
                texts = [f"知识点{i}" for i in range(25)]
                model.embed_documents(texts)

        for record in call_records:
            assert record["dimensions"] == 1024

    def test_single_text_one_call(self) -> None:
        """单条文本只需 1 次 API 调用。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, call_records = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()
                result = model.embed_documents(["只有一个知识点"])

        assert len(call_records) == 1
        assert call_records[0]["input_len"] == 1
        assert len(result) == 1

    def test_exactly_20_texts_one_batch(self) -> None:
        """正好 20 条文本 → 1 批。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, call_records = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()
                texts = [f"知识点{i}" for i in range(20)]
                result = model.embed_documents(texts)

        assert len(call_records) == 1
        assert call_records[0]["input_len"] == 20
        assert len(result) == 20

    def test_21_texts_two_batches(self) -> None:
        """21 条文本 → 2 批（20 + 1）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, call_records = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()
                texts = [f"知识点{i}" for i in range(21)]
                result = model.embed_documents(texts)

        assert len(call_records) == 2
        assert call_records[0]["input_len"] == 20
        assert call_records[1]["input_len"] == 1
        assert len(result) == 21

    def test_empty_list_returns_empty(self) -> None:
        """空列表返回空结果（不调用 API）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, call_records = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()
                result = model.embed_documents([])

        assert result == []
        assert len(call_records) == 0

    def test_40_texts_two_batches_of_20(self) -> None:
        """40 条文本 → 2 批（各 20 条）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_openai, call_records = self._mock_openai_client()

        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with patch("langchain_openai.embeddings.base.openai", mock_openai):
                model = get_embedding_model()
                texts = [f"知识点{i}" for i in range(40)]
                result = model.embed_documents(texts)

        assert len(call_records) == 2
        assert call_records[0]["input_len"] == 20
        assert call_records[1]["input_len"] == 20
        assert len(result) == 40


# ═══════════════════════════════════════════════════════════════════════════════
# 异常路径
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetEmbeddingModelErrors:
    """异常路径：未解析 api_key / 不在支持列表。"""

    def _reset_singleton(self) -> None:
        import src.api.embedding as emb_module
        emb_module._model = None

    def test_unresolved_api_key_raises(self) -> None:
        """api_key 仍是 ${VAR} 占位符时抛出 RuntimeError。"""
        self._reset_singleton()
        mock_cfg = MagicMock()
        mock_cfg.api_key = "${DASHSCOPE_API_KEY}"
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with pytest.raises(RuntimeError, match="api_key 未解析"):
                get_embedding_model()

    def test_unsupported_model_raises(self) -> None:
        """模型不在白名单时抛出 RuntimeError。"""
        self._reset_singleton()
        mock_cfg = MagicMock()
        mock_cfg.model = "bge-m3"  # 不在白名单
        mock_cfg.api_key = "sk-test"
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with pytest.raises(RuntimeError, match="不在支持列表"):
                get_embedding_model()

    def test_error_message_contains_env_var_name(self) -> None:
        """RuntimeError 信息中包含未解析的环境变量名 DASHSCOPE_API_KEY。"""
        self._reset_singleton()
        mock_cfg = MagicMock()
        mock_cfg.api_key = "${DASHSCOPE_API_KEY}"
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
                get_embedding_model()

    def test_error_message_contains_model_name(self) -> None:
        """RuntimeError 信息中包含不支持的模型名。"""
        self._reset_singleton()
        mock_cfg = MagicMock()
        mock_cfg.model = "unknown-model"
        mock_cfg.api_key = "sk-test"
        with patch("src.api.embedding.config") as mock_config:
            mock_config.embedding = mock_cfg
            with pytest.raises(RuntimeError, match="unknown-model"):
                get_embedding_model()
