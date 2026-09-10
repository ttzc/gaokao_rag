# src/im/session_service.py
# QQ 入口 session 持久化修复：短文件名版 ClawSessionService。
#
# 【问题】trpc-claw 默认命名把 session 快照落盘路径写成
#   {workspace}/sessions/{safe(app/user/session)}.jsonl
# 而 StorageManager 用该路径派生存储 key（_manager.py:279）：
#   "session:" + quote(str(path), safe="")
# QQ 场景下 openid 32 位 + "qq:" 前缀使 save_key 达 79 字符，quote 把
# "/"、":" 各转义为 3 字节，加上默认 workspace（~/.trpc_claw/workspace）
# 的绝对路径前缀，key 实测 152 字符 > AioFileStorage 上限 128
# （_aiofile_storage.py:181-185 的 _validate_key 抛错）——日志表现为
# "Failed to persist session after turn ... key too long: 152 > 128"，
# session 快照从未落盘。
#
# 【为什么哈希】文件名取 save_key 的 sha256 前 16 位十六进制（恒定 21
# 字符含 .jsonl），十六进制无字符需 quote 转义，绝对路径 ~98 字符 +
# "session:" 前缀仍 ≤128，且 workspace 更深也留有转义余量。
#
# 【为什么不用配置项】上游 FileStorageConfig.max_key_length（默认 255）
# 是死配置——__init__ 读了进 self._max_key_length，但 _validate_key 是
# staticmethod 只用模块常量 DEFAULT_MAX_KEY_LENGTH，调配置无效。已单独
# 给上游提 issue；本地按"MVP 免补丁"决策不改包内文件，只在项目侧覆写。
#
# 【读写一致性】create_session / get_session / update_session /
# _maybe_migrate 全部经 _get_session_path 单点取路径，覆写这一处即闭合。
# _get_legacy_session_path 刻意不覆写：它在旧目录按"旧命名"找文件做迁移，
# 保持原名才符合迁移语义（迁移目标由调用方传入的新路径决定）。
#
# 【代价】文件名不可读（16 位哈希无法肉眼对应 openid）；排障时日志里
# app/user/session 与 save_key 仍完整可见，不影响定位。

from __future__ import annotations

import hashlib
from pathlib import Path

from trpc_agent_sdk.server.openclaw.session_memory import ClawSessionService


def hashed_session_filename(save_key: str) -> str:
    """把 save_key 映射为短的确定性 JSONL 文件名。

    Args:
        save_key: make_memory_key(app, user, session) 的产物，如
            "gaokao_rag/{openid}/qq:{openid}"

    Returns:
        sha256 前 16 位十六进制 + ".jsonl"（21 字符，无需 quote 转义）
    """
    digest = hashlib.sha256(save_key.encode("utf-8")).hexdigest()
    return f"{digest[:16]}.jsonl"


class ShortKeyClawSessionService(ClawSessionService):
    """session 文件名哈希化的 ClawSessionService（修复 key 超长落盘失败）。

    只覆写 _get_session_path；构造签名与父类一致
    （config / summarizer_manager / session_config），不新增参数。
    """

    def _get_session_path(self, save_key: str) -> Path:
        """sessions 目录下的落盘路径，文件名用哈希短名（见模块头注释）。"""
        return self.sessions_dir / hashed_session_filename(save_key)
