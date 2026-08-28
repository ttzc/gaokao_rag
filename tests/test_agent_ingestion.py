# tests/test_agent_ingestion.py
"""摄入侧「结构识别」子 Agent 的行为/配置测试（mock LLM，不计费）。

覆盖：
- create_structure_recognition_agent()  工厂结构（name/instruction/tools/skill_repository/钩子/白名单接线）
- before_agent_callback 把 skill 工具面收紧为 knowledge_only
- 共享 skill 基础设施（仓库构造 / SKILL.md 加载 / 白名单机制本体）在 tests/test_agent_skills.py
- 所有用例只用本地文件 + mock LLM，无网络 / 计费调用（skill 仓库扫描的是 src/agent/skills/ 本地目录）
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents.core import get_skill_processor_parameters
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.skills import SkillToolSet

from src.agent.ingestion import structure_recognition as sr
from src.agent.ingestion.prompts import STRUCTURE_RECOGNITION_INSTRUCTION


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 工厂
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateStructureRecognitionAgent:
    """create_structure_recognition_agent() 工厂产出正确的 LlmAgent。"""

    def _make_agent(self) -> LlmAgent:
        """构造被测 agent，mock 掉 LLM 工厂（不读取真实 config/.env）。"""
        fake_model = MagicMock()
        with patch.object(sr, "get_llm_model", return_value=fake_model):
            return sr.create_structure_recognition_agent()

    def test_name(self) -> None:
        agent = self._make_agent()
        assert agent.name == "structure_recognition"

    def test_description(self) -> None:
        agent = self._make_agent()
        assert agent.description

    def test_instruction_loaded_from_prompts(self) -> None:
        """instruction 来自 prompts.py 常量模块（长 prompt 抽独立文件）。"""
        agent = self._make_agent()
        assert agent.instruction == STRUCTURE_RECOGNITION_INSTRUCTION
        assert "question-organize" in agent.instruction
        assert "lecture_segments" in agent.instruction
        assert "pending_questions" in agent.instruction
        # 来源链路贯通（2026-08-28）：source_hint 以「来源」行随条目下传，不被三段收敛误伤
        assert "来源" in agent.instruction
        assert "source_hint" in agent.instruction

    def test_tools_contains_skill_toolset(self) -> None:
        agent = self._make_agent()
        assert len(agent.tools) == 1
        assert isinstance(agent.tools[0], SkillToolSet)

    def test_skill_repository_attached(self) -> None:
        """skill_repository 与 SkillToolSet 配套挂载（缺一 skill_load 不可用）。"""
        agent = self._make_agent()
        assert agent.skill_repository is not None
        # 与 tools 里的 toolset 复用同一个仓库
        tool_set = agent.tools[0]
        assert tool_set._repository is agent.skill_repository

    def test_repo_bakes_agent_allowlist(self) -> None:
        """工厂把 ALLOWED_SKILLS 传给了共享构造：本 agent 仓库只认白名单内 skill。
        （白名单机制本身的过滤/报错行为见 tests/test_agent_skills.py，此处只测接线。）"""
        agent = self._make_agent()
        assert agent.skill_repository.skill_list() == list(sr.ALLOWED_SKILLS)

    def test_before_agent_callback_attached(self) -> None:
        """knowledge_only 收紧钩子挂在 before_agent_callback（agent_context 创建后、请求前调用）。"""
        agent = self._make_agent()
        assert callable(agent.before_agent_callback)
        assert agent.before_agent_callback == sr._configure_skill_tool_profile

    def test_model_from_llm_factory(self) -> None:
        """模型走 src/api/llm.py 工厂，不重复造模型。"""
        fake_model = MagicMock()
        with patch.object(sr, "get_llm_model", return_value=fake_model) as m:
            agent = sr.create_structure_recognition_agent()
        m.assert_called_once_with()
        assert agent.model is fake_model


# ═══════════════════════════════════════════════════════════════════════════════
# skill 工具面收紧
# ═══════════════════════════════════════════════════════════════════════════════


class TestSkillToolProfile:
    """before_agent_callback 必须在请求前把 tool_profile 设为 knowledge_only。"""

    def test_skill_tool_profile_is_knowledge_only(self) -> None:
        """收紧为 knowledge_only 的理由见仓库模块 docstring：纯指令 Skill 无 scripts，full 徒增空转工具。"""
        assert sr.SKILL_TOOL_PROFILE == "knowledge_only"

    def test_callback_sets_knowledge_only_on_agent_context(self) -> None:
        agent_context = create_agent_context()
        invocation_ctx = SimpleNamespace(agent_context=agent_context)

        sr._configure_skill_tool_profile(invocation_ctx)

        params = get_skill_processor_parameters(agent_context)
        assert params["tool_profile"] == "knowledge_only"

    def test_callback_idempotent(self) -> None:
        """同一 agent_context 多次调用，参数保持 knowledge_only 不变。"""
        agent_context = create_agent_context()
        invocation_ctx = SimpleNamespace(agent_context=agent_context)

        sr._configure_skill_tool_profile(invocation_ctx)
        sr._configure_skill_tool_profile(invocation_ctx)

        params = get_skill_processor_parameters(agent_context)
        assert params["tool_profile"] == "knowledge_only"
