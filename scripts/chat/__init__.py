# scripts/chat/__init__.py
# chat 包：Team Leader 对话调试入口（命令行模拟 QQ）。
#
# 模块分工：
#   app.py     REPL 主循环 + 参数解析 + 事件流喂给（入口函数 run()）
#   prompt.py  prompt_toolkit 多行输入（粘贴长题目）
#   render.py  rich 事件流富渲染（ChatRenderer，事件类型清单在其模块头）
#
# 启动方式：``uv run python scripts/cli.py chat`` 或 ``uv run python -m scripts.chat``。
# 设计文档：docs/scripts/chat/README.md。
