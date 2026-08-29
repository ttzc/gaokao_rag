# src/agent/retrieval/search.py
# 查询侧「搜索信息」子 Agent：查询链路的第一棒，唯一挂载 knowledge_search
# 语义检索工具（src/agent/tools/retrieve_tool.py 的框架工具），把 Leader 打包的
# 检索任务变成结构化召回清单（search_results / no_result）。
#
# 职责边界（见 docs/agent/retrieval/search.md）：
#   - 只做混合检索召回（题目 + 讲解同 Collection 一起召回，不分子意图），
#     **不写库、不对话、不组织最终答案**——组织回答归 Leader。
#   - 分层铁律：agent 层严禁 import src.store.*；检索能力全部经 tools 层注入
#     （retrieve_tool 内部走 src.retrieval 读门面）。
#
# 工具获取方式（与 ingest 侧的差异，2026-08-28 CI 教训决定）：
#   - ingest_question_tool 是普通 FunctionTool，import 零副作用，摄入侧顶层
#     from-import 即可；knowledge_search_tool 走 PEP 562 惰性导出——**首次访问
#     属性**才实体化 GaokaoKnowledge（需 .env 的 DASHSCOPE_API_KEY），顶层
#     from-import 会在 import 本模块时就建工具，CI 无 .env 直接崩。
#   - 故本模块顶层只 import retrieve_tool 模块对象（零副作用），工厂运行时才
#     访问 retrieve_tool.knowledge_search_tool 完成实体化——延迟点与子 Agent
#     工厂「import 不建实例」的约定一致。
#   - 后续挂业务查询工具（search_questions / browse_questions 等）时同样处理，
#     见 docs/agent/tools/retrieve_tool.md「工具清单」。

from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent

from src.agent.retrieval.prompts import SEARCH_INSTRUCTION
from src.agent.tools import retrieve_tool
from src.api.llm import get_llm_model

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

AGENT_NAME = "search"

AGENT_DESCRIPTION = (
    "查询侧搜索信息：按检索任务调用语义检索工具混合召回题目与知识点讲解"
    "（不分子意图），产出结构化召回清单 search_results（doc_id / doc_type / "
    "score / has_image / 摘要）；无召回时输出 no_result"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 工厂
# ═══════════════════════════════════════════════════════════════════════════════


def create_search_agent() -> LlmAgent:
    """构造查询侧「搜索信息」子 Agent，供 TeamAgent 挂进 members。

    不做模块级单例：构造会触发 ``get_llm_model()``（读取 config + .env）与
    ``knowledge_search_tool`` 的惰性实体化（构造 GaokaoKnowledge），
    import 时执行会在无环境变量的干净环境抛 RuntimeError，故只暴露工厂，
    由调用方（TeamAgent leader 构造）在运行时按需创建。

    模型走 src/api/llm.py 的唯一工厂（与摄入侧子 Agent 同一单例，不重复造模型）。
    """
    return LlmAgent(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        model=get_llm_model(),
        instruction=SEARCH_INSTRUCTION,
        tools=[retrieve_tool.knowledge_search_tool],
    )
