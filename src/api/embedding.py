# src/api/embedding.py
# 嵌入模型客户端层：封装 DashScope 嵌入 API（OpenAI 兼容协议）。
#
# 设计：
#   - _model               — 模块级懒单例（OpenAIEmbeddings 实例）
#   - get_embedding_model() — 懒初始化单例，全项目共用一个嵌入模型实例
#
# 关键约束：
#   - chunk_size=20 + check_embedding_ctx_length=False 组合下，
#     langchain OpenAIEmbeddings 以 chunk_size 作为每批条数；
#     但仍显式在 embed_documents 调用时传入 chunk_size=20，双重保障。
#   - dimensions 必须显式传入（AlgoNotes 踩坑：同模型跨平台默认维度不同）。
#   - 不自写 Embeddings 子类，直接复用 langchain_openai.OpenAIEmbeddings。
#
# 配置来源：config.embedding（model / api_key / base_url / timeout / dimension），
#           由 config.toml + .env 提供。敏感信息走环境变量，不硬编码。

from __future__ import annotations

from langchain_openai import OpenAIEmbeddings

from trpc_agent_sdk.log import logger

from src.config import config

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

# DashScope qwen3.7-text-embedding 单次请求最大条数
_DASHSCOPE_MAX_INPUTS_PER_REQUEST: int = 20

# 经过测试可用的嵌入模型白名单
_SUPPORTED_MODELS: tuple[str, ...] = ("qwen3.7-text-embedding",)

# ═══════════════════════════════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════════════════════════════

_model: OpenAIEmbeddings | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# 核心 API
# ═══════════════════════════════════════════════════════════════════════════════


def get_embedding_model() -> OpenAIEmbeddings:
    """返回嵌入模型单例实例，懒初始化。

    首次调用时从 ``config.embedding`` 读取模型名称 / API Key / 基地址 / 维度，
    创建 ``OpenAIEmbeddings`` 实例并缓存。后续调用直接返回缓存。

    构造参数说明：
        - ``chunk_size=20`` + ``check_embedding_ctx_length=False`` 组合下，
          langchain 以 chunk_size 作为每批条数，确保不超 DashScope 限制。
        - ``tiktoken_enabled=False``：DashScope 不支持 tiktoken 分词。
        - ``dimensions=cfg.dimension``：显式 1024，防维度漂移。

    Returns:
        OpenAIEmbeddings 单例实例。

    Raises:
        RuntimeError: 若 ``config.embedding.api_key`` 仍是未解析的 ``${VAR}`` 占位符，
            说明 .env 中缺少 ``DASHSCOPE_API_KEY``。
        RuntimeError: 若 ``config.embedding.model`` 不在支持列表内。
    """
    global _model
    if _model is not None:
        return _model

    cfg = config.embedding

    # ── 启动期校验 ──────────────────────────────────────────────────────────
    if cfg.api_key.startswith("${"):
        raise RuntimeError(
            f"Embedding api_key 未解析（仍是 '{cfg.api_key}'），"
            "请检查 .env 中是否设置了 DASHSCOPE_API_KEY"
        )
    if cfg.model not in _SUPPORTED_MODELS:
        raise RuntimeError(
            f"Embedding model '{cfg.model}' 不在支持列表 {_SUPPORTED_MODELS} 中，"
            "请检查 config.toml 中 embedding.model 配置"
        )

    # ── 构造 OpenAIEmbeddings ───────────────────────────────────────────────
    _model = OpenAIEmbeddings(
        model=cfg.model,
        openai_api_key=cfg.api_key,
        base_url=cfg.base_url,
        dimensions=cfg.dimension,
        chunk_size=_DASHSCOPE_MAX_INPUTS_PER_REQUEST,
        tiktoken_enabled=False,
        check_embedding_ctx_length=False,
        request_timeout=cfg.timeout,
    )

    logger.info(
        f"Init embedding model: {cfg.model} "
        f"(dimensions={cfg.dimension}, timeout={cfg.timeout}s)"
    )
    return _model
