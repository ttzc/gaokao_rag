# 对话调试入口（scripts/chat/ 包）

> 对应代码：`scripts/chat/`（app.py REPL 主模块 / prompt.py 输入层 / render.py 渲染层）。
> 开发调试入口——**从命令行模拟 QQ 与 Team Leader 对话**，用于验证「口述题目 → 入库」
> 端到端闭环。正式 QQ 入口（trpc-claw）落地前以此代替，落地后也可以用来调试。

## 定位

| 入口 | 用途 | 状态 |
|------|------|------|
| `gaokao chat` | 开发调试：命令行模拟 QQ 与 Leader 对话（一问一答、多轮）——**推荐入口**（console script） | MVP 开发期使用 |
| `python -m scripts.chat` | 同上（包直跑，参数完全一致） | 等价备用 |
| `gaokao browse/detail` | 题目只读 CLI（不经 LLM） | ✅ 已落地 |
| `scripts/ingest.py` | 批量摄取：ima 导出的 PDF 批量入库（不经 TeamLeader） | 规划中 |
| `scripts/mcp_server.py` | MCP Server 入口（stdio/SSE/HTTP） | 规划中 |
| QQ（trpc-claw `_qq.py`） | 正式学生入口 | 后续接入 |

chat 是「模拟 QQ」的最小入口：同一终端一问一答，rich 富渲染打印出 Leader 委派链
（哪个成员被调、调了什么工具、返回什么、烧了多少 token），便于开发期观察多 Agent 协作。

## 前置条件

1. **`.env` 已配置**：`api_key` / `base_url` / `model`（`src/config.py` 加载时自动读 `.env`，chat 包无需重复加载）
2. **`data/` 可写**：真实写库（SQLite + Chroma），`data/` 不存在时需可自动创建
3. **`uv sync` 已执行**：`gaokao` 命令由 `[project.scripts]` 注册进虚拟环境，未 sync 则用备用形态 `uv run python scripts/cli.py chat`

## 运行

```bash
uv run gaokao chat             # 推荐：console script（uv sync 后可用）
uv run python -m scripts.chat  # 等价：包直跑（开发调试习惯用法）
```

> `uv sync` 的 editable 安装已把 `src` / `scripts` 包注入环境，`uv run` 下任意 cwd 可跑；
> 中文输出无需 `PYTHONIOENCODING`（入口自带 stdout UTF-8 兜底）。help 的 usage 前缀
> 按启动形态自适应（`gaokao chat …` / `python -m scripts.chat …`），不用记多套。

启动后出现 `你（QQ）:` 提示符，输入消息回车即发送；`exit` / `quit` 结束。

### 渲染开关

| 参数 | 效果 |
|------|------|
| `--no-think` | 不渲染模型思考流（DeepSeek `reasoning_content`） |
| `--full` | 工具参数 / 返回值不截断（默认单行 300 字截断） |

选项详情：`uv run gaokao chat --help`（透传给 chat 包自己的解析器）。

## 交互流程

```
你（QQ）: 已知函数 f(x) = x² - 2x + 3，求在 [0,3] 上的最小值。我的思路是配方然后看对称轴位置。
💭[gaokao_leader] …（思考流，dim 斜体）
🤝 [gaokao_leader] → structure_recognition  识别题目原文…
💬[structure_recognition] （归一化后的题目三段）
⚙ [structure_recognition] state: pending_questions(812字)
💬[gaokao_leader] 已识别 1 道题：二次函数区间最值（配方/对称轴）。去向？a=入库 b=跳过
📊 本轮 9.8s · LLM×3 · 工具×2 · 委派×1 · in 7.4k / out 950 · 💭620
你（QQ）: a
🤝 [gaokao_leader] → storage_decision  {pending_questions, ingest_decisions}
💾 [storage_decision] ingest_question  {question_id: …}
💬[gaokao_leader] 入库成功：question_id=1, doc_id=q_1
📊 本轮 5.1s · LLM×2 · 工具×2 · 委派×1 · in 6.1k / out 340
```

- **多角色输出**：rich 富渲染（[→ rendering.md](rendering.md)），每个事件带作者
  （`gaokao_leader` / `search` / `structure_recognition` / `storage_decision` /
  `question_maintain`）固定色，工具调用 / 返回 / 委派链 / token 统计全部可见
- **多行输入**：长题目直接粘贴（[→ input.md](input.md)）
- **多轮会话**：同一次进程内多次问答共享会话上下文（InMemorySessionService），追问「第二道呢」可接续
- **真实调用**：chat 走真实 DeepSeek + Embedding + Chroma 写库（计费），仅用于开发调试，不进 pytest

## 包结构与实现要点

```tree
scripts/chat/
├── __init__.py    # 包说明（模块分工 + 启动方式）
├── __main__.py    # python -m scripts.chat 入口（转发 app.run）
├── app.py         # REPL 主循环、参数解析、ask_once 喂事件
├── prompt.py      # prompt_toolkit 多行输入（键位 + 历史）
└── render.py      # ChatRenderer：事件类型 → rich 呈现
```

```python
runner = Runner(
    app_name="gaokao_rag_chat",
    agent=create_gaokao_leader(),          # src/agent/leader.py 工厂
    session_service=InMemorySessionService(),
)
# 固定 user_id / session_id（模拟 QQ 单用户会话）
# run_async 事件流逐条喂 ChatRenderer.feed()：按事件类型分发富渲染（见 rendering.md），
# 轮末 renderer.finish() 打汇总行；raise 型框架异常（RunLimit 等）由
# renderer.on_run_exception() 兜底，REPL 不退出
```

- **统一入口转发**：`scripts/cli.py`（console script `gaokao` 的实现体）顶层对
  `chat` 早路由（参数不过外层 argparse），惰性 import 本包——browse/detail 保持
  零 LLM / 零 Key 依赖。`app.run(argv, prog=...)` 供 cli.py 与 `__main__.py` 两条路径复用
- **渲染器与输入层独立文件**：事件类型清单与分发顺序对齐框架官方消费端范例
  `server/ag_ui/_core/_event_translator.py`（详见 render.py 模块头注释）
- **依赖**：`rich` / `prompt-toolkit` 已在 `pyproject.toml` 显式声明（此前为转依赖）
- **MVP 用 InMemorySessionService**：进程内保持多轮上下文；重启即清空。持久化（`SqlSessionService`）随正式入口切换
- `create_gaokao_leader()` 构造时读 `.env`，环境缺失会抛错——入口友好提示退出码 1，不抛裸栈

## MVP 边界

- 错题去向：Leader 会告知「错因记录暂不支持」，题目仍可入库
- `topic_names`（知识点归位）：本轮不传，入库不挂知识点
- 讲解段（`lecture_segments`）：本轮忽略，不入库不回显
- 意图分流：不做——任何题目相关内容一律按摄入处理
- 单次查询 / 复习模式（`--mode review`）：未实现，chat 目前只有交互 REPL

## 相关

- 输出渲染详解：[rendering.md](rendering.md)
- 多行输入详解：[input.md](input.md)
- 统一 CLI（browse / detail / chat 路由）：[../cli.md](../cli.md)
- Leader 编排与职责：`docs/agent/leader.md`
- 摄入侧数据流与 State 契约：`docs/agent/README.md`
- 写库工具：`docs/agent/tools/ingest_tool.md`
