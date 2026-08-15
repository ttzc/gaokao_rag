# src/api/vlm.py
# 视觉语言模型客户端层：封装 Qwen DashScope API（OpenAI 兼容协议）。
#
# 设计：
#   - QwenVLMModel     — OpenAIModel 子类，声明经过测试可用的模型白名单
#   - _model           — flash 模型单例（默认）
#   - _think_model     — plus 模型单例（推理增强）
#   - get_vlm_model()  — 返回 flash 单例
#   - get_vlm_think_model() — 返回 plus 单例
#
# 配置来源：config.vlm（model / model_think / api_key / base_url / timeout），
#           由 config.toml + .env 提供。敏感信息走环境变量，不硬编码。

from __future__ import annotations

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel, OpenAIModel, shared_http_client_provider_factory
from trpc_agent_sdk.types import GenerateContentConfig

from src.config import config

# ═══════════════════════════════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════════════════════════════

_model: LLMModel | None = None          # flash（默认）
_think_model: LLMModel | None = None    # plus（推理增强）


# ═══════════════════════════════════════════════════════════════════════════════
# 模型适配类
# ═══════════════════════════════════════════════════════════════════════════════


class QwenVLMModel(OpenAIModel):
    """Qwen 视觉语言模型（DashScope OpenAI 兼容端点）的模型适配类。

    OpenAIModel 继承了 LLMModel 的抽象成员 ``supported_models``，
    必须在此实现，否则 QwenVLMModel 仍是抽象类无法实例化。

    注意：此列表是**经过测试可用的模型白名单**，不是从 config.toml 动态读取。
    新增模型需要先在代码里加入列表并验证通过，再开放给用户配置。
    """

    @classmethod
    def supported_models(cls) -> list[str]:
        return [
            "qwen3.7-flash",   # 主力：常见几何图、函数图像、坐标系
            "qwen3.7-plus",    # 推理增强：立体几何、组合图、复杂图形
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# 核心 API
# ═══════════════════════════════════════════════════════════════════════════════


def get_vlm_model() -> LLMModel:
    """返回 flash 模型单例（默认主力模型），懒初始化。

    从 ``config.vlm.model`` 读取模型名称 / API Key / 基地址，
    创建 QwenVLMModel 实例并缓存。后续调用直接返回缓存。

    Raises:
        RuntimeError: 若 ``config.vlm.api_key`` 仍是未解析的 ``${VAR}`` 占位符，
            说明 .env 中缺少 ``DASHSCOPE_API_KEY``。
        RuntimeError: 若 ``config.vlm.model`` 不在支持列表内。
    """
    global _model
    if _model:
        return _model
    cfg = config.vlm
    if cfg.api_key.startswith("${"):
        raise RuntimeError(
            f"VLM api_key 未解析（仍是 '{cfg.api_key}'），"
            "请检查 .env 中是否设置了 DASHSCOPE_API_KEY"
        )
    if cfg.model not in QwenVLMModel.supported_models():
        raise RuntimeError(
            f"VLM model '{cfg.model}' 不在支持列表 "
            f"{QwenVLMModel.supported_models()} 中，"
            "请检查 config.toml 中 vlm.model 配置"
        )
    _model = QwenVLMModel(
        model_name=cfg.model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        http_client_provider_factory=shared_http_client_provider_factory,
        client_args={"timeout": cfg.timeout},
        generate_content_config=GenerateContentConfig(
            temperature=cfg.temperature,
            max_output_tokens=cfg.max_tokens,
        ),
    )
    logger.info(f"Init vlm model (flash): {cfg.model} (temp={cfg.temperature}, max_tokens={cfg.max_tokens}, timeout={cfg.timeout}s)")
    return _model


def get_vlm_think_model() -> LLMModel:
    """返回 plus 模型单例（推理增强模型），懒初始化。

    从 ``config.vlm.model_think`` 读取模型名称 / API Key / 基地址，
    创建 QwenVLMModel 实例并缓存。后续调用直接返回缓存。

    Raises:
        RuntimeError: 若 ``config.vlm.api_key`` 仍是未解析的 ``${VAR}`` 占位符，
            说明 .env 中缺少 ``DASHSCOPE_API_KEY``。
        RuntimeError: 若 ``config.vlm.model_think`` 不在支持列表内。
    """
    global _think_model
    if _think_model:
        return _think_model
    cfg = config.vlm
    if cfg.api_key.startswith("${"):
        raise RuntimeError(
            f"VLM api_key 未解析（仍是 '{cfg.api_key}'），"
            "请检查 .env 中是否设置了 DASHSCOPE_API_KEY"
        )
    if cfg.model_think not in QwenVLMModel.supported_models():
        raise RuntimeError(
            f"VLM model_think '{cfg.model_think}' 不在支持列表 "
            f"{QwenVLMModel.supported_models()} 中，"
            "请检查 config.toml 中 vlm.model_think 配置"
        )
    _think_model = QwenVLMModel(
        model_name=cfg.model_think,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        http_client_provider_factory=shared_http_client_provider_factory,
        client_args={"timeout": cfg.timeout},
        generate_content_config=GenerateContentConfig(
            temperature=cfg.temperature,
            max_output_tokens=cfg.max_tokens,
        ),
    )
    logger.info(f"Init vlm model (plus): {cfg.model_think} (temp={cfg.temperature}, max_tokens={cfg.max_tokens}, timeout={cfg.timeout}s)")
    return _think_model
