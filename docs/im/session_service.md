# session / memory 持久化（src/im/session_service.py）

> 对应代码：`src/im/session_service.py`（`ShortKeyClawSessionService` /
> `hashed_session_filename`）+ `tests/test_im_session_service.py`。
> 接入方式与网关配置见 [claw_app.md](claw_app.md)，平台侧联调见 [README.md](README.md)。

## 持久化机制（框架继承）

**继承而非自研**：`ClawApplication.__init__`（claw.py:122-159）已装配好三件套，`GaokaoClaw` 的
`super().__init__` 全量继承，两个 Runner（runner / worker_runner）构造时都把
`session_service` / `memory_service` 传进去——**TeamAgent 接入不额外做任何 session/memory 适配**。

| 组件 | 作用 | 落盘位置 |
| ------ | ------ | ------ |
| `ClawSessionService`（InMemory 子类） | 整个 Session 含 `state_delta`（→ `GaokaoState.pending_questions` 等）落 JSONL；重启 `create_session` 从盘恢复 | `{workspace}/sessions/*.jsonl` |
| `ClawSummarizerSessionManager` | 每轮 post-turn 检查：未摘要事件 ≥ `agent.memory_window`（配 30）→ LLM 滚动摘要、保留近 15 条；连续 3 次失败降级 RAW 原文归档（`_claw_summarizer.py:213`） | 摘要进长期记忆 |
| `ClawMemoryService` | 长期记忆按 `app/user/session` key 读写 MEMORY.md / HISTORY.md | `{workspace}/memory/` |

> **与 `scripts/chat` 的差别**：chat 调试包用裸 `InMemorySessionService`，重启即清空；QQ 入口继承
> ClawSessionService → **HITL 中间态（回显后等用户答 a/b）跨进程存活**。摘要器用的模型来自
> openclaw.yaml `agent` 段（配置与注入机制见 [claw_app.md](claw_app.md)），heartbeat 默认开启会额外计费、必须显式关闭。

### key 超长 bug 与本地修复（2026-09-10）

**现象**：QQ 入口下 session 快照**从未落盘**，`trpc_claw.log` 报
`Failed to persist session after turn ... AioFileStorage key too long: 152 > 128`。

**链路**（已用同参数复算，与日志一致）：

```
save_key = make_memory_key(app, user, session) = "gaokao_rag/{32位openid}/qq:{32位openid}"   79 字符
  → path = {workspace}/sessions/{safe_filename(...)}.jsonl                                 130 字符
  → StorageManager._session_storage_key(path) = "session:" + quote(path, safe="")  （_manager.py:279）
  → 152 字符 > AioFileStorage._validate_key 上限 DEFAULT_MAX_KEY_LENGTH = 128 （_aiofile_storage.py:181-185）
```

**上游是死配置**：`FileStorageConfig.max_key_length`（默认 255，容得下 152）被 `__init__` 读进
`self._max_key_length`，但 `_validate_key` 写成了 `staticmethod`、只比较模块常量 → 调配置无效。
（已决定单独给上游提 issue；本地按「MVP 免补丁」决策不改包内文件。）

**本地修复**：`src/im/session_service.py` 的 `ShortKeyClawSessionService(ClawSessionService)`
只覆写 `_get_session_path` → `sessions_dir / f"{sha256(save_key)[:16]}.jsonl"`
（16 位十六进制无字符需转义，key 降到 **89** 字符，workspace 再深 30 字符仍有 39 字符余量）。
`GaokaoClaw.__init__` 在重建 Runner **之前**替换 `self.session_service` 并同步
`self.command_handler.params.session_service`（可变 dataclass 字段，handler 在调用点读）——父类中共
三处持有该引用，Runner 走既有重建代码，替换这两处即闭合。

- 读写一致性：`create_session` / `get_session` / `update_session` / `_maybe_migrate` 全经
  `_get_session_path` 单点取路径，覆写一处即全覆盖（`_get_legacy_session_path` 刻意不覆写，
  旧目录迁移仍按旧命名找文件）
- 代价：session 文件名不可读（16 位哈希）；排障靠日志里的 app/user/session 与 save_key
- 回归守卫：`tests/test_im_session_service.py`（纯函数，不构造配置/模型）——断言旧命名确实超限
  （152）而哈希短名 ≤128，另测确定性 / 区分度 / 字符安全

离线验证（**不发 LLM 请求、不计费**）：

```bash
uv run python -X utf8 -c "
import asyncio
from src.im.claw_app import create_claw_app
async def main():
    app = create_claw_app()
    s = await app.session_service.create_session(
        app_name='gaokao_rag', user_id='<openid>', session_id='qq:<openid>')
    await app.session_service.update_session(s)
    print(app.session_service._get_session_path(s.save_key))
asyncio.run(main())"
# 预期：~/.trpc_claw/workspace/sessions/<16位哈希>.jsonl 出现，无 key too long
```

> ⚠️ 该 smoke 未传 `agent_context`，框架会记一条 `Failed to load Session ... 'NoneType' object has no
> attribute 'with_metadata'` 警告（首次 create 无文件可载 + 无上下文所致），不影响结论；真实链路
> `_run_turn` 始终带 `agent_context`。

**待办**：上游 issue（`_validate_key` 应改实例方法用 `self._max_key_length`，可选加超长哈希兜底）。
