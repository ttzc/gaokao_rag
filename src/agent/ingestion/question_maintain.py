# src/agent/ingestion/question_maintain.py
# 摄入侧「题目维护」子 Agent：对**已入库题目**的写操作执行者（manage 分支）。
# 消费 Leader 打包的 {action: "update"|"delete", question_id, user_request,
# question_snapshot?, user_confirmed?}：改题走字段结构化 / 来源行拆解 / 知识点重标
# 后调 update_question 工具；删题是薄层，只调 delete_question 工具回传 cascade 统计，
# 汇总 manage_result 返回。
#
# 职责边界（见 docs/agent/ingestion/question_maintain.md）：
#   - **定位 question_id 与删前回显确认归 Leader**——Leader 是唯一持全量对话、
#     唯一与用户对话的节点；本 Agent 不发起对话、不做回显、不收集确认。
#   - **为什么改 / 删不挂 Leader**：Leader 是纯编排者（create_gaokao_leader()
#     不传 tools=），挂写工具等于把「只委派」改成「既委派又执行」；且改题有
#     实打实的 LLM 编排活（口述 → 字段结构化、来源行拆解映射），全塞 Leader 必然臃肿。
#   - **删题不可逆**（无软删除 / 回收站）：必须由 Leader 先向用户回显删除范围并
#     拿到确认（user_confirmed=true）后才委派执行，instruction 与工具 docstring 双重钉死。
#   - 分层铁律：agent 层只经 FunctionTool 调 src/ingestion 门面，严禁 import src.store.*。
#   - 知识点归位双路由（topic_draft，ingest 意图）职责待 V0.6c 接入（依赖
#     src/ingestion/topic.py 门面），本版只挂改 / 删两件工具，instruction 不引用
#     不存在的工具。
#
# 工厂模式同 storage_decision：不做模块级单例——构造会触发 get_llm_model()
# （读取 config + .env），import 时执行会在无环境变量的干净环境抛 RuntimeError，
# 故只暴露工厂，由调用方（TeamAgent leader 构造）在运行时按需创建。

from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent

from src.agent.ingestion.prompts import QUESTION_MAINTAIN_INSTRUCTION
from src.agent.tools.ingest_tool import delete_question_tool, update_question_tool
from src.api.llm import get_llm_model

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

AGENT_NAME = "question_maintain"

AGENT_DESCRIPTION = "摄入侧题目维护：对已入库题目执行改题（字段结构化后部分更新）/ 删题（级联删除，需 Leader 先回显确认）"


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 工厂
# ═══════════════════════════════════════════════════════════════════════════════


def create_question_maintain_agent() -> LlmAgent:
    """构造摄入侧「题目维护」子 Agent，供 TeamAgent 挂进 members（manage 分支）。

    本版只做改 / 删两件事；知识点归位双路由（topic_draft）职责待 V0.6c
    （src/ingestion/topic.py 门面）落地后接入，届时再补挂知识点工具。

    不做模块级单例：构造会触发 ``get_llm_model()``（读取 config + .env），
    在 import 时执行会在无环境变量的干净环境抛出 RuntimeError，故只暴露工厂，
    由调用方（TeamAgent leader 构造）在运行时按需创建。

    模型走 src/api/llm.py 的唯一工厂（DeepSeek，OpenAI 兼容），不重复造模型。
    tools 只有改 / 删两件写工具——删前回显确认归 Leader，本 Agent 不挂对话类能力。
    """
    return LlmAgent(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        model=get_llm_model(),
        instruction=QUESTION_MAINTAIN_INSTRUCTION,
        tools=[update_question_tool, delete_question_tool],
    )
