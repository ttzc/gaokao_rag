# src/im/claw_app.py
# trpc-claw QQ 主入口的 TeamAgent 接入（方式 A，设计见 docs/im/README.md）。
#
# 思路：ClawApplication.__init__ 默认装配（bus/channels/model/storage/session/
# memory/heartbeat）全部保留，只把主 agent 从默认 LlmAgent 换成 gaokao
# TeamAgent，并按 claw.py:171-183 的原逻辑重建两个 Runner。
# 不覆写其他环节、不碰 trpc_agent_sdk 包内文件（MVP 免补丁决策，
# 长答案分片等 _qq.py 适配器改造属 V1.1+）。

from __future__ import annotations

from pathlib import Path
from typing import Optional, cast

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.openclaw.claw import ClawApplication

from src.agent.leader import create_gaokao_leader

# 项目内默认配置（channels.qq + ${VAR} 桥接 .env），实测通过
DEFAULT_CONFIG_PATH = Path(__file__).with_name("openclaw.yaml")


class GaokaoClaw(ClawApplication):
    """TeamAgent 替换默认 LlmAgent 的 ClawApplication 子类（方式 A）。"""

    def __init__(
        self,
        workspace: Optional[Path] = None,
        config_path: Optional[Path] = None,
    ) -> None:
        super().__init__(workspace, config_path)  # 默认装配：bus/channels/model/storage/session/memory
        # 唯一替换点：主 agent 换成 gaokao TeamAgent
        self.agent = create_gaokao_leader()
        # 重建 runner（对照 claw.py:171-183，worker_runner 必须一并重建，
        # 否则后台任务仍指向旧 LlmAgent；TeamAgent 的 sub_agents 为空——
        # 成员挂在 members 字段——故 worker_agent 回退为 TeamAgent 本身）
        self.runner = Runner(
            app_name=self.config.runtime.app_name,
            agent=self.agent,
            session_service=self.session_service,
            memory_service=self.memory_service,
        )
        raw_worker = self.agent.sub_agents[0] if self.agent.sub_agents else self.agent
        worker_agent = cast(BaseAgent, raw_worker)
        self.worker_runner = Runner(
            app_name=f"{self.config.runtime.app_name}_worker",
            agent=worker_agent,
            session_service=self.session_service,
            memory_service=self.memory_service,
        )


def create_claw_app(
    workspace: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> GaokaoClaw:
    """装配 GaokaoClaw（QQ 主入口便捷工厂）。

    Args:
        workspace: 工作目录，None 时用配置默认值
        config_path: openclaw 配置路径，None 时用项目内 src/im/openclaw.yaml

    Returns:
        装配好的 GaokaoClaw 实例（agent 已是 gaokao TeamAgent，可直接
        await run_gateway() / run_cli_fallback()）
    """
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    return GaokaoClaw(workspace=workspace, config_path=config_path)
