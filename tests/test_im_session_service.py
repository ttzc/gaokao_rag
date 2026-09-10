# tests/test_im_session_service.py
# ShortKeyClawSessionService 的哈希文件名回归测试（纯函数，不构造
# ClawConfig/模型、不碰网络）。核心是把 "key too long: 152 > 128" 这个
# bug 钉死：用真实 QQ openid 用例复算 StorageManager 的 key 派生逻辑
# （_manager.py:279 "session:" + quote(path, safe="")），断言哈希短名
# 后 ≤ AioFileStorage 的 128 上限，而旧命名确实超限（证明修复必要）。

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

from nanobot.utils.helpers import safe_filename

from src.im.session_service import hashed_session_filename

# AioFileStorage._validate_key 的硬上限（_aiofile_storage.py:181-185）
_MAX_KEY_LENGTH = 128

# 代表性默认 workspace（与真实默认 ~/.trpc_claw/workspace 同量级路径长度）：
# 超限断言用它而非 Path.home()，避免翻转依赖运行环境的 home 路径长度
_REPRESENTATIVE_WS = Path("C:/Users/student/.trpc_claw/workspace")

# 日志里的真实用例：app=gaokao_rag，QQ openid 32 位，session 带 "qq:" 前缀
_OPENID = "837BC24E8CCF1D40A6A8C788E1A27DAE"
_SAVE_KEY = f"gaokao_rag/{_OPENID}/qq:{_OPENID}"  # make_memory_key 产物，79 字符


def _storage_key_len(workspace: Path, filename: str) -> int:
    """按 StorageManager._session_storage_key 的原式复算 key 长度。"""
    path = workspace / "sessions" / filename
    return len(f"session:{quote(str(path), safe='')}")


class TestSessionKeyLengthRegression:
    """key 超长 bug（152 > 128）的回归守卫。"""

    def test_hashed_name_key_within_limit(self) -> None:
        """哈希短名下，默认 workspace 与更深 workspace 的 key 都 ≤128。"""
        default_ws = Path.home() / ".trpc_claw" / "workspace"
        deeper_ws = default_ws / ("a" * 30)  # 预留余量：workspace 再深 30 字符
        name = hashed_session_filename(_SAVE_KEY)
        for ws in (default_ws, deeper_ws):
            assert _storage_key_len(ws, name) <= _MAX_KEY_LENGTH

    def test_old_naming_exceeds_limit(self) -> None:
        """旧命名（safe_filename 保留原长度）复现超限——钉住 bug 本身。

        用显式代表性路径而非 Path.home()：超限与否只取决于路径长度量级，
        固定常量使断言在任何机器上结果一致。
        """
        old_name = f"{safe_filename(_SAVE_KEY.replace(':', '_'))}.jsonl"
        assert _storage_key_len(_REPRESENTATIVE_WS, old_name) > _MAX_KEY_LENGTH


class TestHashedSessionFilename:
    """hashed_session_filename 的确定性 / 区分度 / 字符安全。"""

    def test_deterministic(self) -> None:
        """同输入同输出（跨进程稳定，sha256 无随机盐）。"""
        assert hashed_session_filename(_SAVE_KEY) == hashed_session_filename(_SAVE_KEY)
        expected = hashlib.sha256(_SAVE_KEY.encode("utf-8")).hexdigest()[:16] + ".jsonl"
        assert hashed_session_filename(_SAVE_KEY) == expected

    def test_distinguishes_different_keys(self) -> None:
        """不同 save_key → 不同文件名（openid 不同 / session 不同均区分）。"""
        keys = {
            _SAVE_KEY,
            f"gaokao_rag/{_OPENID.lower()}/qq:{_OPENID.lower()}",
            f"gaokao_rag/{_OPENID}/qq:other-session",
            f"other_app/{_OPENID}/qq:{_OPENID}",
        }
        names = {hashed_session_filename(k) for k in keys}
        assert len(names) == len(keys)

    def test_filename_is_path_safe(self) -> None:
        """文件名不含路径分隔符 / 冒号等不安全字符（quote 后无转义膨胀）。"""
        name = hashed_session_filename(_SAVE_KEY)
        assert set(name) <= set("0123456789abcdef.jsonl")
        for unsafe in ("/", "\\", ":", " ", ".."):
            assert unsafe not in name
