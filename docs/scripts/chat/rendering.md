# chat 输出渲染（rich）

> 对应代码：`scripts/chat/render.py`（`ChatRenderer`）。app.py 只喂事件
> （`ask_once` 逐条 `renderer.feed(event)`），渲染决策全部在这一层。
> 入口与渲染开关见 [README.md](README.md)。

## 事件类型判定

分发基于 tRPC-Agent 事件类型调研（2026-09-08）：`Event` 无 type 枚举，靠
`partial` / `part.thought` / `error_code` / `actions.state_delta` /
`usage_metadata` 字段组合判定；分发顺序对齐框架官方消费端范例
`server/ag_ui/_core/_event_translator.py`（取消 → 致命错误 → 长任务 →
工具参数流跳过 → 工具进度流 → content parts → state_delta → usage 累加）。

## 呈现约定

| 事件类型 | 判定 | 呈现 |
|------|------|------|
| 正文流 | `partial && text && !thought` | `💬[author]` 前缀 + author 固定色，流式打字机 |
| 思考流 | `partial && text && thought` | `💭[author]` 前缀 + dim 斜体，与正文分层（DeepSeek `reasoning_content` 映射为 `thought=True`，旧版混在正文里打） |
| 工具调用 | 非 partial `function_call` | 单行：家族图标（🤝委派 / 📚技能 / 🔍检索 / 💾写库）+ author + args 截断预览；`delegate_to_member` 特化为「→ 成员（任务摘要）」 |
| 工具结果 | 非 partial `function_response` | 单行：✔ 绿 + ⚡耗时（配对 call.id）+ 返回截断；`error_code`/`response["error"]` 时 ✖ 红 |
| 委派哨兵 | response 带 `marker="__TEAM_DELEGATION__"` | 压暗一行「↳ 委派 X 开始执行」（成员输出在后续 author=成员 的事件里） |
| 致命错误 | `error_code` 且无 function_response | 红行 `❌ [code] message`（框架错误是 yield 事件不抛异常，旧版 `except` 兜底接不到） |
| 可恢复工具错误 | `error_code` 且有 function_response | 并入工具结果行标红（会回喂 LLM 重试） |
| 状态变更 | `actions.state_delta`（常为 content=None） | 灰行 `⚙ [author] state: key(体量)`——只报键名和字数不刷内容（旧版整吞，TeamAgent 的 `pending_questions` 持久化不可见） |
| 取消 | `AgentCancelledEvent` | 黄行「⏹ 本轮已取消」 |
| token 用量 | 非 partial 事件 `usage_metadata` | 轮末汇总行 `📊 耗时 · LLM×n · 工具×n · 委派×n · in/out/💭`，多 agent 时按 author 分账 |

author 色板与工具图标集中在 render.py 顶部常量（`_AUTHOR_COLORS` / `_TOOL_ICONS`），
新成员 / 新工具未登记时落默认色 / 🔧 图标，不阻断渲染。

## 约定细节（实测坑位）

- **流式去重**：终态非 partial 事件携带的全文不重打；但某 author 本轮只有
  非 partial 文字（模型未开流式）时兜底整段打一次，信息不静默丢失。
- **工具参数流**（`tool_streaming_args` 增量）跳过不渲染（args 不完整，刷屏）。
- **分支可叠加**：同一终态事件可同时携带 text + state_delta + usage_metadata，
  分发按「依次检查、多路命中」写，不做严格互斥（对齐官方 translator）。
- **单行纪律**：工具 / 状态 / 错误行超终端宽即省略号裁尾不折行。实现注意：
  rich 的 `Text(no_wrap=...)` 构造属性会被 `Console.print` 忽略，**必须走 print
  参数**（`_print_line`）。
- **Console 关 markup/highlight**：LLM 原文里的 `[xxx]` 会被 rich 当样式标签
  炸 MarkupError，着色一律 `Text(style=...)`。
- **stdout UTF-8 兜底要先于 Console 构造**：rich 的 Windows legacy 渲染器按
  `file.encoding` 写文本，GBK 环境下 emoji 直接抛 UnicodeEncodeError
  （app.py `_repl` 开头，注释已钉死顺序）。

## 相关

- 入口 / 渲染开关：[README.md](README.md)
- 事件流生产侧（TeamAgent 委派机制）：`docs/agent/README.md`
