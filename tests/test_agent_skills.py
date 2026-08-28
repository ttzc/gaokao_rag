# tests/test_agent_skills.py
"""共享 Skill 基础设施测试（src/agent/skills/，mock -free、不计费）。

覆盖：
- create_skill_tool_set() 配套构造（toolset/repository）+ SKILLS_ROOT 为本地目录
- question-organize Skill 能从本地仓库成功加载（frontmatter name 与目录名一致、纯指令无 scripts）
- allowed_skills 白名单硬约束：名单外不进 summaries/skill_list（模型不可见），get() 报 ValueError（加载不了）
- 所有用例只扫描 src/agent/skills/ 本地目录，无网络 / 计费调用
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills.tools import CopySkillStager

from src.agent.skills import SKILLS_ROOT, create_skill_tool_set

_SKILL_DIR = SKILLS_ROOT / "question-organize"


# ═══════════════════════════════════════════════════════════════════════════════
# 配套构造
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateSkillToolSet:
    """create_skill_tool_set() 直接可用（不触发 LLM 构造，包级共享，供各 agent 与 leader 复用）。"""

    def test_toolset_and_repo_pair(self) -> None:
        tool_set, repo = create_skill_tool_set()
        assert isinstance(tool_set, SkillToolSet)
        assert tool_set._repository is repo

    def test_skill_root_is_local_dir(self) -> None:
        """skill 路径必须是本地目录（不是 URL），保证加载不吃网络。"""
        assert Path(SKILLS_ROOT).is_dir()
        assert "://" not in str(SKILLS_ROOT)

    def test_toolset_declares_skill_load(self) -> None:
        tool_set, _ = create_skill_tool_set()
        assert tool_set._load_tool.name == "skill_load"

    def test_repo_has_index(self) -> None:
        _, repo = create_skill_tool_set()
        assert repo.summaries

    def test_stager_is_copy_for_windows(self) -> None:
        """stager 必须是 CopySkillStager——框架默认 LinkSkillStager 走 os.symlink，
        Windows 无符号链接权限时 skill_load 报 WinError 1314。断言防止未来改回。"""
        tool_set, _ = create_skill_tool_set()
        assert isinstance(tool_set._skill_stager, CopySkillStager)
        assert tool_set._skill_stager._stage_mode == "copy"


# ═══════════════════════════════════════════════════════════════════════════════
# Skill 仓库加载
# ═══════════════════════════════════════════════════════════════════════════════


class TestSkillRepository:
    """question-organize Skill 能从本地 src/agent/skills/ 仓库成功加载。"""

    def _make_repo(self):
        _, repo = create_skill_tool_set()
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
# skill 白名单（agent 级硬约束）
# ═══════════════════════════════════════════════════════════════════════════════


class TestSkillAllowlist:
    """allowed_skills 是烘焙进仓库实例的框架级硬约束，不依赖 prompt 自觉。

    仓库真实根目录为 src/agent/skills/（已索引 question-organize），
    用「白名单不含它」即可证明是对**已索引 skill 的过滤**，而非目录里没有。
    """

    def test_none_means_all_visible(self) -> None:
        """不传白名单（leader 全量视角）：已索引 skill 可见可加载。"""
        _, repo = create_skill_tool_set()
        assert "question-organize" in repo.skill_list()
        assert repo.get("question-organize") is not None

    def test_allowlisted_skill_visible_and_loadable(self) -> None:
        _, repo = create_skill_tool_set(["question-organize"])
        assert repo.skill_list() == ["question-organize"]
        assert [s.name for s in repo.summaries()] == ["question-organize"]
        assert repo.get("question-organize").summary.name == "question-organize"

    def test_indexed_skill_outside_allowlist_is_hidden(self) -> None:
        """question-organize 在磁盘上已索引，但白名单外 → 清单不可见 + get 报 ValueError。"""
        _, repo = create_skill_tool_set(["ghost-skill"])
        assert repo.skill_list() == []
        assert repo.summaries() == []
        with pytest.raises(ValueError, match="not found"):
            repo.get("question-organize")

    def test_empty_allowlist_hides_everything(self) -> None:
        """空列表 ≠ None：什么都不给（防漏传参数时静默全开的兜底语义验证）。"""
        _, repo = create_skill_tool_set([])
        assert repo.skill_list() == []
        with pytest.raises(ValueError, match="not found"):
            repo.get("question-organize")

    def test_allowlist_entry_not_on_disk_is_harmless(self) -> None:
        """白名单里有、磁盘上没有的名字：不报错，只是不出现。"""
        _, repo = create_skill_tool_set(["question-organize", "ghost-skill"])
        assert repo.skill_list() == ["question-organize"]
