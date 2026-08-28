# 对话 CLI（scripts/chat.py）

> 对应代码：`scripts/chat.py`。开发调试入口——**从命令行模拟 QQ 与 Team Leader 对话**，用于验证「口述题目 → 入库」端到端闭环。正式 QQ 入口（trpc-claw）落地前以此代替，落地后也可以用来调试。

## 定位

| 入口 | 用途 | 状态 |
|------|------|------|
| `scripts/chat.py` | 开发调试：命令行模拟 QQ 与 Leader 对话（一问一答、多轮） | MVP 开发期使用 |
| `scripts/ingest.py` | 批量摄取：ima 导出的 PDF 批量入库（不经 TeamLeader） | 规划中 |
| `scripts/mcp_server.py` | MCP Server 入口（stdio/SSE/HTTP） | 规划中 |
| QQ（trpc-claw `_qq.py`） | 正式学生入口 | 后续接入 |

chat.py 是「模拟 QQ」的最小入口：同一终端一问一答，事件流打印出 Leader 委派链（哪个成员被调、调了什么工具、返回什么），便于开发期观察多 Agent 协作。

## 前置条件

1. **`.env` 已配置**：`api_key` / `base_url` / `model`（`src/config.py` 加载时自动读 `.env`，chat.py 无需重复加载）
2. **`data/` 可写**：真实写库（SQLite + Chroma），`data/` 不存在时需可自动创建
3. 依赖已安装（`uv sync` 或等价）

## 运行

```bash
uv run python scripts/chat.py
```

> `uv sync` 的 editable 安装已把 `src` 包注入环境，`uv run` 下任意 cwd 可跑；
> 中文输出无需 `PYTHONIOENCODING`（脚本自带 stdout UTF-8 兜底）。

启动后出现 `你（QQ）:` 提示符，输入消息回车即发送；`exit` / `quit` 结束。

## 交互流程

```
你（QQ）: 已知函数 f(x) = x² - 2x + 3，求在 [0,3] 上的最小值。我的思路是配方然后看对称轴位置。
[structure_recognition] （归一化处理后）
[leader] 已识别 1 道题：二次函数区间最值（配方/对称轴）
         去向？a=入库 b=跳过
你（QQ）: a
[storage_decision] （写库）
[leader] 入库成功：question_id=1, doc_id=q_1
```

- **多角色输出**：每个事件打印作者（`leader` / `structure_recognition` / `storage_decision`），工具调用（`delegate_to_member` / `skill_load` / `ingest_question`）可见——模拟 QQ 多角色对话
- **多轮会话**：同一次进程内多次问答共享会话上下文（InMemorySessionService），追问「第二道呢」可接续
- **真实调用**：chat.py 走真实 DeepSeek + Embedding + Chroma 写库（计费），仅用于开发调试，不进 pytest

## 技术实现要点

```python
runner = Runner(
    app_name="gaokao_rag_chat",
    agent=create_gaokao_leader(),          # src/agent/leader.py 工厂
    session_service=InMemorySessionService(),
)
# 固定 user_id / session_id（模拟 QQ 单用户会话）
# run_async 事件流：文字流式打印 + function_call 打印工具调用 + 异常兜底不崩溃
```

- **MVP 用 InMemorySessionService**：进程内保持多轮上下文；重启即清空。持久化（`SqlSessionService`）随正式入口切换
- `create_gaokao_leader()` 构造时读 `.env`，环境缺失会抛错——运行前确认配置

## MVP 边界

- 错题去向：Leader 会告知「错因记录暂不支持」，题目仍可入库
- `topic_names`（知识点归位）：本轮不传，入库不挂知识点
- 讲解段（`lecture_segments`）：本轮忽略，不入库不回显
- 意图分流：不做——任何题目相关内容一律按摄入处理

## 相关

- Leader 编排与职责：`docs/agent/leader.md`
- 摄入侧数据流与 State 契约：`docs/agent/README.md`
- 写库工具：`docs/agent/tools/ingest_tool.md`
