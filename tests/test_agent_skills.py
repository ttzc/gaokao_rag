# tests/test_agent_skills.py
"""共享 Skill 基础设施测试（src/agent/skills/，mock -free、不计费）。

覆盖：
- create_skill_tool_set() 配套构造（toolset/repository）+ SKILLS_ROOT 为本地目录
- question-organize Skill 能从本地仓库成功加载（frontmatter name 与目录名一致、纯指令无 scripts）
- error-organize Skill 文件级用例（存在 / frontmatter / 红线词 / 四段前缀逐字 + 与写门面前缀同源）
- 待挂载负向断言：全项目没有任何 Agent 的 ALLOWED_SKILLS 含 error-organize
- allowed_skills 白名单硬约束：名单外不进 summaries/skill_list（模型不可见），get() 报 ValueError（加载不了）
- 所有用例只扫描 src/agent/skills/ 本地目录，无网络 / 计费调用
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills.tools import CopySkillStager

from src.agent.skills import SKILLS_ROOT, create_skill_tool_set
from src.ingestion.error import _SUMMARY_SECTIONS

_SKILL_DIR = SKILLS_ROOT / "question-organize"
_ERROR_SKILL_DIR = SKILLS_ROOT / "error-organize"

# error-organize 的四段前缀（与写门面 _SUMMARY_SECTIONS 刻意同一套标签）
_ERROR_PREFIXES = ("错因类型：", "错因：", "知识点缺口：", "改进建议：")


def _agent_allowlists() -> dict[str, tuple[str, ...]]:
    """AST 扫描 src/agent/ 下**模块级** ALLOWED_SKILLS 字面量定义。

    不 import agent 模块：本函数只为读常量，AST 零副作用、不触发 .env/config 读取，
    也不受 agent 工厂是否可构造影响。key 用相对 src/agent/ 的 posix 路径（跨平台稳定）。
    """
    root = SKILLS_ROOT.parent
    found: dict[str, tuple[str, ...]] = {}
    for py in sorted(root.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
                value = node.value
            else:
                continue
            if "ALLOWED_SKILLS" not in names or not isinstance(value, ast.Tuple):
                continue
            found[py.relative_to(root).as_posix()] = tuple(
                elt.value
                for elt in value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            )
    return found


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
# error-organize Skill 文件级用例（本批只落文件本体，执行方 Agent 下一批接线）
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorOrganizeSkill:
    """SKILL.md 本体契约：存在性 / frontmatter / 红线词 / 四段前缀（格式漂移防护）。"""

    def _text(self) -> str:
        return (_ERROR_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_dir_and_file_exist(self) -> None:
        assert _ERROR_SKILL_DIR.is_dir()
        assert (_ERROR_SKILL_DIR / "SKILL.md").is_file()

    def test_repo_lists_and_loads_error_organize(self) -> None:
        """全量视角能列出并加载：frontmatter name 与目录名一致是仓库注册前提。"""
        _, repo = create_skill_tool_set()
        assert "error-organize" in repo.skill_list()
        skill = repo.get("error-organize")
        assert skill.summary.name == "error-organize"
        assert skill.summary.description
        assert "错因" in skill.body

    def test_frontmatter_name_matches_dir_name(self) -> None:
        raw = self._text()
        frontmatter = yaml.safe_load(raw.split("---", 2)[1])
        assert frontmatter["name"] == "error-organize"
        assert frontmatter["description"]

        _, repo = create_skill_tool_set()
        repo_dir = Path(repo.path("error-organize")).name
        assert repo_dir == frontmatter["name"] == _ERROR_SKILL_DIR.name

    def test_skill_has_no_scripts(self) -> None:
        """纯 prompt 模块：无 scripts/，不挂工具、不查库。"""
        assert not (_ERROR_SKILL_DIR / "scripts").exists()

    def test_redline_phrases_present(self) -> None:
        """红线词钉住命门（防将来改写 prompt 时把「不脑补 / 宁可稀疏」改丢）。"""
        text = self._text()
        assert "不脑补" in text
        assert "宁可稀疏" in text

    def test_section_prefixes_verbatim(self) -> None:
        """四段前缀逐字固定——分节文本靠标签可靠抽取，前缀漂移即契约破裂。"""
        text = self._text()
        for prefix in _ERROR_PREFIXES:
            assert prefix in text, f"SKILL.md 缺少分节前缀 {prefix!r}"

    def test_section_prefixes_match_write_facade(self) -> None:
        """SKILL.md 前缀与写门面 _SUMMARY_SECTIONS 同一套标签：门面改前缀而 prompt
        没跟上，Agent 产出的分节文本与落库后派生的 embedding 文本即脱钩。"""
        facade_prefixes = {prefix for _, prefix in _SUMMARY_SECTIONS}
        assert facade_prefixes == set(_ERROR_PREFIXES)  # 门面侧钉死四段
        text = self._text()
        assert all(p in text for p in facade_prefixes)


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


# ═══════════════════════════════════════════════════════════════════════════════
# error-organize 待挂载（本批负向断言：钉住「文件已落、Agent 未接线」）
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorOrganizePendingMount:
    """本批只落 SKILL.md 本体，执行方（错题管理 Agent）下一批才落地。

    拆批安全的前提是白名单机制：全项目没有任何 Agent 的 ALLOWED_SKILLS 含
    error-organize → 它不进任何 prompt 清单、`skill_load` 亦报错，文件落库零风险。
    错题管理 Agent 落地时把本类改成正向断言（该 Agent 白名单 == ("error-organize",)）。
    """

    def test_scan_finds_allowlists_and_structure_recognition_unchanged(self) -> None:
        """先钉扫描本身有效（防逻辑失效让下面的负向断言恒真），再钉现状：
        全项目仅 structure_recognition 定义白名单，且仍只有 question-organize 一项。"""
        found = _agent_allowlists()
        assert found, "src/agent/ 下应能扫到 ALLOWED_SKILLS 定义（扫描逻辑失效？）"
        assert found == {"ingestion/structure_recognition.py": ("question-organize",)}

    def test_no_agent_allowlist_contains_error_organize(self) -> None:
        offenders = {
            module: skills
            for module, skills in _agent_allowlists().items()
            if "error-organize" in skills
        }
        assert offenders == {}, f"error-organize 已被挂进白名单（本批应为待挂载）：{offenders}"

    def test_error_organize_hidden_under_existing_agent_allowlist(self) -> None:
        """机制验证（不只是字面量不含）：拿现有 Agent 的白名单建仓库，
        磁盘上已索引的 error-organize 依然不可见、加载报错——拆批安全依赖的正是这条。"""
        allowed = _agent_allowlists()["ingestion/structure_recognition.py"]
        _, repo = create_skill_tool_set(allowed)
        assert repo.skill_list() == ["question-organize"]
        assert "error-organize" not in repo.skill_list()
        with pytest.raises(ValueError, match="not found"):
            repo.get("error-organize")
