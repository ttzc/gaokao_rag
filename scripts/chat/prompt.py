# scripts/chat/prompt.py
# chat 包的输入层：prompt_toolkit 多行 PromptSession（粘贴长题目一次成型）。
#
# 键位约定（multiline=True 下默认 Enter 变插入换行，须显式反转语义）：
#   - Enter              → 提交整段（validate_and_handle）
#   - Alt+Enter / Ctrl+J → 手动换行（Windows 终端 Alt+Enter 常被吞，Ctrl+J 兜底）
#   - 括号粘贴（bracketed paste）→ 多行原文整体入缓冲，换行保留，不触发提交
#
# 终端限制：Git Bash（mintty）伪终端下 prompt_toolkit 拿不到 Win32 控制台
# 缓冲区，构造即抛 NoConsoleScreenBufferError——由 app.py 捕获后降级单行
# input()，本模块只负责正常路径。
#
# 设计见 docs/scripts/chat/input.md。

from __future__ import annotations

from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

from src.config import config

# 输入行底部提示（bottom_toolbar），说明多行输入键位
INPUT_HINT = " Enter 发送 · Alt+Enter / Ctrl+J 换行 · 直接粘贴多行题目（换行保留） · exit 退出 "


def build_prompt_session() -> PromptSession[str]:
    """构造支持多行的输入会话。

    历史：FileHistory 落在 ``{store.data_dir}/chat_history.txt``（跨进程可 ↑↓
    翻旧输入；data/ 已 gitignore，与业务库同生命周期，可随 data/ 一起清理）。

    Raises:
        Exception: 伪终端（如 Git Bash/mintty）无 Win32 控制台缓冲区时由
            prompt_toolkit 抛出——调用方（app.py）负责捕获并降级 input()。
    """
    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event) -> None:  # noqa: ANN001 — prompt_toolkit 事件对象
        event.app.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _newline_alt_enter(event) -> None:  # noqa: ANN001
        event.app.current_buffer.newline()

    @bindings.add("c-j")
    def _newline_ctrl_j(event) -> None:  # noqa: ANN001
        event.app.current_buffer.newline()

    project_root = Path(__file__).resolve().parent.parent.parent
    history_dir = project_root / config.store.data_dir
    history_dir.mkdir(parents=True, exist_ok=True)

    return PromptSession(
        message="你（QQ）: ",
        multiline=True,
        key_bindings=bindings,
        history=FileHistory(history_dir / "chat_history.txt"),
        bottom_toolbar=lambda: INPUT_HINT,
    )
