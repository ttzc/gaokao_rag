# src/agent/ingestion/storage_decision.py
# 摄入侧「入库决策」子 Agent：摄入链路的**写库执行者**。消费结构识别产出的
# pending_questions（已归一的题目 / 答案 / 解析三段）与 Leader 收集好的 ingest_decisions
# （每题去向：入库 / 错题 / 跳过），逐题调写库工具 ingest_question 分流落库，汇总
# ingest_results 返回。
#
# 职责边界（见 docs/agent/ingestion/storage_decision.md）：
#   - **只写库**——回显题目清单、与用户的多轮对话、收集去向都由 Leader 负责
#     （2026-08-28 用户明确）。本 Agent 不发起对话、不做回显、不收集决策。
#   - 不做归一化——题目三段是结构识别 + question-organize Skill 的产物，原样消费。
#     故不挂 Skill（无 skill_repository / SkillToolSet）、无 before_agent_callback。
#   - 分层铁律：agent 层只 import src/ingestion（写）/ src/retrieval（读）门面，严禁
#     import src.store.*。写库统一经 ingest_question_tool（其内部才碰门面）。
#   - 错题分支「先题后错」：ingest_error 门面 MVP 尚未落地，本轮只挂 ingest_question；
#     错题决策下题目照常入库，结果标 error_pending，错因记录交后续迭代。
#
# 工厂模式同 structure_recognition：不做模块级单例——构造会触发 get_llm_model()
# （读取 config + .env），import 时执行会在无环境变量的干净环境抛 RuntimeError，
# 故只暴露工厂，由调用方（TeamAgent leader 构造）在运行时按需创建。

from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent

from src.agent.ingestion.prompts import STORAGE_DECISION_INSTRUCTION
from src.agent.tools.ingest_tool import ingest_question_tool
from src.api.llm import get_llm_model

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

AGENT_NAME = "storage_decision"

AGENT_DESCRIPTION = "摄入侧入库决策：消费结构化题目 + 用户去向意图，调用写库工具执行入库/错题/跳过分流"


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 工厂
# ═══════════════════════════════════════════════════════════════════════════════


def create_storage_decision_agent() -> LlmAgent:
    """构造摄入侧「入库决策」子 Agent，供 TeamAgent 挂进 members。

    不做模块级单例：构造会触发 ``get_llm_model()``（读取 config + .env），
    在 import 时执行会在无环境变量的干净环境抛出 RuntimeError，故只暴露工厂，
    由调用方（TeamAgent leader 构造）在运行时按需创建。

    模型走 src/api/llm.py 的唯一工厂（DeepSeek，OpenAI 兼容），不重复造模型。
    tools 只有写库工具 ingest_question——本 Agent 无归一化职责，故不挂 Skill。
    """
    return LlmAgent(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        model=get_llm_model(),
        instruction=STORAGE_DECISION_INSTRUCTION,
        tools=[ingest_question_tool],
    )
