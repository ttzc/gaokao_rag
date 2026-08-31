# tests/test_agent_search.py
"""查询侧「搜索信息」子 Agent 的配置/约束测试（mock LLM + mock 检索工具，不计费）。

覆盖：
- create_search_agent() 工厂结构（name/description/instruction/tools/model）
- 工具挂载为 knowledge_search + get_question_detail 两个：前者是 retrieve_tool
  的惰性导出（patch retrieve_tool._tool 顶掉实体化，测试不建真实 GaokaoKnowledge、
  不碰 chroma——同 tests/test_agent_storage_decision _make_agent 思路），
  后者是模块级 FunctionTool 实例（构造零副作用，直接断言同一对象）
- 惰性实体化纪律：import search 模块不触发 knowledge_search_tool 实体化
  （PEP 562 惰性导出 + 工厂内属性访问的 CI 教训回归）
- instruction 关键约束：只读检索执行者定位 + knowledge_search/get_question_detail/
  search_results/no_result/has_image 契约标记 + 详情补全约束（doc_id 来源、≤5 次）
  + 红线（不编造/不回答用户/改写上限 3 次），
  且不含 Leader 侧回显/收集去向表述（防职责回潮）
- 无 Skill 挂载：tools 不含 SkillToolSet、skill_repository / before_agent_callback
  均为 None
- 分层铁律：AST 解析 src/agent/retrieval/ 全部文件，无 src.store import
  （同 tests/test_agent_storage_decision.py TestLayeringRule 思路）
- 所有用例只用 mock LLM，无网络 / 计费调用
"""

from __future__ import annotations

import ast
import contextlib
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool
from trpc_agent_sdk.skills import SkillToolSet

import src.agent.tools.retrieve_tool as retrieve_tool
from src.agent.retrieval import search as se
from src.agent.retrieval.prompts import SEARCH_INSTRUCTION


def _fake_search_tool() -> MagicMock:
    """检索工具的 mock：**带 spec**——LlmAgent 对 tools 做 pydantic 校验
    （isinstance ToolSetABC），裸 MagicMock 会被拒；spec 真实工具类后 isinstance 通过。
    """
    return MagicMock(spec=LangchainKnowledgeSearchTool)


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 工厂
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateSearchAgent:
    """create_search_agent() 工厂产出正确的 LlmAgent。"""

    def _make_agent(self) -> tuple[LlmAgent, MagicMock]:
        """构造被测 agent，mock 掉 LLM 工厂 + 顶掉检索工具实体化。

        ``get_llm_model`` 逐模块替换（from-import 把函数对象绑进消费模块全局）；
        ``retrieve_tool._tool`` 塞 MagicMock——惰性导出首次**访问属性**才建真实
        GaokaoKnowledge（chroma 文件句柄 + embeddings 实例），patch 后工厂拿到的
        就是这个 mock，单元测试零存储副作用。
        """
        fake_model = MagicMock()
        fake_tool = _fake_search_tool()
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(se, "get_llm_model", return_value=fake_model)
            )
            stack.enter_context(patch.object(retrieve_tool, "_tool", fake_tool))
            agent = se.create_search_agent()
        return agent, fake_tool

    def test_name(self) -> None:
        agent, _ = self._make_agent()
        assert agent.name == "search"
        assert se.AGENT_NAME == "search"

    def test_description(self) -> None:
        agent, _ = self._make_agent()
        assert agent.description

    def test_instruction_loaded_from_prompts(self) -> None:
        """instruction 来自 prompts.py 常量模块（长 prompt 抽独立文件）。"""
        agent, _ = self._make_agent()
        assert agent.instruction == SEARCH_INSTRUCTION

    def test_tools_mounted(self) -> None:
        """挂两个工具：惰性导出的 knowledge_search_tool（顶包后的 mock）+
        模块级业务查询实例 get_question_detail_tool（与 tools 层同一对象）。"""
        agent, fake_tool = self._make_agent()
        assert list(agent.tools) == [
            fake_tool, retrieve_tool.get_question_detail_tool,
        ]

    def test_model_from_llm_factory(self) -> None:
        """模型走 src/api/llm.py 工厂，不重复造模型。"""
        fake_model = MagicMock()
        fake_tool = _fake_search_tool()
        with contextlib.ExitStack() as stack:
            m = stack.enter_context(
                patch.object(se, "get_llm_model", return_value=fake_model)
            )
            stack.enter_context(patch.object(retrieve_tool, "_tool", fake_tool))
            agent = se.create_search_agent()
        m.assert_called_once_with()
        assert agent.model is fake_model


# ═══════════════════════════════════════════════════════════════════════════════
# 惰性实体化纪律（PEP 562 CI 教训回归）
# ═══════════════════════════════════════════════════════════════════════════════


class TestLazyToolMaterialization:
    """import search 模块绝不触发工具实体化（CI 无 .env 也能 collect）。"""

    def test_module_import_does_not_materialize_tool(self) -> None:
        """reload search 后 retrieve_tool._tool 仍为 None。

        search.py 顶层只 import retrieve_tool 模块对象，knowledge_search_tool 的
        属性访问被推迟到工厂内——reload 重跑模块顶层代码，若哪天改回顶层
        from-import（import 期即实体化），本用例即红。
        前置：_tool 已被其他测试实体化时跳过（本仓库测试不真建工具，正常不触发）。
        """
        if retrieve_tool._tool is not None:
            pytest.skip("工具已被实体化，仅对新鲜 import 成立")
        importlib.reload(se)
        assert retrieve_tool._tool is None


# ═══════════════════════════════════════════════════════════════════════════════
# instruction 关键约束（职责边界写进 prompt，防回潮）
# ═══════════════════════════════════════════════════════════════════════════════


class TestSearchInstruction:
    """instruction 承载「只读检索执行者」定位与检索契约。"""

    def test_tool_and_contract_markers(self) -> None:
        """工具名与输入/输出契约字段对模型可见（委派按此对齐）。"""
        for marker in (
            "knowledge_search", "get_question_detail",
            "search_results", "no_result",
            "has_image", "doc_type", "doc_id", "score",
        ):
            assert marker in SEARCH_INSTRUCTION

    def test_detail_tool_constraints(self) -> None:
        """详情补全约束写进指令：仅 question 条目（note 走 kn_* 不适用）、
        question_id 来源（doc_id 数字部分）、只查真实召回、单轮 ≤5 次上限。"""
        for marker in ("仅对 `doc_type=\"question\"`", "kn_*", "不适用本工具",
                       "数字部分", "真实", "不超过 5 次"):
            assert marker in SEARCH_INSTRUCTION

    def test_hybrid_recall_semantics(self) -> None:
        """混合检索设计：题目 + 讲解一起召回、不筛选不分子意图。"""
        for marker in ("question", "note", "两类都保留"):
            assert marker in SEARCH_INSTRUCTION

    def test_rewrite_cap_stated(self) -> None:
        """弱召回改写上限（累计不超过 3 次）写进指令。"""
        assert "不超过 3 次" in SEARCH_INSTRUCTION

    def test_red_lines(self) -> None:
        """红线：只读 / 不编造 / 不回答用户 / 不改写内容。"""
        assert "只读" in SEARCH_INSTRUCTION
        assert "不编造" in SEARCH_INSTRUCTION
        assert "不回答用户" in SEARCH_INSTRUCTION
        assert "不改写内容" in SEARCH_INSTRUCTION

    def test_no_leader_echo_flow(self) -> None:
        """防职责回潮：Leader 侧回显/收集去向的对话流程表述不得渗入。"""
        for banned in ("回显", "询问每题去向", "入库 / 跳过"):
            assert banned not in SEARCH_INSTRUCTION


# ═══════════════════════════════════════════════════════════════════════════════
# 无 Skill 挂载
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoSkillMounted:
    """检索环节无归一化职责，不挂 Skill、无收紧钩子。"""

    def _make_agent(self) -> LlmAgent:
        agent, _ = TestCreateSearchAgent()._make_agent()
        return agent

    def test_tools_has_no_skill_toolset(self) -> None:
        assert not any(isinstance(t, SkillToolSet) for t in self._make_agent().tools)

    def test_no_skill_repository(self) -> None:
        assert self._make_agent().skill_repository is None

    def test_no_before_agent_callback(self) -> None:
        assert self._make_agent().before_agent_callback is None


# ═══════════════════════════════════════════════════════════════════════════════
# 分层铁律：agent 层不得 import src.store
# ═══════════════════════════════════════════════════════════════════════════════


class TestLayeringRule:
    """查询侧只准走工具层（其内部才碰 src.retrieval 门面），严禁直连存储。

    用 AST 解析真实 import 而非文本 grep：文件注释里合法出现"严禁 import
    src.store"字样（说明铁律本身），grep 会误报（同 tests/test_agent_tools.py）。
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

    def test_no_store_import_in_retrieval_package(self) -> None:
        """查询侧全部模块文件扫一遍，防未来新 agent 违规。"""
        pkg_dir = Path(se.__file__).parent
        py_files = sorted(pkg_dir.glob("*.py"))
        assert py_files, "查询侧包内应有 .py 文件可测"
        for path in py_files:
            mods = self._imported_modules(path)
            violations = [
                m for m in mods if m == "src.store" or m.startswith("src.store.")
            ]
            assert violations == [], f"{path.name} 违反分层铁律: {violations}"
