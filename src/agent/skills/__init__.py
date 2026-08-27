# src/agent/skills/__init__.py
# Agent Skill 定义包：每个子目录一个 Skill（含 SKILL.md 指令正文），由子 Agent 经 skill_load 按需注入。
# 当前 Skill：question-organize（把单个题目单元归一为题目 / 答案 / 解析三段，整篇切出的题目段与零散单题通用）。
#
# 共享 Skill 基础设施（本包 __init__ 承载）：所有 agent（摄入侧 / 查询侧 / leader）复用
# 同一个 skill 目录，SKILL.md 放一份即物理全员可达，故仓库 / ToolSet 构造在此上提复用。
#
# 两层收紧，各管一头：
#   - skill 名单（本模块）：create_skill_tool_set(allowed_skills=...) 白名单烘焙进仓库
#     实例，名单外不进 prompt、skill_load 直接报错——框架层硬约束，不依赖 prompt 自觉；
#   - 工具种类（各 agent 模块）：SkillProfileNames 控制暴露哪几个内置工具
#     （knowledge_only vs full），由 agent 在 before_agent_callback 里设 tool_profile。

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import CachedFsSkillRepository
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository

# skill 根目录：即本包目录 src/agent/skills/（仓库只认含 SKILL.md 的子目录，
# __init__.py / __pycache__ 不会被误当作 skill）
SKILLS_ROOT = Path(__file__).resolve().parent

__all__ = ["SKILLS_ROOT", "create_skill_tool_set"]


class _AllowlistedSkillRepository(CachedFsSkillRepository):
    """带 skill 白名单的缓存仓库（agent 级硬收紧）。

    框架基类 BaseSkillRepository 预留了 visibility_filter 槽位并在全链路执行：
      - summaries() 过滤 → skill_processor 注入系统提示词的清单里没有白名单外的
        skill，模型根本「看不见」；
      - path() / get() / skill_run_env() 校验 → skill_load 白名单外 skill 直接
        ValueError，即便模型幻觉出名字也「加载不了」。
    但 Fs/Cached 子类的构造签名没把该参数透传出来，只能在构造后补赋值——框架
    server/openclaw 的 _skill_loader 同样用子类覆写实现白名单，模式与官方先例一致。

    另覆 skill_list()：基类实现无视 filter、返回全部索引名，会从工具返回值泄漏
    白名单外 skill 的存在，故改为跟随（已过滤的）summaries。
    """

    def __init__(self, *roots: str, allowed_skills: Iterable[str], **kwargs) -> None:
        super().__init__(*roots, **kwargs)
        allowed = frozenset(allowed_skills)
        # 基类 __init__ 有此官方槽位，仅 FS 子类未透传，构造后补赋值是唯一入口
        self._visibility_filter = lambda s: s.name in allowed  # noqa: E731

    def skill_list(self, mode: str = "all") -> list[str]:
        return sorted(summary.name for summary in self.summaries())


def create_skill_tool_set(
    allowed_skills: Iterable[str] | None = None,
) -> tuple[SkillToolSet, BaseSkillRepository]:
    """构建共享的 Skill ToolSet + Repository（指向本包 src/agent/skills/）。

    Args:
        allowed_skills: 本 agent 的 skill 白名单（SKILL.md frontmatter name）。
            白名单外的 skill 不进 prompt、skill_load 亦报错——框架层硬约束，
            不依赖系统提示词自觉。**各子 Agent 工厂应显式传入**；None 表示全量
            可见，仅留给确需浏览全部 skill 的 leader。

    Returns:
        (tool_set, repository)：tool_set 挂进 LlmAgent.tools，repository 挂进
        LlmAgent.skill_repository（两者配套，缺一 skill 工具不可用）。
        供各子 Agent 工厂与 TeamAgent leader 构造复用。

    参数取舍：
      - enable_hot_reload=False：skills 目录是仓库内的静态文件，不需要后台热加载
        扫描（官方示例默认开启，测试环境下热加载线程徒增不确定性）。
      - use_cached_repository=True：与官方示例一致，用缓存型仓库索引 SKILL.md。
    """
    if allowed_skills is None:
        repository: BaseSkillRepository = create_default_skill_repository(
            str(SKILLS_ROOT),
            enable_hot_reload=False,
            use_cached_repository=True,
        )
    else:
        repository = _AllowlistedSkillRepository(
            str(SKILLS_ROOT),
            allowed_skills=allowed_skills,
            enable_hot_reload=False,
        )
    tool_set = SkillToolSet(
        repository=repository,
        run_tool_kwargs={"save_as_artifacts": True, "omit_inline_content": False},
    )
    return tool_set, repository
