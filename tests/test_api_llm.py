# tests/test_api_llm.py
"""src/api/llm.py 单元测试。

覆盖：
- DeepSeekModel.supported_models()  白名单
- get_llm_model()                   懒初始化、单例、配置透传、timeout、http_client_provider
- get_llm_model()                   异常：未解析 api_key / 不在支持列表
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from trpc_agent_sdk.models import OpenAIModel, shared_http_client_provider_factory

from src.api.llm import DeepSeekModel, get_llm_model
from src.config import LLMConfig


# ═══════════════════════════════════════════════════════════════════════════════
# DeepSeekModel 白名单
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeepSeekModel:
    """模型白名单验证。"""

    def test_supported_models_returns_list(self) -> None:
        models = DeepSeekModel.supported_models()
        assert isinstance(models, list)
        assert len(models) == 2

    def test_default_model_in_whitelist(self) -> None:
        """默认模型 deepseek-v4-flash 必须在白名单内。"""
        assert LLMConfig().model in DeepSeekModel.supported_models()

    def test_is_openai_subclass(self) -> None:
        """DeepSeekModel 继承 OpenAIModel，不应该是抽象类。"""
        assert issubclass(DeepSeekModel, OpenAIModel)

    def test_can_instantiate_with_valid_model(self) -> None:
        """白名单内的模型名可以正常实例化。"""
        model = DeepSeekModel(
            model_name="deepseek-v4-flash",
            api_key="sk-test",
            base_url="https://api.deepseek.com",
        )
        assert model is not None


# ═══════════════════════════════════════════════════════════════════════════════
# get_llm_model() 单例与懒初始化
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetLLMModel:
    """懒初始化单例。"""

    def _reset_singleton(self) -> None:
        """重置模块级单例，确保测试隔离。"""
        import src.api.llm as llm_module
        llm_module._model = None

    def _make_mock_cfg(self) -> MagicMock:
        """构造一个模拟 LLMConfig。"""
        cfg = MagicMock()
        cfg.model = "deepseek-v4-flash"
        cfg.api_key = "sk-deepseek-test"
        cfg.base_url = "https://api.deepseek.com"
        cfg.timeout = 60.0
        cfg.temperature = 0.7
        cfg.max_tokens = 4096
        return cfg

    def test_first_call_creates_instance(self) -> None:
        """首次调用返回 DeepSeekModel 实例。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert isinstance(result, DeepSeekModel)

    def test_second_call_returns_same_instance(self) -> None:
        """第二次调用返回缓存，不重建。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                first = get_llm_model()
                second = get_llm_model()
        assert first is second

    def test_model_name_from_config(self) -> None:
        """创建的模型 model_name 来自 config.llm.model。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert result._model_name == "deepseek-v4-flash"

    def test_timeout_passed_to_client_args(self) -> None:
        """config.llm.timeout 通过 client_args 传入。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_cfg.timeout = 30.0
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert result.config["client_args"]["timeout"] == 30.0
        assert result.client_args["timeout"] == 30.0

    def test_temperature_passed_to_generate_content_config(self) -> None:
        """config.llm.temperature 通过 generate_content_config 传入。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_cfg.temperature = 0.3
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert result.generate_content_config.temperature == 0.3

    def test_max_tokens_passed_to_generate_content_config(self) -> None:
        """config.llm.max_tokens 通过 generate_content_config 传入。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        mock_cfg.max_tokens = 2048
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert result.generate_content_config.max_output_tokens == 2048

    def test_default_temperature_is_0_7(self) -> None:
        """默认 temperature 为 0.7（与 LLMConfig 一致）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert result.generate_content_config.temperature == 0.7

    def test_default_max_tokens_is_4096(self) -> None:
        """默认 max_tokens 为 4096（与 LLMConfig 一致）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert result.generate_content_config.max_output_tokens == 4096

    def test_default_timeout_is_60(self) -> None:
        """默认 timeout 为 60s（与 LLMConfig 一致）。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert result.config["client_args"]["timeout"] == 60.0

    def test_api_key_passed_correctly(self) -> None:
        """API Key 从 config 透传到模型。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert result.config["api_key"] == "sk-deepseek-test"

    def test_base_url_passed_correctly(self) -> None:
        """base_url 从 config 透传到模型。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert result.config["base_url"] == "https://api.deepseek.com"

    def test_http_client_provider_is_set(self) -> None:
        """shared_http_client_provider_factory 被传入，http_client_provider 非 None。"""
        self._reset_singleton()
        mock_cfg = self._make_mock_cfg()
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                result = get_llm_model()
        assert result._http_client_provider is not None


# ═══════════════════════════════════════════════════════════════════════════════
# get_llm_model() 异常路径
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetLLMModelErrors:
    """异常路径：未解析 api_key / 不在支持列表。"""

    def _reset_singleton(self) -> None:
        import src.api.llm as llm_module
        llm_module._model = None

    def test_unresolved_api_key_raises(self) -> None:
        """api_key 仍是 ${VAR} 占位符时抛出 RuntimeError。"""
        self._reset_singleton()
        mock_cfg = MagicMock()
        mock_cfg.model = "deepseek-v4-flash"
        mock_cfg.api_key = "${DEEPSEEK_API_KEY}"
        mock_cfg.base_url = "https://api.deepseek.com"
        mock_cfg.timeout = 60.0
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                with pytest.raises(RuntimeError, match="api_key 未解析"):
                    get_llm_model()

    def test_unsupported_model_raises(self) -> None:
        """模型不在白名单时抛出 RuntimeError。"""
        self._reset_singleton()
        mock_cfg = MagicMock()
        mock_cfg.model = "deepseek-chat"  # 不在白名单
        mock_cfg.api_key = "sk-test"
        mock_cfg.base_url = "https://api.deepseek.com"
        mock_cfg.timeout = 60.0
        with patch.object(DeepSeekModel, "supported_models", return_value=["deepseek-v4-flash", "deepseek-v4-pro"]):
            with patch("src.api.llm.config") as mock_config:
                mock_config.llm = mock_cfg
                with pytest.raises(RuntimeError, match="不在支持列表"):
                    get_llm_model()
