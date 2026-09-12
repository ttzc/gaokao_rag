# Issue to trpc-group/trpc-agent-python: FileStorageConfig.max_key_length 失效导致 session 持久化静默失败

<!-- ════════════ 本地提交备注 · 不要复制到 GitHub issue 正文 ════════════
     以下引用块是本次提交的记录（状态 / 标题 / 标签 / 版本与行号基准），
     **不属于 issue 正文**——本块包在 HTML 注释里，GitHub 不会渲染；
     若日后转载本文件或据此重新起草，可整块删除（含首尾两行注释标记）。
     ════════════ 本地提交备注 BEGIN ════════════

> **状态**：**已提交** —— [#333](https://github.com/trpc-group/trpc-agent-python/issues/333)（OPEN，2026-09-12）  
> **目标仓库**：`trpc-group/trpc-agent-python`  
> **标题**：`[Bug] FileStorageConfig.max_key_length 失效导致 session 持久化静默失败`  
> **标签**：`bug`（实际提交时**未附标签**，可后续补加）  
> **发现时间**：2026-09-10；**提交时间**：2026-09-12  
> **实测版本**：trpc-agent-py **1.1.20**（release commit `f05797d`，其后 `1aa44cf` 为本仓库最新提交）  
> **行号基准**：trpc-agent-py 的行号引用均给出 **commit permalink**，锁定 `1aa44cf`（与 release commit `f05797d` 在本文涉及的文件上**完全一致**，仅额外新增 `storage/_sql_common.py`，故行号在 1.1.20 同样成立）；nanobot 行号锁定 tag **`v0.2.0`**（commit `c018c3f`）

     ════════════ 本地提交备注 END ════════════ -->

---

## TL;DR

- **死配置**：`FileStorageConfig.max_key_length`（默认 255）被 `AioFileStorage.__init__` 读进
  `self._max_key_length`，但 `_validate_key` 是 `@staticmethod`、只比硬编码的
  `DEFAULT_MAX_KEY_LENGTH = 128`；该实例属性在包内**没有任何其他引用**，配置形同虚设。
- **触发面广**：**官方支持的两条通道（Telegram / 企业微信）都存在可触发场景，且部分场景不需要
  极端 id 即可触发**——session 的存储 key 由绝对路径派生，Telegram 的 `sender_id` 含 username
  （上限 32 字符），企业微信单聊里 `userid` 会出现两次（且非超级管理员创建的机器人拿到的是
  **加密 userid**）。
- **后果是静默的**：key 破 128 → `add()` 抛 `ValueError` → 被
  `ClawApplication._persist_session_after_turn` 吞成一条 warning → **持久化快照缺失，进程重启或
  跨进程读取时状态丢失；该持久化路径只记录 warning，没有 error 级别日志**。
- **短期修复**：`_validate_key` 改为实例方法、比较 `self._max_key_length`
  （注意自带测试有 6 处按类名静态调用，需同步调整）。
- **长期加固**：key 长度本身无上界（随 workspace 深度增长），需从 `ClawSessionService._get_session_path`
  侧哈希 session 文件名；只调大上限不解决问题。

## 环境

- trpc-agent-py：**1.1.20**（release commit `f05797d`；实测复现在此版本及其后 `1aa44cf` 上一致）
- 操作系统：Windows 11，Python 3.13
- 上游依赖：nanobot-ai：**0.2.0**（复现所用；本文所有 nanobot 行号与 permalink 均以此为基准）
- nanobot 0.3.0 目前因 [`nanobot.heartbeat` 缺失](https://github.com/trpc-group/trpc-agent-python/issues/320) 无法与 trpc-claw 配套使用，故本文不涉及其行号
- 存储：默认 file 后端（`AioFileStorage`）、默认 workspace（`~/.trpc_claw/workspace`，见 [`config/_constants.py:16`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/config/_constants.py#L16) 的 `DEFAULT_WORKSPACE_PATH`）
- 复现用 **Telegram / 企业微信的真实 id 形态**构造（不依赖通道实际在线）
- 企业微信的 id 形态参考其 [官方文档](https://developer.work.weixin.qq.com/document/62155)

## 复现步骤

调用形式已对照 1.1.20 源码核对：`AioFileStorage.add(db, data)` 的 `data` 既接受 `dict` 也接受
`FileData`（[`_aiofile_storage.py:93-99`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_aiofile_storage.py#L93-L99)）；
file 后端的 `commit()` 是 no-op（[`:152-153`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_aiofile_storage.py#L152-L153)），
**无需** `await db.commit()`；会话句柄用 `create_file_session()`
（[`:84-86`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_aiofile_storage.py#L84-L86)）
而非 `create_db_session()`。下面脚本可直接运行，仅需 `nanobot` + `trpc_agent_sdk`：

```python
import asyncio
from pathlib import Path
from urllib.parse import quote

from nanobot.utils.helpers import safe_filename            # 与 ClawSessionService 同一个 helper
from trpc_agent_sdk.server.openclaw.config import FileStorageConfig
from trpc_agent_sdk.server.openclaw.storage import AioFileStorage

WS = Path.home() / ".trpc_claw" / "workspace"              # 与默认 workspace 一致
APP = "custom_rag"

# 场景 A：Telegram 超级群——sender_id = f"{user.id}|{user.username}"
#        （nanobot.channels.telegram._sender_id，username 上限 32 字符）
tg_user_id = "123456789|" + "u" * 32
tg_session = "telegram:-1001234567890"                 # 超级群 chat_id

# 场景 B：企业微信单聊——chatid 仅群聊返回，单聊回落 userid
#        （nanobot.channels.wecom：chat_id = body.get("chatid", sender_id)）
#        非企业超级管理员创建的机器人拿到的是加密 userid；
#        企业微信未公开其固定长度，下面的 40 字符（以及下文的 chatid 22 字符）都是构造示例
wc_user_id = "e" * 40
wc_session = "wecom:" + wc_user_id


def build_key(user_id: str, session_id: str) -> str:
    # ClawSessionService._get_session_path + StorageManager._session_storage_key 的等价复算
    save_key = f"{APP}/{user_id}/{session_id}"
    name = f"{safe_filename(save_key.replace(':', '_'))}.jsonl"
    return f"session:{quote(str(WS / 'sessions' / name), safe='')}"


async def main():
    for label, user_id, session_id in (
        ("Telegram 超级群", tg_user_id, tg_session),
        ("企业微信单聊", wc_user_id, wc_session),
    ):
        key = build_key(user_id, session_id)
        print(f"{label}: user_id={len(user_id)} session_id={len(session_id)} key={len(key)}")
        storage = AioFileStorage(
            config=FileStorageConfig(base_dir=str(WS), max_key_length=255))   # 显式给到 255
        db = await storage.create_file_session()
        try:
            await storage.add(db, {"key": key, "value": "{}"})
            print(f"{label}: OK")
        except ValueError as ex:
            print(f"{label}: RAISED ValueError: {ex}")
        await storage.close()


asyncio.run(main())
```

实际输出：

```
Telegram 超级群: user_id=42 session_id=23 key=150
Telegram 超级群: RAISED ValueError: AioFileStorage key too long: 150 > 128
企业微信单聊: user_id=40 session_id=46 key=171
企业微信单聊: RAISED ValueError: AioFileStorage key too long: 171 > 128
```

注意 `max_key_length=255` 已显式传入，异常仍然是 `> 128`——这就是配置失效的直接证据。

真实链路中该异常不会冒泡：`ClawApplication._persist_session_after_turn` 捕获后只记一条 warning——
`except` 块在
[`claw.py:460-467`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/claw.py#L460-L467)，
`logger.warning` 调用在
[`claw.py:461`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/claw.py#L461)——
形如

```
Failed to persist session after turn app=<app> user=<id> session=<channel>:<chat_id>:
AioFileStorage key too long: 150 > 128
```

## 影响面：id 长度预算问题（两条官方通道均存在可触发场景）

这不是某条通道的特例，而是「存储 key 由绝对路径派生 + 用户/会话 id 直接进 key」的结构性缺陷：
只要 id 够长就会撞上 128 上限。测试用配置（`app_name=custom_rag`、默认
`~/.trpc_claw/workspace`）实测各形态：

| 通道 / 场景 | user_id 长度 | session_id 长度 | key | 余量 |
| ------ | :---: | :---: | :---: | :---: |
| Telegram 私聊（username 11 字符） | 21 | 18 | 124 | +4 |
| Telegram 私聊（username 32 字符） | 42 | 18 | **145** | −17 |
| Telegram 超级群（chat_id 14 位 + username 32） | 42 | 23 | **150** | −22 |
| 企业微信单聊（明文 userid 10 字符） | 10 | 16 | 111 | +17 |
| 企业微信单聊（明文 userid 20 字符） | 20 | 26 | **131** | −3 |
| 企业微信单聊（加密 userid 40 字符，示例） | 40 | 46 | **171** | −43 |
| 企业微信群聊（chatid 22 + 明文 userid 10） | 10 | 28 | 123 | +5 |
| 企业微信群聊（chatid 22 + 加密 userid 40，示例） | 40 | 28 | **153** | −25 |

> **关于 id 长度**：除 Telegram 的 `username 32 字符`（官方上限，属真实约束）外，表中其余 id 长度均为**构造示例**。企业微信官方文档 [接收消息](https://developer.work.weixin.qq.com/document/62155) 对加密 userid 只说明「如果智能机器人创建者为企业超级管理员，则为明文 userid，否则为企业主体下的加密 userid」，**未给出固定长度**，`chatid` 的长度同样未公开。这两者的实际长度均**不受使用者控制**，只能按最坏情况预留余量。

经验公式（测试用项目 app 名与 workspace 长度下）：

```
key ≈ 85 + len(user_id) + len(session_id)     → 安全阈值：两者之和 ≤ 43 字符
```

该式在本表 8 个场景下**逐行精确成立**（常数 85 = `session:` 前缀 8 + 固定路径段 63 +
固定转义膨胀 14，见「次生问题」一节的拆解）。需要说明的是：路径中的转义膨胀已被计入这 85——
`WS` 自带的 4 个 `\` 加上 `\sessions\` 2 个，共 6 个 `\`→`%5C`（+12），盘符 `:`→`%3A`（+2）。
若 `user_id` / `session_id` 本身含**经 `safe_filename` 后仍需百分号编码**的字符
（空格、`%`、非 ASCII 等），实际长度还会进一步膨胀；常见的 Telegram / 企业微信 id
（数字、字母、`|`、`:`、`-`）会被 `safe_filename` 归一为 `_`，不产生额外编码，故此时该式为精确式。

- **Telegram**：`_sender_id`（`nanobot.channels.telegram`）返回 `f"{user.id}|{user.username}"`，无 username 时退化为 `str(user.id)`——见  [`channels/telegram.py:818-821`](https://github.com/HKUDS/nanobot/blob/v0.2.0/nanobot/channels/telegram.py#L818-L821)。username 上限 32 字符 → **私聊**只要用户名较长即超限，无需进群（9 位数字 id + 15 字符 username 时 key 恰好 128，已在限额上；16 字符即失败）。
- **企业微信**：官方文档（`developer.work.weixin.qq.com/document/62155`、`/60904`）明确 `chatid` **仅群聊返回**；`nanobot.channels.wecom` 的 [`chat_id = body.get("chatid", sender_id)`](https://github.com/HKUDS/nanobot/blob/v0.2.0/nanobot/channels/wecom.py#L256) 在单聊时回落到 `userid` ——于是 **userid 在 `user_id` 与 `session_id` 里出现两次**，单聊 userid 超过约 18 字符即超限。群聊侧同一文档说明「如果智能机器人创建者为企业超级管理员，则为明文 userid，否则为企业主体下的 **加密 userid**」。上表所用的 **40 字符只是构造示例、实际长度可能不同**（企业微信未公开该长度，以其官方文档为准），但无论具体多长都**不受使用者控制**——只能按最坏情况给 key 预留余量。
- `InboundMessage.session_key_override`（thread-scoped session）目前各通道均未使用（仅定义于 [`nanobot.bus.events:24-29`](https://github.com/HKUDS/nanobot/blob/v0.2.0/nanobot/bus/events.py#L24-L29)），暂不构成额外风险；但一旦启用，id 会更长。
- 阈值还会随 **workspace 路径深度** 与 **`runtime.app_name` 长度** 进一步收紧：上表是 `custom_rag`（10 字符）+ Windows 用户目录（35 字符）的结果，两者任一变长，余量立即被吃掉。
- 上表 8 个形态逐一调用 `add()` 实测：**余量为负的 5 个（145 / 150 / 131 / 171 / 153）全部抛 `ValueError: AioFileStorage key too long: N > 128`，余量为正的 3 个（124 / 111 / 123）写入成功**；表中没有余量为 0 的行。（key 恰好 128 的边界组合另见上文 Telegram 一条，同样未被 `>` 拦下；若把它算作第 9 个测试点，则分布为 5 负 / 3 正 / 1 零。）

**平台差异**：以上数据均来自 **Windows 11**，常数 85 只在「Windows 路径 + `custom_rag` + 35 字符 workspace」下成立。Linux 侧 `quote(safe='')` 会把 `/` 编码成 `%2F`（与 Windows 的 `\`→`%5C` 同为 1→3 字符），路径分隔符都是 6 个、膨胀量相同；差别只在 Windows 多一个盘符 `:`→`%3A`，故**同深度路径下 Linux 的常数比 Windows 少 2**。同一组 id 计算对照：Windows `key=150`（常数 85）vs 典型 `/home/<user>/.trpc_claw/workspace` `key=145`（常数 80，另 3 字符差来自路径本身更短）。

但 key **随 `user_id` + `session_id` 线性增长、随 workspace 深度无上界增长**的结构性问题与平台无关：Linux 上 id 够长同样会撞上 128 上限，下文「调大 `max_key_length` 不能根治」「需从 `_get_session_path` 侧哈希文件名」的结论也完全一致。

## 根因

死配置的声明处 ——
[`config/_config.py:120-123`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/config/_config.py#L120-L123)：

```python
class FileStorageConfig(BaseModel):
    """trpc_claw file storage config."""
    base_dir: str = ""
    max_key_length: int = 255        # 被读取，但从未被使用
```

读取处
[`_aiofile_storage.py:81`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_aiofile_storage.py#L81)
与校验处
[`_aiofile_storage.py:180-188`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_aiofile_storage.py#L180-L188)：

```python
self._max_key_length = config.max_key_length        # 第 81 行 —— 死属性，全仓仅此一处引用

@staticmethod                                       # 第 180 行 —— staticmethod 拿不到 self
def _validate_key(key: str) -> None:
    if not key:
        raise ValueError("AioFileStorage key cannot be empty")
    if len(key) > DEFAULT_MAX_KEY_LENGTH:           # 第 184 行 —— 模块常量 = 128
        raise ValueError(f"AioFileStorage key too long: {len(key)} > {DEFAULT_MAX_KEY_LENGTH}")
    if "/" in key or "\\" in key:                   # 第 186 行
        raise ValueError("AioFileStorage key must not contain path separators")
```

`DEFAULT_MAX_KEY_LENGTH = 128` 定义在
[`trpc_agent_sdk/storage/_constants.py:8`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/storage/_constants.py#L8)。

顺带一个不对称细节：`_validate_key` 只在
[`add()`（第 100 行）](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_aiofile_storage.py#L100)
里被调用，
[`get()`（第 142 行）](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_aiofile_storage.py#L142)
**不校验**——即**读路径不校验 key，因此超长 key 不会在读取时暴露**；写入失败后读取只会返回
`None`，进一步静默化故障。两条路径在存储管理层的入口分别是
`StorageManager.save_session`（[`_manager.py:109`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_manager.py#L109)）
→ `_set_value`（[`:196`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_manager.py#L196)）
→ `storage.add`（会抛），与
`StorageManager.load_session`（[`:142`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_manager.py#L142)）
→ `_get_value`（[`:212`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_manager.py#L212)）
→ `storage.get`（不抛）。因为 `save_session` 与 `load_session` 用**同一个**
`_session_storage_key(path)` 推导 key，一旦写入侧失败、读取侧却「正常」返回 `None`（文件不存在），
故障就会以「读取返回 `None`（如同会话为空）、而该持久化路径只记录 warning、没有 error 级别日志」
的形式呈现。

## 次生问题：派生 key 长度无上界

[`_manager.py:278-280`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_manager.py#L278-L280)：

```python
@staticmethod
def _session_storage_key(path: Path) -> str:
    return f"session:{quote(str(path), safe='')}"
```

存储 key 由 session JSONL 的**绝对路径**拼成，长度取决于用户的 workspace 路径（Windows 上还要
叠加 `%5C` / `%3A` 的转义膨胀）与 id 长度。以 Telegram 超级群场景为例：`save_key` 77 字符
→ 文件名 77 → 绝对路径 128 → 转义 +14 → key 150。

常量拆解（测试配置下，`key = 85 + U + S`）：

| 组成 | 长度 |
| ------ | :---: |
| `session:` 前缀 | 8 |
| workspace 绝对路径（35） + `\sessions\`（10） | 45 |
| `save_key` 逻辑部分 `app/user/session` + 2 个分隔符 + `.jsonl` | `10 + U + S + 8` |
| 固定转义膨胀（6×`\`→`%5C`、1×`:`→`%3A`） | 14 |
| **合计** | **`85 + U + S`** |

更深的用户目录或更长的 workspace 会突破 255 的上限，所以**只调大上限并不能根除这个失败模式**。

## 建议修复

1. 让配置生效（恢复配置本身的语义）：

```python
def _validate_key(self, key: str) -> None:
    if not key:
        raise ValueError("AioFileStorage key cannot be empty")
    if len(key) > self._max_key_length:
        raise ValueError(f"AioFileStorage key too long: {len(key)} > {self._max_key_length}")
    if "/" in key or "\\" in key:
        raise ValueError("AioFileStorage key must not contain path separators")
```

   改为实例方法时需要同步调整**以类名静态调用**的既有位置：目前仓库内除 `add()` 外，还有自带测试
   [`tests/server/openclaw/storage/test_aiofile_storage.py:47-66`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/tests/server/openclaw/storage/test_aiofile_storage.py#L47-L66)
   共 6 处 `AioFileStorage._validate_key(...)`，改签名后这些用例会失败（需改为
   `storage._validate_key(...)` 或改测 `add()` 的行为）。生产代码里没有其他调用点。

2. （可选加固，针对无上界问题）把派生 key 的长度压到有界。注意**不能只改
   `_manager._session_storage_key`**：file 后端的 `session:` key 直接承担路径职责
   （[`_aiofile_storage.py:213`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/storage/_aiofile_storage.py#L213)
   对 `session:` 前缀直接 `Path(unquote(key[len("session:"):]))`），所以哈希必须落在
   **真实文件名**上，即从 `ClawSessionService._get_session_path`
   （[`_claw_session_service.py:193-195`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/session_memory/_claw_session_service.py#L193-L195)）
   侧收紧。由于读写两侧都经由同一 `_get_session_path` → 同一 `_session_storage_key`，
   只要映射是确定性的，读写天然一致。

3. （和本 issue 核心问题无关，但建议）让失败更显眼：
   [`claw.py:460-467`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/claw.py#L460-L467)
   目前只记 warning 后继续，持久化路径彻底坏掉时非常容易被忽略。

## 应用侧绕行方案（不修改 SDK）

具体做法：继承 `ClawSessionService`（`trpc_agent_sdk.server.openclaw.session_memory.ClawSessionService`）
并覆写 `_get_session_path(self, save_key: str) -> Path`，把文件名换成定长哈希；再让
`ClawApplication` 用上这个子类。

```python
import hashlib
from pathlib import Path

from trpc_agent_sdk.server.openclaw import claw as claw_mod
from trpc_agent_sdk.server.openclaw.session_memory import ClawSessionService


class HashedClawSessionService(ClawSessionService):
    """把 session 文件名换成定长哈希，使存储 key 不再随 id / workspace 深度增长。"""

    def _get_session_path(self, save_key: str) -> Path:
        digest = hashlib.sha256(save_key.encode("utf-8")).hexdigest()[:16]
        return self.sessions_dir / f"{digest}.jsonl"


# ClawApplication.__init__ 内部按模块全局名构造 ClawSessionService（见 claw.py 的
# :75 导入、:152 构造），且该实例在 __init__ 期间就被传给 Runner(:174) /
# worker_runner(:181) / command_handler(:206)，构造完再替换 self.session_service
# 并不干净 —— 因此在实例化 ClawApplication 之前替换模块全局名。
claw_mod.ClawSessionService = HashedClawSessionService

app = claw_mod.ClawApplication()
```

上面提到的构造与接线位置（`1aa44cf` permalink）：
[`claw.py:75`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/claw.py#L75)、
[`:152`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/claw.py#L152)、
[`:174`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/claw.py#L174)、
[`:181`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/claw.py#L181)、
[`:206`](https://github.com/trpc-group/trpc-agent-python/blob/1aa44cf/trpc_agent_sdk/server/openclaw/claw.py#L206)。

效果：key 从 150 降到 **89**
（`8 + [35 + 10 + 16 + 6] + 14 = 89`），且不随 id 长度变化，仅随 workspace 深度线性增长。
`_get_legacy_session_path` 不覆写，旧的 `sessions/*.jsonl` 迁移逻辑不受影响。

如果维护者倾向上面的任一修复，我可以直接提 PR。
