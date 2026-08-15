# tests/test_api_vlm.py
"""src/api/vlm.py 单元测试。

覆盖：
- QwenVLMModel.supported_models()   白名单
- get_vlm_model()                   flash 单例、懒初始化、配置透传、timeout
- get_vlm_think_model()             plus 单例、model_think 配置
- 异常路径：未解析 api_key / 不在支持列表
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from trpc_agent_sdk.models import OpenAIModel, shared_http_client_provider_factory

from src.api.vlm import QwenVLMModel, get_vlm_model, get_vlm_think_model
from src.config import VLMConfig


# ═══════════════════════════════════════════════════════════════════════════════
# QwenVLMModel 白名单
# ═══════════════════════════════════════════════════════════════════════════════


class TestQwenVLMModel:
    """模型白名单验证。"""

    def test_supported_models_returns_list(self) -> None:
        models = QwenVLMModel.supported_models()
        assert isinstance(models, list)
        assert len(models) == 2

    def test_flash_in_whitelist(self) -> None:
        assert "qwen3.7-flash" in QwenVLMModel.supported_models()

    def test_plus_in_whitelist(self) -> None:
        assert "qwen3.7-plus" in QwenVLMModel.supported_models()

    def test_is_openai_subclass(self) -> None:
        assert issubclass(QwenVLMModel, OpenAIModel)

    def test_can_instantiate_with_valid_model(self) -> None:
        model = QwenVLMModel(
            model_name="qwen3.7-flash",
            api_key="sk-test",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        assert model is not None


# ═══════════════════════════════════════════════════════════════════════════════
# get_vlm_model() — flash 单例
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetVLMModel:
    """flash 模型单例。"""

    def _reset_singletons(self) -> None:
        import src.api.vlm as vlm_module
        vlm_module._model = None
        vlm_module._think_model = None

    def _make_mock_cfg(self) -> MagicMock:
        cfg = MagicMock()
        cfg.model = "qwen3.7-flash"
        cfg.model_think = "qwen3.7-plus"
        cfg.api_key = "sk-dashscope-test"
        cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        cfg.timeout = 120.0
        cfg.temperature = 0.1
        cfg.max_tokens = 1024
        return cfg

    def test_first_call_creates_instance(self) -> None:
        """首次调用返回 QwenVLMModel 实例。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert isinstance(result, QwenVLMModel)

    def test_second_call_returns_same_instance(self) -> None:
        """第二次调用返回缓存，不重建。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                first = get_vlm_model()
                second = get_vlm_model()
        assert first is second

    def test_model_name_is_flash(self) -> None:
        """flash 单例的 model_name 是 config.vlm.model（qwen3.7-flash）。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert result._model_name == "qwen3.7-flash"

    def test_timeout_passed_to_client_args(self) -> None:
        """config.vlm.timeout 通过 client_args 传入。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        mock_cfg.timeout = 90.0
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert result.config["client_args"]["timeout"] == 90.0
        assert result.client_args["timeout"] == 90.0

    def test_default_timeout_is_120(self) -> None:
        """默认 timeout 为 120s（与 VLMConfig 一致）。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert result.config["client_args"]["timeout"] == 120.0

    def test_api_key_passed_correctly(self) -> None:
        """API Key 从 config 透传到模型。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert result.config["api_key"] == "sk-dashscope-test"

    def test_base_url_passed_correctly(self) -> None:
        """base_url 从 config 透传到模型。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert result.config["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_http_client_provider_is_set(self) -> None:
        """shared_http_client_provider_factory 被传入，provider 非 None。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert result._http_client_provider is not None

    def test_temperature_passed_to_generate_content_config(self) -> None:
        """config.vlm.temperature 通过 generate_content_config 传入。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        mock_cfg.temperature = 0.05
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert result.generate_content_config.temperature == 0.05

    def test_max_tokens_passed_to_generate_content_config(self) -> None:
        """config.vlm.max_tokens 通过 generate_content_config 传入。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        mock_cfg.max_tokens = 512
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert result.generate_content_config.max_output_tokens == 512

    def test_default_temperature_is_0_1(self) -> None:
        """VLM 默认 temperature 为 0.1（描述任务宜低，减少幻觉）。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert result.generate_content_config.temperature == 0.1

    def test_default_max_tokens_is_1024(self) -> None:
        """VLM 默认 max_tokens 为 1024。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_model()
        assert result.generate_content_config.max_output_tokens == 1024


# ═══════════════════════════════════════════════════════════════════════════════
# get_vlm_think_model() — plus 单例
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetVLMThinkModel:
    """plus 模型单例（推理增强）。"""

    def _reset_singletons(self) -> None:
        import src.api.vlm as vlm_module
        vlm_module._model = None
        vlm_module._think_model = None

    def _make_mock_cfg(self) -> MagicMock:
        cfg = MagicMock()
        cfg.model = "qwen3.7-flash"
        cfg.model_think = "qwen3.7-plus"
        cfg.api_key = "sk-dashscope-test"
        cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        cfg.timeout = 120.0
        cfg.temperature = 0.1
        cfg.max_tokens = 1024
        return cfg

    def test_first_call_creates_instance(self) -> None:
        """首次调用返回 QwenVLMModel 实例。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_think_model()
        assert isinstance(result, QwenVLMModel)

    def test_second_call_returns_same_instance(self) -> None:
        """第二次调用返回缓存，不重建。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                first = get_vlm_think_model()
                second = get_vlm_think_model()
        assert first is second

    def test_model_name_is_plus(self) -> None:
        """plus 单例的 model_name 是 config.vlm.model_think（qwen3.7-plus）。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_think_model()
        assert result._model_name == "qwen3.7-plus"

    def test_timeout_passed_to_client_args(self) -> None:
        """config.vlm.timeout 通过 client_args 传入。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        mock_cfg.timeout = 180.0
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_think_model()
        assert result.config["client_args"]["timeout"] == 180.0

    def test_api_key_passed_correctly(self) -> None:
        """API Key 从 config 透传到模型。"""
        self._reset_singletons()
        mock_cfg = self._make_mock_cfg()
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                result = get_vlm_think_model()
        assert result.config["api_key"] == "sk-dashscope-test"


# ═══════════════════════════════════════════════════════════════════════════════
# 两个单例互相独立
# ═══════════════════════════════════════════════════════════════════════════════


class TestDualSingletons:
    """flash 和 plus 是两个独立单例，model_name 不同。"""

    def _reset_singletons(self) -> None:
        import src.api.vlm as vlm_module
        vlm_module._model = None
        vlm_module._think_model = None

    def test_flash_and_plus_are_different_instances(self) -> None:
        """get_vlm_model 和 get_vlm_think_model 返回不同实例。"""
        self._reset_singletons()
        mock_cfg = MagicMock()
        mock_cfg.model = "qwen3.7-flash"
        mock_cfg.model_think = "qwen3.7-plus"
        mock_cfg.api_key = "sk-test"
        mock_cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        mock_cfg.timeout = 120.0
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                flash = get_vlm_model()
                plus = get_vlm_think_model()
        assert flash is not plus
        assert flash._model_name == "qwen3.7-flash"
        assert plus._model_name == "qwen3.7-plus"


# ═══════════════════════════════════════════════════════════════════════════════
# 异常路径
# ═══════════════════════════════════════════════════════════════════════════════


class TestVLMErrors:
    """异常路径：未解析 api_key / 不在支持列表。"""

    def _reset_singletons(self) -> None:
        import src.api.vlm as vlm_module
        vlm_module._model = None
        vlm_module._think_model = None

    def test_unresolved_api_key_raises_flash(self) -> None:
        """flash: api_key 仍是 ${VAR} 占位符时抛出 RuntimeError。"""
        self._reset_singletons()
        mock_cfg = MagicMock()
        mock_cfg.model = "qwen3.7-flash"
        mock_cfg.model_think = "qwen3.7-plus"
        mock_cfg.api_key = "${DASHSCOPE_API_KEY}"
        mock_cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        mock_cfg.timeout = 120.0
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                with pytest.raises(RuntimeError, match="api_key 未解析"):
                    get_vlm_model()

    def test_unresolved_api_key_raises_think(self) -> None:
        """plus: api_key 仍是 ${VAR} 占位符时抛出 RuntimeError。"""
        self._reset_singletons()
        mock_cfg = MagicMock()
        mock_cfg.model = "qwen3.7-flash"
        mock_cfg.model_think = "qwen3.7-plus"
        mock_cfg.api_key = "${DASHSCOPE_API_KEY}"
        mock_cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        mock_cfg.timeout = 120.0
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                with pytest.raises(RuntimeError, match="api_key 未解析"):
                    get_vlm_think_model()

    def test_unsupported_model_raises_flash(self) -> None:
        """flash: 模型不在白名单时抛出 RuntimeError。"""
        self._reset_singletons()
        mock_cfg = MagicMock()
        mock_cfg.model = "qwen3-vl-8b"  # 不在白名单
        mock_cfg.model_think = "qwen3.7-plus"
        mock_cfg.api_key = "sk-test"
        mock_cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        mock_cfg.timeout = 120.0
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                with pytest.raises(RuntimeError, match="不在支持列表"):
                    get_vlm_model()

    def test_unsupported_model_raises_think(self) -> None:
        """plus: model_think 不在白名单时抛出 RuntimeError。"""
        self._reset_singletons()
        mock_cfg = MagicMock()
        mock_cfg.model = "qwen3.7-flash"
        mock_cfg.model_think = "qwen3-vl-32b"  # 不在白名单
        mock_cfg.api_key = "sk-test"
        mock_cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        mock_cfg.timeout = 120.0
        with patch.object(QwenVLMModel, "supported_models", return_value=["qwen3.7-flash", "qwen3.7-plus"]):
            with patch("src.api.vlm.config") as mock_config:
                mock_config.vlm = mock_cfg
                with pytest.raises(RuntimeError, match="不在支持列表"):
                    get_vlm_think_model()
