# tests/test_agent_leader.py
"""Team Leader（MVP 临时版）的配置/约束测试（mock LLM，不计费）。

覆盖：
- create_gaokao_leader() 工厂结构：TeamAgent、name、members 恰为三个子 Agent
  工厂产物（搜索信息在前 + 结构识别 + 入库决策，以 instruction/tools 同源断言
  "确为工厂产物"）、share_member_interactions=False、无 tools、模型走 llm 工厂
- 工厂各调用一次：leader 不绕开工厂自造成员
- LEADER_INSTRUCTION 关键约束：双闭环流程词（回显/打包/检索作答）、上下文隔离表述
  （成员不回看对话）、3 条铁律标记、MVP 降级（错因暂不支持 / topic_names 不传）；
  且不含完整版编排表述（9 成员/意图识别/未实现成员名等，防范围回潮）
- 分层铁律：AST 解析 src/agent/ 全部 .py，无 src.store import
  （同 tests/test_agent_storage_decision.py TestLayeringRule 思路）
- 全部用例 mock 掉四处 get_llm_model 绑定 + 顶掉 knowledge_search_tool 惰性实体化，
  不读真实 config/.env、不建真实 GaokaoKnowledge、无网络/计费
"""

from __future__ import annotations

import ast
import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import src.agent as agent_pkg
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.teams import TeamAgent

from src.agent import leader as ld
from src.agent.ingestion import storage_decision as sd
from src.agent.ingestion import structure_recognition as sr
from src.agent.ingestion.prompts import (
    STORAGE_DECISION_INSTRUCTION,
    STRUCTURE_RECOGNITION_INSTRUCTION,
)
from src.agent.retrieval import search as se
from src.agent.retrieval.prompts import SEARCH_INSTRUCTION
from src.agent.tools import retrieve_tool
from src.agent.tools.ingest_tool import ingest_question_tool


def _fake_search_tool() -> MagicMock:
    """检索工具的 mock：**带 spec**——LlmAgent 对 tools 做 pydantic 校验
    （isinstance ToolSetABC），裸 MagicMock 会被拒；spec 真实工具类后 isinstance 通过。
    """
    return MagicMock(spec=LangchainKnowledgeSearchTool)


def _make_leader() -> tuple[TeamAgent, MagicMock, MagicMock]:
    """构造被测 leader，mock 四处 ``get_llm_model`` 绑定 + 顶掉检索工具实体化。

    from-import 在导入时把函数对象绑进各消费模块全局，须逐模块替换
    （同 tests/test_agent_storage_decision._make_agent 思路，×4 个模块）；
    ``retrieve_tool._tool`` 塞 MagicMock——search 工厂在构造期访问惰性导出属性会
    实体化 GaokaoKnowledge（chroma 句柄），patch 后拿到 mock 工具，测试零存储副作用。
    """
    fake_model = MagicMock()
    fake_search_tool = _fake_search_tool()
    with contextlib.ExitStack() as stack:
        for mod in (ld, sr, sd, se):
            stack.enter_context(
                patch.object(mod, "get_llm_model", return_value=fake_model)
            )
        stack.enter_context(
            patch.object(retrieve_tool, "_tool", fake_search_tool)
        )
        team = ld.create_gaokao_leader()
    return team, fake_model, fake_search_tool


# ═══════════════════════════════════════════════════════════════════════════════
# TeamAgent 工厂
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateGaokaoLeader:
    """create_gaokao_leader() 工厂产出正确的 MVP TeamAgent。"""

    def test_returns_team_agent(self) -> None:
        team, _, _ = _make_leader()
        assert isinstance(team, TeamAgent)

    def test_name(self) -> None:
        team, _, _ = _make_leader()
        assert team.name == "gaokao_leader"
        assert ld.AGENT_NAME == "gaokao_leader"

    def test_members_exactly_three_factories(self) -> None:
        """members 恰为搜索信息 + 结构识别 + 入库决策三个成员（MVP 范围）。"""
        team, _, _ = _make_leader()
        assert len(team.members) == 3
        assert all(isinstance(m, LlmAgent) for m in team.members)
        assert [m.name for m in team.members] == [
            se.AGENT_NAME,
            sr.AGENT_NAME,
            sd.AGENT_NAME,
        ]

    def test_members_are_factory_products(self) -> None:
        """确为三个工厂的产物：instruction 与工厂常量同源、工具面与工厂装配一致。

        不比对对象身份（工厂每次构造新实例），改比对工厂装配的确定性产物：
        搜索信息挂 knowledge_search_tool（retrieve_tool 惰性导出的同一实例，
        经 _make_leader 顶包）、结构识别挂 SkillToolSet（question-organize 归一）、
        入库决策挂 ingest_question_tool（纯写库）——绕开工厂自造的成员过不了这关。
        """
        team, _, fake_search_tool = _make_leader()
        search_agent, struct_agent, store_agent = team.members
        assert search_agent.instruction == SEARCH_INSTRUCTION
        assert list(search_agent.tools) == [fake_search_tool]
        assert struct_agent.instruction == STRUCTURE_RECOGNITION_INSTRUCTION
        assert any(isinstance(t, SkillToolSet) for t in struct_agent.tools)
        assert store_agent.instruction == STORAGE_DECISION_INSTRUCTION
        assert store_agent.tools == [ingest_question_tool]

    def test_factories_called_once_each(self) -> None:
        """三个成员工厂各被调用一次，不重复构造。"""
        fake_model = MagicMock()
        with contextlib.ExitStack() as stack:
            for mod in (ld, sr, sd, se):
                stack.enter_context(
                    patch.object(mod, "get_llm_model", return_value=fake_model)
                )
            stack.enter_context(
                patch.object(retrieve_tool, "_tool", _fake_search_tool())
            )
            m_se = stack.enter_context(
                patch.object(
                    ld,
                    "create_search_agent",
                    side_effect=se.create_search_agent,
                )
            )
            m_sr = stack.enter_context(
                patch.object(
                    ld,
                    "create_structure_recognition_agent",
                    side_effect=sr.create_structure_recognition_agent,
                )
            )
            m_sd = stack.enter_context(
                patch.object(
                    ld,
                    "create_storage_decision_agent",
                    side_effect=sd.create_storage_decision_agent,
                )
            )
            team = ld.create_gaokao_leader()
        m_se.assert_called_once_with()
        m_sr.assert_called_once_with()
        m_sd.assert_called_once_with()
        assert [m.name for m in team.members] == [
            se.AGENT_NAME,
            sr.AGENT_NAME,
            sd.AGENT_NAME,
        ]

    def test_share_member_interactions_false(self) -> None:
        """函数式隔离：成员不共享本回合交互历史（设计约定显式钉死在构造里）。"""
        team, _, _ = _make_leader()
        assert team.share_member_interactions is False

    def test_share_team_history_false(self) -> None:
        """成员同样不回看全量对话——隔离策略的另一半，依赖框架默认 False。"""
        team, _, _ = _make_leader()
        assert team.share_team_history is False

    def test_leader_has_no_tools(self) -> None:
        """Leader 纯编排：除框架委派工具外不挂业务 tools（写库在成员，门面在 tools 层）。"""
        team, _, _ = _make_leader()
        assert list(team.tools) == []

    def test_model_from_llm_factory(self) -> None:
        """模型走 src/api/llm.py 的唯一工厂，不重复造模型。"""
        fake_model = MagicMock()
        with contextlib.ExitStack() as stack:
            m = stack.enter_context(
                patch.object(ld, "get_llm_model", return_value=fake_model)
            )
            for mod in (sr, sd, se):
                stack.enter_context(
                    patch.object(mod, "get_llm_model", return_value=fake_model)
                )
            stack.enter_context(
                patch.object(retrieve_tool, "_tool", _fake_search_tool())
            )
            team = ld.create_gaokao_leader()
        m.assert_called_once_with()
        assert team.model is fake_model

    def test_instruction_is_module_constant(self) -> None:
        """instruction 即本模块 LEADER_INSTRUCTION（leader 层不抽 prompts 模块）。"""
        team, _, _ = _make_leader()
        assert team.instruction == ld.LEADER_INSTRUCTION
        assert team.instruction


# ═══════════════════════════════════════════════════════════════════════════════
# LEADER_INSTRUCTION 关键约束（双闭环流程 + 隔离 + 铁律写进 prompt，防范围回潮）
# ═══════════════════════════════════════════════════════════════════════════════


class TestLeaderInstruction:
    """指令承载 MVP 双闭环流程、上下文隔离与 3 条铁律。"""

    def test_mvp_closed_loops_and_member_names(self) -> None:
        """双闭环定位与三个成员名（delegate_to_member 按名委派，必须出现）。

        输入定位是「待清洗信息」而非仅口述——口述/OCR 多题/粘贴文本来源同权，
        指令须泛化表述并列举来源（2026-08-28 用户修正）。
        """
        for marker in ("待清洗", "口述", "OCR", "入库",
                       "structure_recognition", "storage_decision",
                       "search", "查询闭环", "检索"):
            assert marker in ld.LEADER_INSTRUCTION
        # 不得回退成"只认口述"的窄定位
        assert "学生口述题目" not in ld.LEADER_INSTRUCTION

    def test_intent_routing_inline(self) -> None:
        """意图分流内联 Leader（2026-08-28 决策）：问→查询、给→摄入 + 判不准先追问。"""
        for marker in ("先判断", "追问"):
            assert marker in ld.LEADER_INSTRUCTION

    def test_data_contract_fields(self) -> None:
        """State 契约字段名对 Leader 可见（打包 task 时要原样引用）。"""
        for marker in ("pending_questions", "lecture_segments", "ingest_decisions",
                       "ingest_results", "search_results", "no_result"):
            assert marker in ld.LEADER_INSTRUCTION

    def test_flow_keywords(self) -> None:
        """流程关键动作：回显、询问去向、打包委派、汇总返回、检索作答。"""
        for marker in ("回显", "去向", "打包", "汇总", "作答"):
            assert marker in ld.LEADER_INSTRUCTION

    def test_context_isolation_stated(self) -> None:
        """隔离表述：成员不回看对话，全部上下文打包进 task。"""
        assert "成员不回看对话" in ld.LEADER_INSTRUCTION
        assert "写进 task" in ld.LEADER_INSTRUCTION
        assert "只有你与用户对话" in ld.LEADER_INSTRUCTION

    def test_three_iron_rules(self) -> None:
        """3 条铁律逐条在场：完成标准 / 调用上限 / 不自相矛盾。"""
        assert "完成标准" in ld.LEADER_INSTRUCTION
        assert "不再委派" in ld.LEADER_INSTRUCTION
        assert "最多委派一次" in ld.LEADER_INSTRUCTION
        assert "不自相矛盾" in ld.LEADER_INSTRUCTION

    def test_mvp_degradations(self) -> None:
        """MVP 降级：错题告知暂不支持、讲解段忽略、topic_names 不传。"""
        assert "错因记录暂不支持" in ld.LEADER_INSTRUCTION
        assert "topic_names" in ld.LEADER_INSTRUCTION

    def test_no_full_version_claims(self) -> None:
        """防范围回潮：完整版 9 成员/意图分流子 Agent/未实现成员名不得出现。

        2026-08-29 更新：search 已落地，「搜索信息」「查询侧」不再是回潮标记；
        仍未实现的成员（文档识别/知识整理/聚合数据/输出整理/VLM 等）继续钉死。
        """
        for banned in (
            "9 成员",
            "意图分类",
            "意图识别",
            "文档识别",
            "知识整理",
            "输出整理",
            "聚合数据",
            "VLM",
        ):
            assert banned not in ld.LEADER_INSTRUCTION


# ═══════════════════════════════════════════════════════════════════════════════
# 分层铁律：agent 层不得 import src.store
# ═══════════════════════════════════════════════════════════════════════════════


class TestLayeringRule:
    """agent 层（含 leader）只准走门面，严禁 import src.store.*。

    用 AST 解析真实 import 而非文本 grep：文件注释里合法出现"严禁 import
    src.store"字样（说明铁律本身），grep 会误报。扫描范围是 src/agent/ 全部
    .py（含子包），leader 落地后由本测试守住整层。
    """

    @staticmethod
    def _imported_modules(path: Path) -> set[str]:
        """收集文件中全部 import / from-import 的模块名（含子模块全名）。"""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        return mods

    def test_no_store_import_in_agent_package(self) -> None:
        """src/agent/ 递归全部模块文件扫一遍，防未来新 agent 违规。"""
        pkg_dir = Path(agent_pkg.__file__).parent
        py_files = sorted(pkg_dir.rglob("*.py"))
        assert py_files, "agent 包内应有 .py 文件可测"
        for path in py_files:
            mods = self._imported_modules(path)
            violations = [
                m for m in mods if m == "src.store" or m.startswith("src.store.")
            ]
            assert violations == [], f"{path} 违反分层铁律: {violations}"
