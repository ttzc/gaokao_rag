# scripts/im_server.py
# IM 网关启动入口（trpc-claw QQ 通道，方式 A，设计见 docs/im/README.md）。
#
# 为什么不用 `trpc_agent_cmd openclaw run`：该命令硬编码实例化默认
# ClawApplication（_cli.py:56），不会加载 GaokaoClaw 子类，TeamAgent 接入无效。
# 本脚本复刻 _cli.py:50-62 的启动逻辑，仅把默认类换成 GaokaoClaw：
#   - 有启用通道 → run_gateway()（QQ 网关长连接）
#   - 无启用通道 → run_cli_fallback()（.env 缺 QQ 密钥时可本地无网调试）
#
# 要点：真实连接 QQ 官方 API（走 .env 的 QQ_APP_ID/QQ_APP_SECRET），
# 本文件不进 pytest——启动逻辑验证靠手动跑（沙箱单聊联调）。

from __future__ import annotations

import asyncio
from pathlib import Path

from src.im import create_claw_app


def main(workspace: str | None = None, config: str | None = None) -> None:
    """启动 IM 网关（复刻 _cli.py:50-62，加载 GaokaoClaw 子类）。

    Args:
        workspace: 工作目录，None 时用配置默认值
        config: openclaw 配置路径，None 时用项目内 src/im/openclaw.yaml
    """
    ws = Path(workspace).expanduser().resolve() if workspace else None
    cfg = Path(config).expanduser().resolve() if config else None

    async def _run() -> None:
        gateway = create_claw_app(workspace=ws, config_path=cfg)
        # 对照 _cli.py:57-60：有启用通道走网关，否则 CLI 回退
        if not gateway.channels.enabled_channels:
            await gateway.run_cli_fallback()
            return
        await gateway.run_gateway()

    asyncio.run(_run())


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Gaokao RAG IM 网关（trpc-claw QQ 通道）")
    ap.add_argument("-c", "--config", default=None,
                    help="openclaw 配置文件路径（默认 src/im/openclaw.yaml）")
    ap.add_argument("-w", "--workspace", default=None, help="工作目录（默认取配置值）")
    args = ap.parse_args()
    main(workspace=args.workspace, config=args.config)
