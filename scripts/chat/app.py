# scripts/chat/app.py
# 对话 CLI 主模块（开发调试入口）：从命令行模拟 QQ 与 Team Leader 一问一答，
# 用于验证「口述题目 → 归一 → 回显确认 → 入库」端到端闭环
# （正式 trpc-claw QQ 入口落地前以此代替，落地后也可作调试通道）。
# 总览见 docs/scripts/chat/README.md，渲染细节见 docs/scripts/chat/rendering.md。
#
# 启动方式（参数完全一致，推荐前者）：
#   uv run gaokao chat [选项]                    # console script（uv sync 后，scripts.cli 转发至此）
#   uv run python -m scripts.chat [选项]         # 包直跑（__main__.py 转发至此）
#
# 要点：
#   - 真实调用：DeepSeek + Embedding + Chroma/SQLite 写库全走真链路（计费），
#     因此不进 pytest——本包无单测，逻辑验证靠手动跑。
#   - 多轮会话：进程内 InMemorySessionService 保持上下文（重启即清空），
#     固定 user_id / session_id 模拟 QQ 单用户会话。
#   - 事件流打印：rich 富渲染（render.py ChatRenderer）——按事件类型分发：
#     正文按 author 着色流式、思考流 dim 斜体、工具调用/结果单行
#     （委派 🤝 / 技能 📚 / 检索 🔍 / 写库 💾 图标 + 耗时 + ✔/✖）、state_delta
#     露出键名体量、错误分致命/可恢复两级、轮末 token 汇总。事件类型清单与
#     实测坑位见 render.py 模块头。--no-think 隐藏思考流，--full 不截断。
#   - 异常兜底：单轮 run 抛错只打印不退出，下一轮可继续（框架常规错误走
#     error_code 事件渲染，这里兜的是 RunLimit 等 raise 型异常）。
#   - 多行输入：prompt.py（prompt_toolkit PromptSession，multiline），
#     伪终端自动回退单行 input()。

from __future__ import annotations

import argparse
import asyncio
import sys
from io import TextIOWrapper

from rich.console import Console
from rich.panel import Panel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from src.agent.leader import create_gaokao_leader

from .prompt import INPUT_HINT, build_prompt_session
from .render import PREVIEW_CHARS, ChatRenderer

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

APP_NAME = "gaokao_rag_chat"

# 固定单一用户 / 会话：模拟 QQ 单用户（MVP 决策 8），追问「第二道呢」可接续
USER_ID = "qq_dev_user"
SESSION_ID = "qq_dev_session"

# ═══════════════════════════════════════════════════════════════════════════════
# 事件流喂给（渲染逻辑在 render.py，本文件只管分发入口）
# ═══════════════════════════════════════════════════════════════════════════════


async def ask_once(runner: Runner, text: str, renderer: ChatRenderer) -> None:
    """发送一条用户消息，把事件流喂给渲染器，直到本轮结束。

    Args:
        runner: 已构造好的 Runner（持有 Leader 与会话服务）。
        text: 用户输入原文。
        renderer: 本轮的富渲染器（feed 分发各类事件，finish 打汇总行）。
    """
    message = Content(parts=[Part.from_text(text=text)])
    renderer.start_turn()
    try:
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=SESSION_ID,
            new_message=message,
        ):
            renderer.feed(event)
    except Exception as exc:  # noqa: BLE001 — 单轮兜底：打印异常但不退出
        renderer.on_run_exception(exc)
    renderer.finish()


# ═══════════════════════════════════════════════════════════════════════════════
# 参数解析 + REPL 主循环
# ═══════════════════════════════════════════════════════════════════════════════


def _build_parser(prog: str) -> argparse.ArgumentParser:
    """构造 chat 自己的参数解析器（渲染开关，不影响 REPL 行为）。

    Args:
        prog: 展示在 --help 首行的程序名——经 scripts/cli.py（gaokao 命令）
            转发时传 ``"gaokao chat"`` / ``"cli.py chat"``（按启动形态取名），
            包直跑时传 ``"python -m scripts.chat"``。
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Gaokao RAG 对话调试入口（模拟 QQ，rich 富渲染事件流；真实调用计费）",
    )
    parser.add_argument(
        "--no-think",
        action="store_true",
        help="不渲染模型思考流（DeepSeek reasoning_content，part.thought=True）",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="工具参数/返回值不截断（默认单行 300 字截断）",
    )
    return parser


async def _repl(*, show_thought: bool, truncate: int) -> int:
    """构造 Runner 并进入一问一答循环。

    Args:
        show_thought: 是否渲染思考流（``--no-think`` 取反）。
        truncate: 工具参数/返回截断长度；0 = 不截断（``--full``）。

    Returns:
        退出码：0=正常结束；1=Leader 构造失败（配置缺失）。
    """
    # Git Bash / Windows 终端中文输出兜底（不依赖 PYTHONIOENCODING 环境变量）。
    # 必须先于 Console 构造：rich 的 Windows legacy 渲染器按 file.encoding
    # 写文本，GBK 环境下 emoji 前缀直接抛 UnicodeEncodeError。
    if isinstance(sys.stdout, TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    # markup=False：LLM 原文里的 [xxx] 会被 rich 当样式标签解析炸 MarkupError，
    # 所有着色走 Text(style=...)；highlight=False：关数字/路径自动着色防干扰。
    console = Console(markup=False, highlight=False)
    renderer = ChatRenderer(console, show_thought=show_thought, truncate=truncate)

    console.print(
        Panel(
            "输入题目相关内容开始；长题目直接粘贴（多行保留），Enter 发送。\n"
            "多轮上下文仅存活于本进程（InMemorySessionService，重启即清空）。\n"
            "渲染开关：--no-think 隐藏思考流 · --full 工具返回不截断",
            title="Gaokao RAG — Team Leader 对话调试入口（模拟 QQ）",
            border_style="cyan",
        )
    )

    # 构造 Leader 会读取 config.toml + .env（get_llm_model → src.config），
    # 环境缺失时抛错——在入口处友好提示并退出，而不是抛裸栈。
    try:
        leader = create_gaokao_leader()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[启动失败] 构造 Leader 异常：{type(exc).__name__}: {exc}")
        console.print("[启动失败] 请确认 .env 已配置 api_key / base_url / model（见 docs/scripts/chat/README.md 前置条件）。")
        return 1

    runner = Runner(
        app_name=APP_NAME,
        agent=leader,
        session_service=InMemorySessionService(),
    )

    # 伪终端下多行输入构造失败（prompt.py 注释）——降级单行 input() 不阻断调试。
    try:
        session = build_prompt_session()
        console.print(f"[cli] 按键提示:{INPUT_HINT}")
    except Exception as exc:  # noqa: BLE001 — 终端环境兜底
        console.print(f"[cli] 当前终端不支持多行输入（{type(exc).__name__}），回退单行模式。")
        console.print("[cli] 需要粘贴长题目请在 cmd / PowerShell / Windows Terminal 下运行本脚本。")
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
            await ask_once(runner, text, renderer)
    except KeyboardInterrupt:
        # Ctrl+C 退出（含生成中途打断）：走 finally 正常关 Runner
        console.print("[cli] 键盘中断，退出。")
    finally:
        await runner.close()

    console.print("[cli] 再见。")
    return 0


def run(argv: list[str] | None = None, *, prog: str = "python -m scripts.chat") -> int:
    """chat 的同步入口：解析渲染开关 → asyncio.run 进 REPL。

    供两条路径复用：``gaokao chat``（实现在 scripts/cli.py，chat 早路由转发，
    prog 按启动形态传入）与 ``python -m scripts.chat``（__main__.py）。

    Args:
        argv: 参数列表，``None`` 时取 ``sys.argv[1:]``。
        prog: --help 展示的程序名。

    Returns:
        退出码（语义见 :func:`_repl`）；argparse 自身错误按标准退出码 2。
    """
    args = _build_parser(prog).parse_args(argv)
    return asyncio.run(
        _repl(show_thought=not args.no_think, truncate=0 if args.full else PREVIEW_CHARS)
    )
