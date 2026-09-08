# scripts/chat.py
# 对话 CLI（开发调试入口）：从命令行模拟 QQ 与 Team Leader 一问一答，
# 用于验证「口述题目 → 归一 → 回显确认 → 入库」端到端闭环
# （正式 trpc-claw QQ 入口落地前以此代替，落地后也可作调试通道）。
# 设计见 docs/scripts/chat.md。
#
# 要点：
#   - 真实调用：DeepSeek + Embedding + Chroma/SQLite 写库全走真链路（计费），
#     因此不进 pytest——本文件无单测，逻辑验证靠手动跑。
#   - 多轮会话：进程内 InMemorySessionService 保持上下文（重启即清空），
#     固定 user_id / session_id 模拟 QQ 单用户会话。
#   - 事件流打印：partial 文字流式打印（带 [author] 前缀），非 partial 事件
#     打印工具调用 / 返回（委派链 delegate_to_member、skill_load、ingest_question
#     均可见），便于观察多 Agent 协作。
#   - 异常兜底：单轮 run 抛错只打印不退出，下一轮可继续。
#   - 多行输入：prompt_toolkit PromptSession（multiline），粘贴长题目一次
#     成型（换行保留），Enter 发送、Alt+Enter / Ctrl+J 手动换行。

from __future__ import annotations

import asyncio
import sys
from io import TextIOWrapper
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from src.agent.leader import create_gaokao_leader
from src.config import config

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

APP_NAME = "gaokao_rag_chat"

# 固定单一用户 / 会话：模拟 QQ 单用户（MVP 决策 8），追问「第二道呢」可接续
USER_ID = "qq_dev_user"
SESSION_ID = "qq_dev_session"

# 工具调用参数 / 返回值的预览截断长度（task 全文可能很长，只看不截语义）
_PREVIEW_CHARS = 300

# 输入行底部提示（prompt_toolkit bottom_toolbar），说明多行输入键位
_INPUT_HINT = " Enter 发送 · Alt+Enter / Ctrl+J 换行 · 直接粘贴多行题目（换行保留） · exit 退出 "

# ═══════════════════════════════════════════════════════════════════════════════
# 事件流打印
# ═══════════════════════════════════════════════════════════════════════════════


def _preview(value: object, limit: int = _PREVIEW_CHARS) -> str:
    """把工具参数 / 返回值压成单行短文本，超长截断。"""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（共 {len(text)} 字，已截断）"


async def ask_once(runner: Runner, text: str) -> None:
    """发送一条用户消息，流式打印 Leader / 成员的事件，直到本轮结束。

    Args:
        runner: 已构造好的 Runner（持有 Leader 与会话服务）。
        text: 用户输入原文。

    打印约定：
        - partial 文字事件 → 流式打印正文，作者切换时另起一行打 ``[author]`` 前缀；
        - 非 partial 的 function_call / function_response → 独占一行打印，
          并把文字流打断（下一个文字分块会重新打作者前缀）。
    """
    message = Content(parts=[Part.from_text(text=text)])
    streaming_author: str | None = None

    try:
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=SESSION_ID,
            new_message=message,
        ):
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                # 工具调用/返回只在非 partial 事件打：partial 事件里的
                # function_call 是流式增量参数（args 不完整），打出来会刷屏
                # （对齐官方 examples/team 的打印约定）。
                if part.function_call and not event.partial:
                    streaming_author = None
                    call = part.function_call
                    print(f"\n[{event.author}] 🔧 {call.name} args={_preview(call.args)}")
                elif part.function_response and not event.partial:
                    streaming_author = None
                    resp = part.function_response
                    print(f"[{event.author}] ↩ {resp.name} response={_preview(resp.response)}")
                elif part.text and event.partial:
                    if streaming_author != event.author:
                        streaming_author = event.author
                        print(f"\n[{streaming_author}] ", end="", flush=True)
                    print(part.text, end="", flush=True)
        print()
    except Exception as exc:  # noqa: BLE001 — 单轮兜底：打印异常但不退出
        print(f"\n[错误] 本轮运行异常：{type(exc).__name__}: {exc}")
        print("[错误] 会话仍可用，可继续输入或 exit 退出。")


# ═══════════════════════════════════════════════════════════════════════════════
# 多行输入（prompt_toolkit）
# ═══════════════════════════════════════════════════════════════════════════════


def _make_prompt_session() -> PromptSession[str]:
    """构造支持多行的输入会话。

    键位约定（multiline=True 下默认 Enter 变插入换行，须显式反转语义）：
        - Enter            → 提交整段（validate_and_handle）
        - Alt+Enter / Ctrl+J → 手动换行（Windows 终端 Alt+Enter 常被吞，Ctrl+J 兜底）
        - 括号粘贴（bracketed paste）→ 多行原文整体入缓冲，换行保留，不触发提交

    历史：FileHistory 落在 ``{store.data_dir}/chat_history.txt``（跨进程可 ↑↓
    翻旧输入；data/ 已 gitignore，与业务库同生命周期，可随 data/ 一起清理）。
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

    project_root = Path(__file__).resolve().parent.parent
    history_dir = project_root / config.store.data_dir
    history_dir.mkdir(parents=True, exist_ok=True)

    return PromptSession(
        message="你（QQ）: ",
        multiline=True,
        key_bindings=bindings,
        history=FileHistory(history_dir / "chat_history.txt"),
        bottom_toolbar=lambda: _INPUT_HINT,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# REPL 主循环
# ═══════════════════════════════════════════════════════════════════════════════


async def main() -> None:
    """构造 Runner 并进入一问一答循环。"""
    # Git Bash / Windows 终端中文输出兜底（不依赖 PYTHONIOENCODING 环境变量）
    if isinstance(sys.stdout, TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("Gaokao RAG — Team Leader 对话调试入口（模拟 QQ）")
    print("输入题目相关内容开始；长题目直接粘贴（多行保留），Enter 发送。")
    print("多轮上下文仅存活于本进程（InMemorySessionService，重启即清空）。")
    print("=" * 60)

    # 构造 Leader 会读取 config.toml + .env（get_llm_model → src.config），
    # 环境缺失时抛错——在入口处友好提示并退出，而不是抛裸栈。
    try:
        leader = create_gaokao_leader()
    except Exception as exc:  # noqa: BLE001
        print(f"[启动失败] 构造 Leader 异常：{type(exc).__name__}: {exc}")
        print("[启动失败] 请确认 .env 已配置 api_key / base_url / model（见 docs/scripts/chat.md 前置条件）。")
        sys.exit(1)

    runner = Runner(
        app_name=APP_NAME,
        agent=leader,
        session_service=InMemorySessionService(),
    )

    # Git Bash（mintty）伪终端下 prompt_toolkit 拿不到 Win32 控制台缓冲区，
    # 构造即抛 NoConsoleScreenBufferError——此时降级为原单行 input()，
    # 不阻断调试入口（多行输入需在 cmd / PowerShell / Windows Terminal 下使用）。
    try:
        session: PromptSession[str] | None = _make_prompt_session()
        print(f"[cli] 按键提示:{_INPUT_HINT}")
    except Exception as exc:  # noqa: BLE001 — 终端环境兜底
        print(f"[cli] 当前终端不支持多行输入（{type(exc).__name__}），回退单行模式。")
        print("[cli] 需要粘贴长题目请在 cmd / PowerShell / Windows Terminal 下运行本脚本。")
        session = None

    try:
        while True:
            try:
                text = await session.prompt_async() if session else input("你（QQ）: ")
            except EOFError:
                print()
                break
            text = text.strip()
            if not text:
                continue
            if text.lower() in {"exit", "quit"}:
                break
            await ask_once(runner, text)
    except KeyboardInterrupt:
        # Ctrl+C 退出（含生成中途打断）：走 finally 正常关 Runner
        print("\n[cli] 键盘中断，退出。")
    finally:
        await runner.close()

    print("[cli] 再见。")


if __name__ == "__main__":
    asyncio.run(main())
