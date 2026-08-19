# tests/test_config.py
"""src/config.py 单元测试。

覆盖：
- _expand / _expand_dict  占位符替换
- LLMConfig / VLMConfig / EmbeddingConfig / MinerUConfig / StoreConfig / QQConfig  默认值与校验
- AppConfig 从 TOML 加载
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

# ── src.config 统一模块级导入（editable install 后 pytest 收集期可正常 import） ─
from src.config import (
    AppConfig,
    EmbeddingConfig,
    LLMConfig,
    MinerUConfig,
    QQConfig,
    StoreConfig,
    VLMConfig,
    _expand,
    _expand_dict,
    _load,
    config,
)

# ═══════════════════════════════════════════════════════════════════════════════
# _expand 系列
# ═══════════════════════════════════════════════════════════════════════════════


class TestExpand:
    """${VAR} 占位符替换。"""

    def test_expand_existing_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOO", "bar")
        assert _expand("${FOO}") == "bar"

    def test_expand_missing_env_keeps_placeholder(self) -> None:
        assert _expand("${NOT_SET_VAR}") == "${NOT_SET_VAR}"

    def test_expand_nested_in_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOST", "localhost")
        assert _expand("${HOST}:5432") == "localhost:5432"

    def test_expand_no_placeholder(self) -> None:
        assert _expand("plain text") == "plain text"

    def test_expand_multiple_placeholders(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert _expand("${A}/${B}") == "1/2"

    def test_expand_dict_nested_placeholder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_expand_dict 递归替换嵌套 dict 中的深层占位符。"""
        monkeypatch.setenv("NESTED_VAR", "replaced")
        raw = {"a": {"b": "${NESTED_VAR}"}, "c": "top-level"}
        result = _expand_dict(raw)
        assert result == {"a": {"b": "replaced"}, "c": "top-level"}


# ═══════════════════════════════════════════════════════════════════════════════
# LLMConfig
# ═══════════════════════════════════════════════════════════════════════════════


class TestLLMConfig:
    def test_default_model(self) -> None:
        assert LLMConfig().model == "deepseek-v4-flash"

    def test_default_base_url(self) -> None:
        assert LLMConfig().base_url == "https://api.deepseek.com"

    def test_default_temperature(self) -> None:
        assert LLMConfig().temperature == 0.7

    def test_temperature_validation(self) -> None:
        with pytest.raises(ValidationError):
            LLMConfig(temperature=3.0)

    def test_timeout_validation(self) -> None:
        with pytest.raises(ValidationError):
            LLMConfig(timeout=0)


# ═══════════════════════════════════════════════════════════════════════════════
# VLMConfig
# ═══════════════════════════════════════════════════════════════════════════════


class TestVLMConfig:
    def test_default_model(self) -> None:
        assert VLMConfig().model == "qwen3.7-flash"

    def test_default_model_think(self) -> None:
        assert VLMConfig().model_think == "qwen3.7-plus"

    def test_default_base_url(self) -> None:
        assert (
            VLMConfig().base_url
            == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

    def test_default_image_size_threshold(self) -> None:
        assert VLMConfig().image_size_threshold == 500_000

    def test_default_complexity_keywords(self) -> None:
        kw = VLMConfig().complexity_keywords
        assert "立体几何" in kw
        assert "恒成立" in kw

    def test_default_temperature(self) -> None:
        """VLM 描述任务默认 temperature=0.1（低随机性，减少幻觉）。"""
        assert VLMConfig().temperature == 0.1

    def test_default_max_tokens(self) -> None:
        """VLM 描述任务默认 max_tokens=1024。"""
        assert VLMConfig().max_tokens == 1024

    def test_temperature_validation(self) -> None:
        with pytest.raises(ValidationError):
            VLMConfig(temperature=-0.1)

    def test_max_tokens_validation(self) -> None:
        with pytest.raises(ValidationError):
            VLMConfig(max_tokens=0)

    def test_custom_keywords(self) -> None:
        custom = VLMConfig(complexity_keywords=["自定义关键词"])
        assert custom.complexity_keywords == ["自定义关键词"]

    def test_image_size_threshold_validation(self) -> None:
        with pytest.raises(ValidationError):
            VLMConfig(image_size_threshold=0)


# ═══════════════════════════════════════════════════════════════════════════════
# EmbeddingConfig
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmbeddingConfig:
    def test_default_model(self) -> None:
        assert EmbeddingConfig().model == "qwen3.7-text-embedding"

    def test_default_dimension(self) -> None:
        assert EmbeddingConfig().dimension == 1024

    def test_default_base_url(self) -> None:
        assert (
            EmbeddingConfig().base_url
            == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MinerUConfig
# ═══════════════════════════════════════════════════════════════════════════════


class TestMinerUConfig:
    def test_default_base_url(self) -> None:
        assert MinerUConfig().base_url == "https://api.mineru.ai/v1"

    def test_default_max_pages(self) -> None:
        assert MinerUConfig().max_pages == 50

    def test_max_pages_validation(self) -> None:
        with pytest.raises(ValidationError):
            MinerUConfig(max_pages=0)


# ═══════════════════════════════════════════════════════════════════════════════
# StoreConfig
# ═══════════════════════════════════════════════════════════════════════════════


class TestStoreConfig:
    def test_default_data_dir(self) -> None:
        s = StoreConfig()
        assert s.data_dir == "data"

    def test_derived_paths_from_default(self) -> None:
        s = StoreConfig()
        assert s.raw_dir == "data/files/raw"
        assert s.processed_dir == "data/files/processed"
        assert s.chroma_dir == "data/chroma_db"
        assert s.sqlite_path == "data/gaokao.db"

    def test_custom_data_dir(self) -> None:
        s = StoreConfig(data_dir="/tmp/custom_data")
        assert s.data_dir == "/tmp/custom_data"
        assert s.raw_dir == "/tmp/custom_data/files/raw"
        assert s.sqlite_path == "/tmp/custom_data/gaokao.db"

    def test_absolute_data_dir_derived_paths(self) -> None:
        """绝对路径 data_dir 的派生路径应保留绝对前缀。"""
        s = StoreConfig(data_dir="/mnt/ssd/gaokao_data")
        assert s.raw_dir == "/mnt/ssd/gaokao_data/files/raw"
        assert s.processed_dir == "/mnt/ssd/gaokao_data/files/processed"
        assert s.chroma_dir == "/mnt/ssd/gaokao_data/chroma_db"
        assert s.sqlite_path == "/mnt/ssd/gaokao_data/gaokao.db"


# ═══════════════════════════════════════════════════════════════════════════════
# QQConfig
# ═══════════════════════════════════════════════════════════════════════════════


class TestQQConfig:
    def test_default_app_id_is_placeholder(self) -> None:
        assert QQConfig().app_id == "${QQ_APP_ID}"

    def test_default_app_secret_is_placeholder(self) -> None:
        assert QQConfig().app_secret == "${QQ_APP_SECRET}"


# ═══════════════════════════════════════════════════════════════════════════════
# AppConfig 聚合 + TOML 加载
# ═══════════════════════════════════════════════════════════════════════════════


class TestAppConfig:
    def test_default_has_all_sections(self) -> None:
        app = AppConfig()
        assert app.llm is not None
        assert app.vlm is not None
        assert app.embedding is not None
        assert app.mineru is not None
        assert app.store is not None
        assert app.qq is not None

    def test_load_from_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """模拟 config.toml 加载，验证 ${VAR} 替换 + Pydantic 校验。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-test")
        monkeypatch.setenv("MINERU_API_KEY", "sk-mineru-test")
        monkeypatch.setenv("QQ_APP_ID", "test-app-id")
        monkeypatch.setenv("QQ_APP_SECRET", "test-app-secret")

        toml_content = (
            '[llm]\n'
            'model = "deepseek-v4-flash"\n'
            'base_url = "https://api.deepseek.com"\n'
            'api_key = "${DEEPSEEK_API_KEY}"\n'
            'temperature = 0.5\n'
            '\n'
            '[vlm]\n'
            'model = "qwen3.7-flash"\n'
            'temperature = 0.2\n'
            'max_tokens = 512\n'
            'timeout = 90.0\n'
            '\n'
            '[store]\n'
            'data_dir = "data/test"\n'
        )
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(toml_content, encoding="utf-8")

        # 直接传临时路径，无需 os.chdir
        app = _load(cfg_file)

        assert app.llm.model == "deepseek-v4-flash"
        assert app.llm.base_url == "https://api.deepseek.com"
        assert app.llm.api_key == "sk-deepseek-test"
        assert app.llm.temperature == 0.5
        assert app.vlm.model == "qwen3.7-flash"
        assert app.vlm.temperature == 0.2
        assert app.vlm.max_tokens == 512
        assert app.vlm.timeout == 90.0
        assert app.store.data_dir == "data/test"
        assert app.store.sqlite_path == "data/test/gaokao.db"

    def test_missing_config_toml_returns_defaults(
        self, tmp_path: Path
    ) -> None:
        """config.toml 不存在时返回全量默认值。"""
        empty_path = tmp_path / "nonexistent.toml"
        app = _load(empty_path)

        assert app.llm.model == "deepseek-v4-flash"
        assert app.vlm.model == "qwen3.7-flash"
        assert app.mineru.max_pages == 50

    def test_malformed_toml_raises_runtime_error(
        self, tmp_path: Path
    ) -> None:
        """损坏的 TOML（未闭合 section）应抛出 RuntimeError。"""
        bad_file = tmp_path / "bad.toml"
        bad_file.write_text("[llm\nmodel = \"broken\"\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="config.toml 解析失败"):
            _load(bad_file)
