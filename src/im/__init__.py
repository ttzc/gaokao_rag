# src/im/__init__.py
# IM（QQ）接入层：trpc-claw 网关 + TeamAgent 装配（方式 A）。
# 实现见 claw_app.py；设计见 docs/im/README.md；启动入口 scripts/im_server.py。

from src.im.claw_app import DEFAULT_CONFIG_PATH, GaokaoClaw, create_claw_app

__all__ = ["GaokaoClaw", "create_claw_app", "DEFAULT_CONFIG_PATH"]
