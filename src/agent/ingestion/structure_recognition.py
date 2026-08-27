# src/agent/ingestion/structure_recognition.py
# 摄入侧「结构识别」子 Agent：摄入链路的第二棒，把文档内容从「整篇文本」切分为
# 「讲解段 + 题目段」的集合；讲解段原样保留，每道题目（整篇切出的或零散单题）都加载
# question-organize Skill 归一为「题目 / 答案 / 解析」三段。
#
# 职责边界（见 docs/agent/ingestion/structure_recognition.md）：
#   - 只做语义划分与整理，**不写库**——tools 只有 skill_tool_set（question-organize 纯
#     prompt），连 src/ingestion 写门面都不调用，写入由下游入库决策 Agent 完成。
#   - 分层铁律：agent 层只允许 import src/ingestion（写）/ src/retrieval（读）门面，
#     严禁 import src.store.*。
#
# Skill 工具面决策（knowledge_only vs full，无官方示例，自定并注释理由）：
#   - question-organize 是**纯指令 Skill**（无 scripts/、无命令可执行），按
#     full profile 会向模型暴露 skill_run / skill_exec / skill_select_tools 等 10 个
#     内置工具，诱导模型空转（run 一个不存在的脚本必然失败）。
#   - 故采用 knowledge_only（SkillProfileNames.KNOWLEDGE_ONLY）：工具面收紧为
#     skill_load / skill_select_docs / skill_list_docs，与本环节「取指令正文、不执行」的
#     定位一致，prompt 噪声更小。
#   - 共享构造：skill 仓库全员复用，create_skill_tool_set() / SKILLS_ROOT 在
#     src/agent/skills/__init__.py；构造时传本 agent 的 skill 白名单（ALLOWED_SKILLS，
#     硬约束：名单外不进 prompt、skill_load 报错），profile 收紧是本模块自己的事。
#   - 注入时机：BaseAgent.before_agent_callback（FilterType.AGENT 过滤器）。此时
#     run_async 已创建 agent_context（_base_agent.py run_async 内
#     ctx.agent_context = create_agent_context()），且早于 LlmProcessor 构建请求时读取的
#     skill_processor 参数（_request_processor.py 内
#     get_skill_processor_parameters(ctx.agent_context)）。每次调用幂等。

from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents.core import set_skill_processor_parameters
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.skills import SkillProfileNames

from src.agent.ingestion.prompts import STRUCTURE_RECOGNITION_INSTRUCTION
from src.agent.skills import create_skill_tool_set
from src.api.llm import get_llm_model

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

AGENT_NAME = "structure_recognition"

AGENT_DESCRIPTION = "摄入侧结构识别：把整篇文本语义切分为讲解段（原样保留）与题目段，每道题目都按题目整理 Skill 归一为题目 / 答案 / 解析三段并各给一句话概括"

# 本 agent 的 skill 工具面：只加载（取指令正文），不执行任何内置 skill 命令
SKILL_TOOL_PROFILE = str(SkillProfileNames.KNOWLEDGE_ONLY)

# 本 agent 的 skill 白名单（硬约束，构造仓库时烘焙）：结构识别只该用题目整理，
# 未来加新 skill（如讲解段归并）也不会被本 agent 意外看见 / 加载
ALLOWED_SKILLS: tuple[str, ...] = ("question-organize",)


# ═══════════════════════════════════════════════════════════════════════════════
# skill 工具面收紧钩子
# ═══════════════════════════════════════════════════════════════════════════════


def _configure_skill_tool_profile(invocation_ctx: InvocationContext) -> None:
    """在每次请求前把 skill 工具面收紧为 knowledge_only。

    挂在 LlmAgent.before_agent_callback（BaseAgent 字段），框架在 agent 过滤器阶段
    （AgentCallbackFilter._before）以当前 InvocationContext 调用——此时 run_async 已创建
    agent_context、且早于 LlmProcessor 构建请求时读取 skill_processor 参数，对每次调用幂等。
    参数结构见 trpc_agent_sdk/skills/_skill_config.py 的 DEFAULT_SKILL_CONFIG。
    """
    set_skill_processor_parameters(
        invocation_ctx.agent_context,
        {"tool_profile": SKILL_TOOL_PROFILE},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 工厂
# ═══════════════════════════════════════════════════════════════════════════════


def create_structure_recognition_agent() -> LlmAgent:
    """构造摄入侧「结构识别」子 Agent，供 TeamAgent 挂进 members。

    不做模块级单例：构造会触发 ``get_llm_model()``（读取 config + .env），
    在 import 时执行会在无环境变量的干净环境抛出 RuntimeError，故只暴露工厂，
    由调用方（TeamAgent leader 构造）在运行时按需创建。

    模型走 src/api/llm.py 的唯一工厂（DeepSeek，OpenAI 兼容），不重复造模型。
    """
    skill_tool_set, skill_repository = create_skill_tool_set(ALLOWED_SKILLS)
    return LlmAgent(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        model=get_llm_model(),
        instruction=STRUCTURE_RECOGNITION_INSTRUCTION,
        tools=[skill_tool_set],
        skill_repository=skill_repository,
        before_agent_callback=_configure_skill_tool_profile,
    )