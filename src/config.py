# src/config.py
# 配置管理模块：加载 config.toml，解析 ${VAR} 占位符，Pydantic 校验，暴露全局单例。
#
# 加载顺序：
#   1. load_dotenv()        — 把 .env 读进 os.environ
#   2. _expand()            — 把 config.toml 里的 ${VAR} 替换成环境变量值
#   3. _load()              — 解析 TOML → Pydantic 校验 → AppConfig
#   4. _sync_framework_env() — 把业务配置同步到 TRPC_AGENT_ 前缀，供框架读取
#
# 用法：
#     from src.config import config
#     config.llm.model           # → "deepseek-chat"
#     config.vlm.base_url        # → "https://dashscope.aliyuncs.com/compatible-mode/v1"

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════════════════════
# Part 1: 加载 .env（必须在任何配置解析之前执行）
# ═══════════════════════════════════════════════════════════════════════════════

load_dotenv()


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2: 环境变量替换（${VAR} → 实际值）
# ═══════════════════════════════════════════════════════════════════════════════


def _expand(value: str) -> str:
    """将字符串中的 ``${VAR}`` 占位符替换为环境变量值。

    未设置的变量保留原占位符不变，让 Pydantic 校验时再报错。

    Args:
        value: 可能包含 ``${VAR}`` 占位符的字符串。

    Returns:
        替换后的字符串。
    """
    def _replacer(m: re.Match[str]) -> str:
        var = m.group(1)
        return os.getenv(var, m.group(0))

    return re.sub(r'\$\{(\w+)\}', _replacer, value)


def _expand_dict(d: dict[str, Any]) -> dict[str, Any]:
    """递归替换字典中所有 ``${VAR}`` 占位符。

    Args:
        d: 可能包含 ``${VAR}`` 的嵌套字典。

    Returns:
        替换后的字典（原地修改）。
    """
    for k, v in d.items():
        if isinstance(v, str):
            d[k] = _expand(v)
        elif isinstance(v, dict):
            _expand_dict(v)
    return d


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3: Pydantic 配置模型
# ═══════════════════════════════════════════════════════════════════════════════


class LLMConfig(BaseModel):
    """对话模型（OpenAI 兼容端点）配置。"""

    model: str = Field(
        default="deepseek-v4-flash",
        description="模型名称（OpenAI 兼容格式）",
    )
    base_url: str = Field(
        default="https://api.deepseek.com",
        description="API 基地址（不带 /v1，OpenAI 客户端自动拼接）",
    )
    api_key: str = Field(
        default="${DEEPSEEK_API_KEY}",
        description="API Key，通过环境变量引用",
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, gt=0)
    timeout: float = Field(default=60.0, gt=0)


class VLMConfig(BaseModel):
    """视觉语言模型（OpenAI 兼容端点）配置。

    支持双模型策略：默认 8B 处理普通图形，检测到复杂场景时自动升级到 32B-Thinking。
    """

    model: str = Field(
        default="qwen3-vl-8b-instruct",
        description="主力 VLM 模型（DashScope model ID）",
    )
    model_think: str = Field(
        default="qwen3-vl-32b-instruct",
        description="复杂图形推理增强模型（DashScope model ID）",
    )
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="DashScope OpenAI 兼容端点",
    )
    api_key: str = Field(
        default="${DASHSCOPE_API_KEY}",
        description="DashScope API Key，通过环境变量引用",
    )
    timeout: float = Field(default=120.0, gt=0, description="VLM 调用超时（秒），图形理解较慢")

    # 自动升级阈值
    image_size_threshold: int = Field(
        default=500_000,
        gt=0,
        description="图像文件大小阈值（字节），超过则升级到 thinking 模型",
    )
    complexity_keywords: list[str] = Field(
        default_factory=lambda: [
            "立体几何", "三棱锥", "四棱锥", "旋转体",
            "组合", "叠加", "复合",
            "恒成立", "存在性",
        ],
        description="触发模型升级的题目文本关键词列表",
    )


class EmbeddingConfig(BaseModel):
    """嵌入模型（OpenAI 兼容端点）配置。"""

    model: str = Field(
        default="qwen3-embedding-4b",
        description="嵌入模型名称（DashScope model ID）",
    )
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="DashScope OpenAI 兼容端点",
    )
    api_key: str = Field(
        default="${DASHSCOPE_API_KEY}",
        description="DashScope API Key，通过环境变量引用",
    )
    timeout: float = Field(default=60.0, gt=0)


class MinerUConfig(BaseModel):
    """MinerU2.5-Pro PDF 解析服务配置。

    作为 PyMuPDF 的兜底方案，处理复杂版面的试卷 PDF。
    """

    base_url: str = Field(
        default="https://api.mineru.ai/v1",
        description="MinerU API 基地址",
    )
    api_key: str = Field(
        default="${MINERU_API_KEY}",
        description="MinerU API Key，通过环境变量引用",
    )
    timeout: float = Field(default=300.0, gt=0, description="PDF 解析超时（秒），大文件较慢")
    max_pages: int = Field(default=50, gt=0, description="单次调用最大页数限制")


class StoreConfig(BaseModel):
    """三层存储路径配置。"""

    raw_dir: str = Field(
        default="data/raw",
        description="原始文件目录（PDF、图片等）",
    )
    processed_dir: str = Field(
        default="data/processed",
        description="处理后结构化数据目录",
    )
    chroma_dir: str = Field(
        default="data/chroma_db",
        description="Chroma 向量数据库持久化目录",
    )
    sqlite_path: str = Field(
        default="data/gaokao.db",
        description="SQLite 数据库文件路径",
    )


class QQConfig(BaseModel):
    """trpc-claw QQ 通道接入配置。"""

    app_id: str = Field(
        default="${QQ_APP_ID}",
        description="QQ 机器人 AppID，通过环境变量引用",
    )
    app_secret: str = Field(
        default="${QQ_APP_SECRET}",
        description="QQ 机器人 AppSecret，通过环境变量引用",
    )
    # 后续扩展：sandbox 模式、回调地址等


class AppConfig(BaseModel):
    """顶层应用配置，聚合所有子配置。"""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    vlm: VLMConfig = Field(default_factory=VLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    mineru: MinerUConfig = Field(default_factory=MinerUConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
    qq: QQConfig = Field(default_factory=QQConfig)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 4: 加载并校验 config.toml
# ═══════════════════════════════════════════════════════════════════════════════


def _load(config_path: Path = Path("config.toml")) -> AppConfig:
    """加载并校验 ``config.toml``，文件不存在时使用全量默认值。

    Args:
        config_path: 配置文件路径，默认当前目录下的 ``config.toml``。
                     测试等场景可注入临时文件路径。
    """
    if not config_path.exists():
        import warnings
        warnings.warn("config.toml 未找到，使用内置默认值", stacklevel=2)
        return AppConfig()

    try:
        raw: dict[str, Any] = tomllib.load(config_path.open("rb"))
        raw = _expand_dict(raw)
        return AppConfig(**raw)
    except Exception as exc:
        raise RuntimeError(f"config.toml 解析失败: {exc}") from exc


# 全局配置单例，模块导入时即初始化
config: AppConfig = _load()


# ═══════════════════════════════════════════════════════════════════════════════
# Part 5: 框架环境变量桥接
# ═══════════════════════════════════════════════════════════════════════════════
#
# tRPC-Agent 框架通过 TRPC_AGENT_ 前缀的环境变量读取模型配置。
# 本模块的业务配置使用模块级命名（DEEPSEEK_ / DASHSCOPE_ / MINERU_），
# 因此需要在模块加载时同步到框架约定的环境变量前缀。
#
# 同步策略：优先使用 config.toml 里的值（用户显式配置），
#           缺失时回退到框架默认值（框架内部有默认兜底）。
# ═══════════════════════════════════════════════════════════════════════════════


def _sync_framework_env() -> None:
    """将业务配置同步到 ``TRPC_AGENT_`` 环境变量前缀。

    tRPC-Agent 框架在初始化时从 ``os.environ`` 读取这些变量，
    因此必须在任何 ``trpc_agent_sdk`` 导入之前调用本函数。
    """
    # 主模型：用 LLM 的配置覆盖框架默认值
    # 框架默认读 TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
    if config.llm.api_key and not config.llm.api_key.startswith("${"):
        os.environ["TRPC_AGENT_API_KEY"] = config.llm.api_key
    if config.llm.base_url:
        os.environ["TRPC_AGENT_BASE_URL"] = config.llm.base_url
    if config.llm.model:
        os.environ["TRPC_AGENT_MODEL_NAME"] = config.llm.model


_sync_framework_env()
