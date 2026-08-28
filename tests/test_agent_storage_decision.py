# tests/test_agent_storage_decision.py
"""摄入侧「入库决策」子 Agent 的配置/约束测试（mock LLM，不计费）。

覆盖：
- create_storage_decision_agent() 工厂结构（name/instruction/tools/model）
- instruction 关键约束：纯写库执行者定位 + 不发起对话/不回显/不收集决策，
  且不含 Leader 侧回显/收集决策流程表述（防职责回潮，2026-08-28 边界决议）
- 无 Skill 挂载（无归一化职责）：tools 不含 SkillToolSet、skill_repository /
  before_agent_callback 均为 None
- 分层铁律：AST 解析 src/agent/ingestion/ 全部文件，无 src.store import
  （同 tests/test_agent_tools.py TestLayeringRule 思路，agent 本体不碰存储，
  写库只经 ingest_question_tool）
- 所有用例只用 mock LLM，无网络 / 计费调用
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.skills import SkillToolSet

from src.agent.ingestion import storage_decision as st
from src.agent.ingestion.prompts import STORAGE_DECISION_INSTRUCTION
from src.agent.tools.ingest_tool import ingest_question_tool


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 工厂
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateStorageDecisionAgent:
    """create_storage_decision_agent() 工厂产出正确的 LlmAgent。"""

    def _make_agent(self) -> LlmAgent:
        """构造被测 agent，mock 掉 LLM 工厂（不读取真实 config/.env）。"""
        fake_model = MagicMock()
        with patch.object(st, "get_llm_model", return_value=fake_model):
            return st.create_storage_decision_agent()

    def test_name(self) -> None:
        agent = self._make_agent()
        assert agent.name == "storage_decision"

    def test_description(self) -> None:
        agent = self._make_agent()
        assert agent.description

    def test_instruction_loaded_from_prompts(self) -> None:
        """instruction 来自 prompts.py 常量模块（长 prompt 抽独立文件）。"""
        agent = self._make_agent()
        assert agent.instruction == STORAGE_DECISION_INSTRUCTION
        # 输入/工具/输出契约的关键标记对模型可见
        assert "pending_questions" in agent.instruction
        assert "ingest_decisions" in agent.instruction
        assert "ingest_question" in agent.instruction
        assert "ingest_results" in agent.instruction

    def test_tools_contains_ingest_question_tool(self) -> None:
        """唯一工具是写库 FunctionTool 实例（与 tools 层交付物同一个对象）。"""
        agent = self._make_agent()
        assert len(agent.tools) == 1
        assert agent.tools[0] is ingest_question_tool

    def test_model_from_llm_factory(self) -> None:
        """模型走 src/api/llm.py 工厂，不重复造模型。"""
        fake_model = MagicMock()
        with patch.object(st, "get_llm_model", return_value=fake_model) as m:
            agent = st.create_storage_decision_agent()
        m.assert_called_once_with()
        assert agent.model is fake_model


# ═══════════════════════════════════════════════════════════════════════════════
# instruction 关键约束（职责边界写进 prompt，防回潮）
# ═══════════════════════════════════════════════════════════════════════════════


class TestInstructionConstraints:
    """instruction 承载「纯写库执行者」定位，Leader 职责表述不得渗入。"""

    def test_executor_role_and_no_dialogue(self) -> None:
        assert "写库执行者" in STORAGE_DECISION_INSTRUCTION
        assert "不发起对话" in STORAGE_DECISION_INSTRUCTION
        assert "不做回显" in STORAGE_DECISION_INSTRUCTION
        assert "不收集决策" in STORAGE_DECISION_INSTRUCTION

    def test_three_decisions_covered(self) -> None:
        """入库 / 错题 / 跳过三条分流路径与「先题后错」降级都在指令里。"""
        for marker in ("入库", "错题", "跳过", "先题后错", "error_pending"):
            assert marker in STORAGE_DECISION_INSTRUCTION

    def test_missing_decision_returns_pending(self) -> None:
        """决策缺失 → 标 pending 交还 Leader，不擅自猜测。"""
        assert "pending" in STORAGE_DECISION_INSTRUCTION
        assert "不擅自猜测" in STORAGE_DECISION_INSTRUCTION

    def test_no_leader_echo_flow(self) -> None:
        """防职责回潮：回显/收集决策的对话流程表述（docs 中 Leader 侧示意）
        不能出现在本 Agent 指令里。"""
        for banned in ("回复格式", "题号 + 操作", "全部入库？", "已识别到"):
            assert banned not in STORAGE_DECISION_INSTRUCTION


# ═══════════════════════════════════════════════════════════════════════════════
# 无 Skill 挂载（无归一化职责）
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoSkillMounted:
    """归一化在结构识别环节已完成，本 Agent 不挂 Skill、无收紧钩子。"""

    def _make_agent(self) -> LlmAgent:
        fake_model = MagicMock()
        with patch.object(st, "get_llm_model", return_value=fake_model):
            return st.create_storage_decision_agent()

    def test_tools_has_no_skill_toolset(self) -> None:
        agent = self._make_agent()
        assert not any(isinstance(t, SkillToolSet) for t in agent.tools)

    def test_no_skill_repository(self) -> None:
        assert self._make_agent().skill_repository is None

    def test_no_before_agent_callback(self) -> None:
        assert self._make_agent().before_agent_callback is None


# ═══════════════════════════════════════════════════════════════════════════════
# 分层铁律：agent 层不得 import src.store
# ═══════════════════════════════════════════════════════════════════════════════


class TestLayeringRule:
    """agent 层只准走 src/ingestion（写）/ src/retrieval（读）门面，写库经工具。

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

    def test_no_store_import_in_ingestion_package(self) -> None:
        """摄入侧全部模块文件扫一遍，防未来新 agent 违规。"""
        pkg_dir = Path(st.__file__).parent
        py_files = sorted(pkg_dir.glob("*.py"))
        assert py_files, "摄入侧包内应有 .py 文件可测"
        for path in py_files:
            mods = self._imported_modules(path)
            violations = [
                m for m in mods if m == "src.store" or m.startswith("src.store.")
            ]
            assert violations == [], f"{path.name} 违反分层铁律: {violations}"
