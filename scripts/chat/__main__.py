# scripts/chat/__main__.py
# 包直跑入口：``uv run python -m scripts.chat [选项]``
# （转发到 app.run()；统一 CLI 走 scripts/cli.py 的 chat 子命令，参数一致。）

from .app import run

if __name__ == "__main__":
    raise SystemExit(run(prog="python -m scripts.chat"))
