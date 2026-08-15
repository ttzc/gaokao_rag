# src/api/llm.py
# 对话模型客户端层：封装 DeepSeek 官方 API（OpenAI 兼容协议）。
#
# 设计：
#   - DeepSeekModel    — OpenAIModel 子类，声明经过测试可用的模型白名单
#   - get_llm_model()  — 懒初始化单例，全项目共用一个 LLM 实例
#
# 配置来源：config.llm（model / api_key / base_url），由 config.toml + .env 提供。
# 敏感信息走环境变量，不硬编码。

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel, OpenAIModel, shared_http_client_provider_factory

from src.config import config

# 模块级单例，首次调用 get_llm_model() 时初始化。
# Python 模块导入顺序保证只赋值一次（GIL 保护单次赋值原子性）。
_model: LLMModel | None = None


class DeepSeekModel(OpenAIModel):
    """DeepSeek 官方 API（OpenAI 兼容协议）的模型适配类。

    OpenAIModel 继承了 LLMModel 的抽象成员 ``supported_models``，
    必须在此实现，否则 DeepSeekModel 仍是抽象类无法实例化。

    注意：此列表是**经过测试可用的模型白名单**，不是从 config.toml 动态读取。
    用户只能在列表中选择模型，不能在 config.toml 里写任意模型名。
    新增模型需要先在代码里加入列表并验证通过，再开放给用户配置。
    """

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["deepseek-v4-flash", "deepseek-v4-pro"]


def get_llm_model() -> LLMModel:
    """返回 LLM 单例实例，懒初始化。

    首次调用时从 ``config.llm`` 读取模型名称 / API Key / 基地址，
    创建 DeepSeekModel 实例并缓存。后续调用直接返回缓存。

    Raises:
        RuntimeError: 若 ``config.llm.api_key`` 仍是未解析的 ``${VAR}`` 占位符，
            说明 .env 中缺少对应的环境变量，调用方能直接定位配置问题。
    """
    global _model
    if _model:
        return _model
    cfg = config.llm
    if cfg.api_key.startswith("${"):
        raise RuntimeError(
            f"LLM api_key 未解析（仍是 '{cfg.api_key}'），"
            "请检查 .env 中是否设置了 DEEPSEEK_API_KEY"
        )
    if (cfg.model not in DeepSeekModel.supported_models()):
        raise RuntimeError(
            f"LLM model '{cfg.model}' 不在支持列表 {DeepSeekModel.supported_models()} 中，"
            "请检查 config.toml 中 llm.model 配置"
        )
    _model = DeepSeekModel(
        model_name=cfg.model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        http_client_provider_factory=shared_http_client_provider_factory
    )
    logger.info(f"Init llm model: {cfg.model}")
    return _model
