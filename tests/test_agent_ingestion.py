# tests/test_agent_ingestion.py
"""摄入侧「结构识别」子 Agent 的行为/配置测试（mock LLM，不计费）。

覆盖：
- create_structure_recognition_agent()  工厂结构（name/instruction/tools/skill_repository/钩子）
- skill 仓库能成功加载 question-organize/SKILL.md，frontmatter name 与目录名一致
- before_agent_callback 把 skill 工具面收紧为 knowledge_only
- 所有用例只用本地文件 + mock LLM，无网络 / 计费调用（skill 仓库扫描的是 src/agent/skills/ 本地目录）
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents.core import get_skill_processor_parameters
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.skills import SkillToolSet

from src.agent.ingestion import structure_recognition as sr
from src.agent.ingestion.prompts import STRUCTURE_RECOGNITION_INSTRUCTION

_SKILL_DIR = sr.SKILLS_ROOT / "question-organize"


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
# Skill 仓库加载
# ═══════════════════════════════════════════════════════════════════════════════


class TestSkillRepository:
    """question-organize Skill 能从本地 src/agent/skills/ 仓库成功加载。"""

    def _make_repo(self):
        _, repo = sr.create_skill_tool_set()
        return repo

    def test_repo_lists_question_organize(self) -> None:
        repo = self._make_repo()
        assert "question-organize" in repo.skill_list()

    def test_load_question_organize_body(self) -> None:
        """load 后能取到 SKILL.md 正文与描述（既证明仓库就绪，又是纯指令 Skill 可用的前提）。"""
        repo = self._make_repo()
        skill = repo.get("question-organize")
        assert skill.summary.name == "question-organize"
        assert skill.summary.description
        assert "题目" in skill.body and "答案" in skill.body and "解析" in skill.body

    def test_frontmatter_name_matches_dir_name(self) -> None:
        """frontmatter name 必须与目录名一致（tRPC skill 仓库以 frontmatter name 注册，不一致即加载失败）。
        直接解析 SKILL.md 的 YAML frontmatter，同时与 repo.path() 命中的目录名比对。"""
        raw = (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(raw.split("---", 2)[1])
        assert frontmatter["name"] == "question-organize"
        assert frontmatter["description"]

        repo = self._make_repo()
        repo_dir = Path(repo.path("question-organize")).name
        assert repo_dir == frontmatter["name"] == _SKILL_DIR.name

    def test_skill_has_no_scripts(self) -> None:
        """question-organize 是纯指令 Skill：无 scripts/，验证 knowledge_only 收紧不会阉割任何可执行能力。"""
        assert not (_SKILL_DIR / "scripts").exists()


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


# ═══════════════════════════════════════════════════════════════════════════════
# Skill 工具集（不依赖 agent 构造）
# ═══════════════════════════════════════════════════════════════════════════════


class TestSkillToolSet:
    """create_skill_tool_set() 直接可用（不触发 LLM 构造，供测试与 leader 复用）。"""

    def test_toolset_and_repo_pair(self) -> None:
        tool_set, repo = sr.create_skill_tool_set()
        assert isinstance(tool_set, SkillToolSet)
        assert tool_set._repository is repo

    def test_skill_root_is_local_dir(self) -> None:
        """skill 路径必须是本地目录（不是 URL），保证加载不吃网络。"""
        assert Path(sr.SKILLS_ROOT).is_dir()
        assert "://" not in str(sr.SKILLS_ROOT)

    def test_toolset_declares_skill_load(self) -> None:
        tool_set, _ = sr.create_skill_tool_set()
        assert tool_set._load_tool.name == "skill_load"

    def test_repo_has_index(self) -> None:
        _, repo = sr.create_skill_tool_set()
        assert repo.summaries