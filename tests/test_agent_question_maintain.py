# tests/test_agent_question_maintain.py
"""摄入侧「题目维护」子 Agent 的配置/约束测试（mock LLM，不计费）。

覆盖（仿 tests/test_agent_storage_decision.py 的组织）：
- create_question_maintain_agent() 工厂结构（name/instruction/tools/model）：
  tools 恰为改 / 删两件写工具，不含 ingest_question_tool（入库归入库决策）
- instruction 关键约束：写操作执行者定位 + 删除确认闸门（user_confirmed）+
  clarify 回传路径 + 部分更新 / 知识点全量替换语义 + 图形改动降级；
  且不含 Leader 侧回显/确认流程表述与未接入职责（topic_draft，V0.6c）
- 无 Skill 挂载（无归一化职责）：tools 不含 SkillToolSet、skill_repository /
  before_agent_callback 均为 None
- 分层铁律：AST 解析本模块文件，无 src.store import（写库只经工具）
- 所有用例只用 mock LLM，无网络 / 计费调用
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.skills import SkillToolSet

from src.agent.ingestion import question_maintain as qmt
from src.agent.ingestion.prompts import QUESTION_MAINTAIN_INSTRUCTION
from src.agent.tools.ingest_tool import (
    delete_question_tool,
    ingest_question_tool,
    update_question_tool,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 工厂
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateQuestionMaintainAgent:
    """create_question_maintain_agent() 工厂产出正确的 LlmAgent。"""

    def _make_agent(self) -> LlmAgent:
        """构造被测 agent，mock 掉 LLM 工厂（不读取真实 config/.env）。"""
        fake_model = MagicMock()
        with patch.object(qmt, "get_llm_model", return_value=fake_model):
            return qmt.create_question_maintain_agent()

    def test_name(self) -> None:
        agent = self._make_agent()
        assert agent.name == "question_maintain"
        assert qmt.AGENT_NAME == "question_maintain"

    def test_description(self) -> None:
        agent = self._make_agent()
        assert agent.description

    def test_instruction_loaded_from_prompts(self) -> None:
        """instruction 来自 prompts.py 常量模块（长 prompt 抽独立文件）。"""
        agent = self._make_agent()
        assert agent.instruction == QUESTION_MAINTAIN_INSTRUCTION
        assert agent.instruction
        # 输入/工具/输出契约的关键标记对模型可见
        for marker in ("action", "question_id", "user_request", "user_confirmed",
                       "update_question", "delete_question", "manage_result"):
            assert marker in agent.instruction

    def test_tools_exactly_update_and_delete(self) -> None:
        """工具面恰为改 / 删两件（与 tools 层交付物同一批对象），顺序 update 在前。"""
        agent = self._make_agent()
        assert agent.tools == [update_question_tool, delete_question_tool]

    def test_tools_not_contains_ingest_question(self) -> None:
        """入库是入库决策 Agent 的职责，本 Agent 不挂 ingest_question_tool。"""
        agent = self._make_agent()
        assert ingest_question_tool not in agent.tools

    def test_model_from_llm_factory(self) -> None:
        """模型走 src/api/llm.py 工厂，不重复造模型。"""
        fake_model = MagicMock()
        with patch.object(qmt, "get_llm_model", return_value=fake_model) as m:
            agent = qmt.create_question_maintain_agent()
        m.assert_called_once_with()
        assert agent.model is fake_model


# ═══════════════════════════════════════════════════════════════════════════════
# instruction 关键约束（manage 分支职责边界写进 prompt，防回潮）
# ═══════════════════════════════════════════════════════════════════════════════


class TestInstructionConstraints:
    """instruction 承载「写操作执行者」定位与删除确认闸门，Leader 职责不得渗入。"""

    def test_executor_role_and_no_dialogue(self) -> None:
        assert "写操作执行者" in QUESTION_MAINTAIN_INSTRUCTION
        assert "不发起对话" in QUESTION_MAINTAIN_INSTRUCTION
        assert "不做回显" in QUESTION_MAINTAIN_INSTRUCTION
        assert "不收集确认" in QUESTION_MAINTAIN_INSTRUCTION

    def test_delete_confirmation_gate(self) -> None:
        """删除确认闸门：user_confirmed 前置 + 不可逆表述 + 不许补删其他内容。"""
        assert "user_confirmed" in QUESTION_MAINTAIN_INSTRUCTION
        assert "绝不执行删除" in QUESTION_MAINTAIN_INSTRUCTION
        assert "不可逆" in QUESTION_MAINTAIN_INSTRUCTION
        assert "不许自作主张补删其他内容" in QUESTION_MAINTAIN_INSTRUCTION

    def test_update_semantics(self) -> None:
        """改题语义：部分更新（只传要改的字段）+ topic_names 全量替换 + 来源拆解。"""
        for marker in ("部分更新", "全量替换", "来源行拆解",
                       "exam_year", "exam_regions", "question_number",
                       "updated_fields"):
            assert marker in QUESTION_MAINTAIN_INSTRUCTION

    def test_clarify_path(self) -> None:
        """说不清改哪个字段 / 删除缺确认 → clarify 交 Leader 追问，不编造不猜。"""
        assert "clarify" in QUESTION_MAINTAIN_INSTRUCTION
        assert "不编造、不猜" in QUESTION_MAINTAIN_INSTRUCTION

    def test_delete_result_contract(self) -> None:
        """删题回传 cascade 统计（薄层——没有字段拆解、没有内容生成）。"""
        assert "cascade" in QUESTION_MAINTAIN_INSTRUCTION
        assert "薄层" in QUESTION_MAINTAIN_INSTRUCTION

    def test_image_degradation_and_no_missing_tools(self) -> None:
        """图形内容改动本版不支持；知识点归位工具（V0.6c）明确不接入、不许调用。"""
        assert "图形内容改动本版不支持" in QUESTION_MAINTAIN_INSTRUCTION
        assert "不调用不存在的工具" in QUESTION_MAINTAIN_INSTRUCTION
        # topic_draft 双路由职责待 V0.6c，本版 instruction 不得引用
        assert "topic_draft" not in QUESTION_MAINTAIN_INSTRUCTION

    def test_no_leader_echo_flow(self) -> None:
        """防职责回潮：定位题目 / 回显删除范围 / 收集确认是 Leader 侧流程表述，
        不能以「本 Agent 执行」的口吻出现在本指令里。"""
        for banned in ("回复格式", "确认删除第", "等待用户确认后再调用"):
            assert banned not in QUESTION_MAINTAIN_INSTRUCTION


# ═══════════════════════════════════════════════════════════════════════════════
# 无 Skill 挂载（无归一化职责）
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoSkillMounted:
    """改 / 删不涉及题目归一化，本 Agent 不挂 Skill、无收紧钩子。"""

    def _make_agent(self) -> LlmAgent:
        fake_model = MagicMock()
        with patch.object(qmt, "get_llm_model", return_value=fake_model):
            return qmt.create_question_maintain_agent()

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
    """题目维护 Agent 只经 FunctionTool 调 src/ingestion 门面，严禁直连存储。

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

    def test_no_store_import(self) -> None:
        mods = self._imported_modules(Path(qmt.__file__))
        violations = [
            m for m in mods if m == "src.store" or m.startswith("src.store.")
        ]
        assert violations == [], f"question_maintain.py 违反分层铁律: {violations}"
