# scripts/chat/render.py
# chat 包的事件流富渲染层（rich）：把 Runner.run_async 的 Event 流按类型分发，
# 让调试者一眼分清「谁在说话 · 思考还是正文 · 调了哪个工具 · 成没成 · 多久 · 烧了多少 token」。
#
# 事件类型清单基于 tRPC-Agent 源码调研（2026-09-08）：Event 无 type 枚举，
# 靠字段组合判定；分发顺序对齐官方消费端范例
# server/ag_ui/_core/_event_translator.py——取消 → 致命错误 → 长任务 →
# 工具参数流（跳过）→ 工具进度流 → content parts（调用/结果/流式文字）→
# state_delta → usage 累加。
#
# 三个实测坑位（勿改）：
#   1. Event.get_text() 不剔除 thought part——DeepSeek reasoning_content 会混进
#      正文，必须逐 part 判 part.thought 分流（本文件 _stream_text）；
#   2. 同一终态事件可同时携带 text + state_delta + usage_metadata——各分支
#      允许叠加命中，不写排他 return（只有取消/致命错误提前终止分发）；
#   3. 流式正文后再来的非 partial 全文事件不能重打——按 (author, kind) 记
#      _streamed 去重；但若模型未流式（终态才有全文），走兜底整段打一次。
#
# Console 关掉 markup/highlight（app.py 构造时传 markup=False）：LLM 原文里
# 的 [xxx] 会被 rich 当样式标签解析炸 MarkupError，所有着色一律走 Text(style=...)。
#
# 本模块只服务 chat 包（调试入口），真实链路的展示逻辑不进 pytest。

from __future__ import annotations

import time
from typing import Any

from rich.console import Console
from rich.text import Text
from trpc_agent_sdk.events import AgentCancelledEvent, Event, LongRunningEvent

# ═══════════════════════════════════════════════════════════════════════════════
# 主题常量
# ═══════════════════════════════════════════════════════════════════════════════

# author 色板：Team Leader + 4 成员（名字对齐 src/agent/*/AGENT_NAME）；
# 新成员未登记时落 _DEFAULT_COLOR，不阻断渲染。
_AUTHOR_COLORS: dict[str, str] = {
    "gaokao_leader": "cyan",
    "search": "green",
    "structure_recognition": "magenta",
    "storage_decision": "yellow",
    "question_maintain": "blue",
    "user": "bright_black",
}
_DEFAULT_COLOR = "white"

# 工具图标：按名字家族给语义图标（委派/技能/检索/写库），未登记落 🔧。
_TOOL_ICONS: dict[str, str] = {
    "delegate_to_member": "🤝",
    "delegate_to_all": "🤝",
    "transfer_to_agent": "🔀",
    "skill_load": "📚",
    "skill_exec": "📚",
    "skill_run": "📚",
    "skill_write_stdin": "📚",
    "skill_poll_session": "📚",
    "skill_select_tools": "📚",
    "knowledge_search": "🔍",
    "get_question_detail": "🔎",
    "ingest_question": "💾",
    "update_question": "✏️",
    "delete_question": "🗑️",
}

# TeamAgent 委派哨兵：delegate_to_member 的 function_response.response 带
# marker="__TEAM_DELEGATION__"（teams/core/_delegation_signal.py:33）。
# 真正的成员输出在后续 author=成员名 的事件里，这里只压暗带过、不计工具结果。
_DELEGATION_MARKER = "__TEAM_DELEGATION__"

# 委派类工具名：args 是 {member_name, task}，特化渲染成「leader → 成员（任务摘要）」
_DELEGATE_TOOLS = {"delegate_to_member", "delegate_to_all"}

# 工具参数/返回的单行预览长度；0 表示不截断（--full）
PREVIEW_CHARS = 300


# ═══════════════════════════════════════════════════════════════════════════════
# 小工具
# ═══════════════════════════════════════════════════════════════════════════════


def _preview(value: object, limit: int = PREVIEW_CHARS) -> str:
    """把任意 payload 压成单行短文本；limit<=0 不截断（只压换行）。"""
    text = " ".join(str(value).split())
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}…（共 {len(text)} 字，已截断）"


def _k(n: int) -> str:
    """token 数压缩显示：1234 → 1.2k。"""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _human_len(value: object) -> str:
    """state_delta 的值只报体量不报内容（防 pending_questions 刷屏）。"""
    n = len(str(value))
    return f"{n / 1000:.1f}k字" if n >= 1000 else f"{n}字"


# ═══════════════════════════════════════════════════════════════════════════════
# 渲染器
# ═══════════════════════════════════════════════════════════════════════════════


class ChatRenderer:
    """按轮次消费 Event 流并富渲染到终端。

    用法（chat.py 每轮）::

        renderer.start_turn()
        async for ev in runner.run_async(...):
            renderer.feed(ev)
        renderer.finish()   # 打本轮汇总行

    Args:
        console: rich Console（app.py 统一构造，markup=False + highlight=False）。
        show_thought: 是否渲染思考流（part.thought=True 的 text），默认展示为 dim 斜体。
        truncate: 工具参数/返回的单行截断长度；0 = 不截断。
    """

    def __init__(
        self,
        console: Console,
        *,
        show_thought: bool = True,
        truncate: int = PREVIEW_CHARS,
    ) -> None:
        self._c = console
        self._show_thought = show_thought
        self._truncate = truncate
        # 当前打开的流式段（author, kind）；kind ∈ {"body", "thought"}
        self._stream_key: tuple[str, str] | None = None
        self._stream_open = False
        # 本轮已流式过的 (author, kind)：非 partial 全文事件据此去重（坑位 3）
        self._streamed: set[tuple[str, str]] = set()
        # function_call.id → 发起时刻，配对 function_response 算耗时
        self._pending_calls: dict[str, float] = {}
        # author → {"in","out","thought","calls"} token 累加
        self._usage: dict[str, dict[str, int]] = {}
        self._n_tool_calls = 0
        self._n_delegations = 0
        self._turn_t0 = time.monotonic()

    # ── 轮次生命周期 ──────────────────────────────────────────────────────

    def start_turn(self) -> None:
        """重置本轮状态（上一轮残留的流式段一并关闭）。"""
        self._end_stream()
        self._streamed.clear()
        self._pending_calls.clear()
        self._usage.clear()
        self._n_tool_calls = 0
        self._n_delegations = 0
        self._turn_t0 = time.monotonic()

    def finish(self) -> None:
        """本轮结束：关闭流式段，打 token/耗时/调用数汇总行。"""
        self._end_stream()
        total = time.monotonic() - self._turn_t0
        tin = sum(d["in"] for d in self._usage.values())
        tout = sum(d["out"] for d in self._usage.values())
        tth = sum(d["thought"] for d in self._usage.values())
        calls = sum(d["calls"] for d in self._usage.values())
        line = Text(style="dim")
        line.append(
            f"📊 本轮 {total:.1f}s · LLM×{calls} · 工具×{self._n_tool_calls}"
            f" · 委派×{self._n_delegations}",
            style="bold",
        )
        line.append(f" · in {_k(tin)} / out {_k(tout)}")
        if tth:
            line.append(f" · 💭{_k(tth)}")
        self._print(line)
        # 多 agent 分账：谁能看出谁在烧 token 时才展开
        if len(self._usage) > 1:
            detail = Text("   ", style="dim")
            detail.append(
                "  ".join(
                    f"{a}:in{_k(d['in'])}/out{_k(d['out'])}"
                    + (f"/💭{_k(d['thought'])}" if d["thought"] else "")
                    for a, d in self._usage.items()
                ),
                style="dim",
            )
            self._print(detail)

    def on_run_exception(self, exc: BaseException) -> None:
        """run_async 迭代器本身抛异常（RunLimit 等框架异常是 raise 不是 yield）。"""
        self._end_stream()
        self._print(Text(f"❌ 本轮运行异常：{type(exc).__name__}: {exc}", style="bold red"))
        self._print(Text("   会话仍可用，可继续输入或 exit 退出。", style="dim"))

    # ── 分发主入口 ────────────────────────────────────────────────────────

    def feed(self, ev: Event) -> None:
        """消费单个事件，按类型分发渲染。判定顺序见模块头注释。"""
        # 1) 取消终止（Runner 对 RunCancelledException 的 yield 形态）
        if isinstance(ev, AgentCancelledEvent):
            self._end_stream()
            self._print(Text("⏹ 本轮已取消（run_cancelled）", style="yellow"))
            return

        error_code: str | None = ev.error_code
        responses = ev.get_function_responses() or []
        is_fatal = error_code is not None and not responses
        # 2) 致命错误（无 function_response 挂回）：LLM/系统级，该 agent 已终止。
        #    可恢复工具错误（error_code + function_response）不 return，落到
        #    _on_response 里标红（判据对齐 ag_ui/_core/_event_translator.py:197）。
        if is_fatal:
            self._end_stream()
            self._print_line(
                Text(f"❌ [{error_code}] {ev.error_message or '（无描述）'}", style="bold red")
            )
            self._print(Text("   （本 agent 本轮终止，事件流已收尾；会话仍可用）", style="dim"))
            return

        # 3) 长任务 / HITL：本项目未启用长工具，出现即提示（不崩就好）
        if isinstance(ev, LongRunningEvent):
            self._end_stream()
            self._print(Text("⧛ 长任务事件（HITL 等待外部输入，本项目未启用）", style="yellow"))
            return

        # 4) 工具参数流增量：args 不完整（tool_streaming_args 哨兵），跳过防刷屏
        if ev.is_streaming_tool_call():
            return

        # 5) 工具进度流（custom_metadata.tool_progress）：本项目工具暂未产生，留通道
        meta = ev.custom_metadata or {}
        if ev.partial and meta.get("tool_progress"):
            self._print(
                Text(
                    f"   ⏳ {meta.get('tool_name', '?')}: {_preview(meta.get('payload', ''), 120)}",
                    style="dim",
                )
            )
            return

        # 6) content parts：工具调用 / 工具结果 / 流式文字（思考与正文分流）
        if ev.content and ev.content.parts:
            for part in ev.content.parts:
                if part.function_call and not ev.partial:
                    self._on_call(ev.author, part.function_call)
                elif part.function_response and not ev.partial:
                    self._on_response(ev.author, part.function_response, error_code=error_code)
                elif part.text:
                    kind = "thought" if part.thought else "body"
                    if ev.partial:
                        self._stream_text(ev.author, kind, part.text)
                    elif (ev.author, kind) not in self._streamed:
                        # 非 partial 全文事件：该 author 本轮没流式过 → 兜底打一次
                        #（模型未开流式 / 终态回填），流式过 → 跳过防重复（坑位 3）。
                        self._stream_text(ev.author, kind, part.text)

        # 7) 状态变更：TeamAgent 持久化（pending_questions 等）是 content=None 的
        #    纯状态事件，旧版被 `not event.content` 整吞——现在露出键名+体量。
        if ev.actions and ev.actions.state_delta:
            self._on_state_delta(ev.author, ev.actions.state_delta)

        # 8) usage：只挂非 partial 终态事件，逐次累加（与上面各分支可叠加，坑位 2）
        if not ev.partial and ev.usage_metadata:
            self._accumulate_usage(ev.author, ev.usage_metadata)

    # ── 各类事件的渲染 ────────────────────────────────────────────────────

    def _stream_text(self, author: str, kind: str, text: str) -> None:
        """流式打印文字：正文按 author 着色，思考 dim 斜体；author 或 kind
        切换时换行重打前缀（💬[name] / 💭[name]）。"""
        if kind == "thought" and not self._show_thought:
            return
        style = "dim italic" if kind == "thought" else ""
        if self._stream_key != (author, kind):
            self._end_stream()
            prefix = Text("\n")
            if kind == "thought":
                prefix.append(f"💭[{author}] ", style="dim italic")
            else:
                color = _AUTHOR_COLORS.get(author, _DEFAULT_COLOR)
                prefix.append(f"💬[{author}] ", style=f"bold {color}")
            self._c.print(prefix, end="", soft_wrap=True)
            self._stream_key = (author, kind)
            self._stream_open = True
        self._streamed.add((author, kind))
        self._c.print(Text(text, style=style), end="", soft_wrap=True)

    def _on_call(self, author: str, call: Any) -> None:
        """工具调用（非 partial，args 完整）：单行紧凑，委派特化。"""
        self._end_stream()
        name = call.name or "?"
        icon = _TOOL_ICONS.get(name, "🔧")
        color = _AUTHOR_COLORS.get(author, _DEFAULT_COLOR)
        # 调试入口一行一个事件，长预览超宽即省略（_print_line；--full 不截
        # 内容但仍守单行纪律，超宽看宽终端）。
        line = Text()
        line.append(f"{icon} ", style=color)
        line.append(f"[{author}] ", style=f"bold {color}")
        if name in _DELEGATE_TOOLS and isinstance(call.args, dict):
            member = call.args.get("member_name") or call.args.get("member_names") or "?"
            task = _preview(call.args.get("task", ""), self._truncate)
            line.append(f"→ {member}", style="bold")
            if task:
                line.append(f"  {task}", style="dim")
            self._n_delegations += 1
        else:
            line.append(name, style="bold")
            if call.args:
                line.append(f"  {_preview(call.args, self._truncate)}", style="dim")
        self._n_tool_calls += 1
        if call.id:
            self._pending_calls[call.id] = time.monotonic()
        self._print_line(line)

    def _on_response(self, author: str, resp: Any, error_code: str | None = None) -> None:
        """工具结果（非 partial）：✔/✖ 定色 + 耗时（配对 call.id）+ 返回值截断。"""
        self._end_stream()
        name = resp.name or "?"
        icon = _TOOL_ICONS.get(name, "🔧")
        t0 = self._pending_calls.pop(resp.id or "", None)
        cost = f" ⚡{time.monotonic() - t0:.1f}s" if t0 is not None else ""
        payload = resp.response if isinstance(resp.response, dict) else {}
        # 委派哨兵：成员输出在后续事件里，这里压暗带过
        if payload.get("marker") == _DELEGATION_MARKER:
            member = payload.get("member_name") or "?"
            self._print_line(Text(f"   ↳ 委派 {member} 开始执行{cost}", style="dim"))
            return
        line = Text()
        line.append(f"{icon} ", style="dim")
        line.append(f"[{author}] ", style=_AUTHOR_COLORS.get(author, _DEFAULT_COLOR))
        line.append(f"{name} ", style="")
        err = error_code or payload.get("error")
        if err:
            line.append(f"✖ {err}", style="red")
            msg = payload.get("message") or resp.response
            if msg:
                line.append(f" {_preview(msg, self._truncate)}", style="dim")
        else:
            line.append(f"✔{cost}", style="green")
            if payload:
                line.append(f" {_preview(payload, self._truncate)}", style="dim")
        self._print_line(line)

    def _on_state_delta(self, author: str, delta: dict[str, Any]) -> None:
        """状态变更：只列 key + 值体量，内容不进终端。"""
        self._end_stream()
        segs = ", ".join(f"{k}({_human_len(v)})" for k, v in delta.items())
        self._print_line(Text(f"⚙ [{author}] state: {segs}", style="dim bright_black"))

    def _accumulate_usage(self, author: str, um: Any) -> None:
        """token 用量按 author 分账累加（字段名对齐 google-genai UsageMetadata）。"""
        d = self._usage.setdefault(author, {"in": 0, "out": 0, "thought": 0, "calls": 0})
        d["calls"] += 1
        d["in"] += getattr(um, "prompt_token_count", 0) or 0
        d["out"] += getattr(um, "candidates_token_count", 0) or 0
        d["thought"] += getattr(um, "thoughts_token_count", 0) or 0

    # ── 输出原语 ──────────────────────────────────────────────────────────

    def _end_stream(self) -> None:
        """结束当前流式段（打一个换行，让下一个独占行元素不接在正文尾巴上）。"""
        if self._stream_open:
            self._c.print()
            self._stream_open = False
            self._stream_key = None

    def _print(self, renderable: Text, **kwargs: Any) -> None:
        self._c.print(renderable, highlight=False, **kwargs)

    def _print_line(self, renderable: Text) -> None:
        """单行纪律：超终端宽裁尾省略，不折行。

        实测坑位：rich 的 Text(no_wrap=...) 构造属性会被 Console.print 忽略，
        必须作为 print 的关键字参数传入才生效——勿改回对象属性写法。
        """
        self._c.print(renderable, highlight=False, no_wrap=True, overflow="ellipsis")
