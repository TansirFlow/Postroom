"""
无状态 HMAC 签名令牌的编解码底座。

同一套密钥与算法承载两类令牌，用载荷里的 `k`（kind）字段区分：

- ``reply`` —— 免登录回复链接，载荷 ``{k,t,v,e,n}``
- ``sess``  —— 控制台登录会话，载荷 ``{k,u,s,e,n}``

两类令牌都只承载「指向哪条记录 + 何时过期」，用服务端密钥签名：

- 改动任何一位都会导致签名校验失败；
- 任务侧 ``token_version`` / 用户侧 ``session_version`` 自增即可让旧令牌立即失效（轮换）；
- 密钥优先取 .env，其次读 ``data/token_secret.txt``（兼容旧的 ``reply_secret.txt``），
  都没有就生成并落盘，保证服务重启后已发出的令牌依然可用。

历史兼容：早期签发的回复令牌没有 ``k`` 字段，一律按 ``reply`` 处理。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import settings

KIND_REPLY = "reply"
KIND_SESSION = "sess"


class TokenError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class TokenPayload:
    task_id: str
    version: int
    expires_at: int  # epoch seconds


@dataclass
class SessionPayload:
    user_id: str
    session_version: int
    expires_at: int  # epoch seconds


# ------------------------------------------------------------------ secret
_cached_secret: str | None = None
_SECRET_FILES = ("token_secret.txt", "reply_secret.txt")  # 后者为历史文件名


def get_secret() -> str:
    global _cached_secret
    if _cached_secret:
        return _cached_secret
    if settings.reply_token_secret:
        _cached_secret = settings.reply_token_secret
        return _cached_secret

    data_dir = Path(settings.db_path).parent
    for name in _SECRET_FILES:
        path = data_dir / name
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                _cached_secret = value
                return value

    value = secrets.token_urlsafe(48)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / _SECRET_FILES[0]).write_text(value, encoding="utf-8")
    _cached_secret = value
    return value


# ------------------------------------------------------------------ codec
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload: str) -> str:
    digest = hmac.new(get_secret().encode(), payload.encode(), hashlib.sha256).digest()
    return _b64e(digest)


def encode(payload: dict) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{body}.{_sign(body)}"


def decode(token: str, expect_kind: str) -> dict:
    """校验签名 + 类型 + 过期，返回载荷字典。任何异常都归一成 TokenError。"""
    if not token or "." not in token:
        raise TokenError("invalid_token", "令牌格式不正确")
    body, _, signature = token.partition(".")
    if not hmac.compare_digest(signature, _sign(body)):
        raise TokenError("invalid_signature", "令牌校验失败（可能已被修改或并非本站签发）")
    try:
        payload = json.loads(_b64d(body))
    except Exception as exc:  # noqa: BLE001
        raise TokenError("invalid_token", f"令牌无法解析：{exc}") from exc
    if not isinstance(payload, dict):
        raise TokenError("invalid_token", "令牌内容不合法")

    kind = payload.get("k") or KIND_REPLY  # 历史令牌默认按回复链接处理
    if kind != expect_kind:
        raise TokenError("invalid_token", "令牌用途不匹配")

    expires_at = int(payload.get("e") or 0)
    if time.time() > expires_at:
        label = "回复链接" if expect_kind == KIND_REPLY else "登录状态"
        raise TokenError("token_expired", f"{label}已过期，请重新获取")
    return payload


# ------------------------------------------------------------------ 回复链接
def issue_token(task_id: str, version: int = 1, ttl_days: int | None = None) -> tuple[str, datetime]:
    days = ttl_days if ttl_days is not None else settings.reply_token_ttl_days
    expires = datetime.now(timezone.utc) + timedelta(days=max(1, days))
    payload = {
        "k": KIND_REPLY,
        "t": task_id,
        "v": version,
        "e": int(expires.timestamp()),
        "n": secrets.token_hex(4),
    }
    return encode(payload), expires


def verify_token(token: str) -> TokenPayload:
    payload = decode(token, KIND_REPLY)
    return TokenPayload(
        task_id=str(payload.get("t") or ""),
        version=int(payload.get("v") or 0),
        expires_at=int(payload.get("e") or 0),
    )


# ------------------------------------------------------------------ 登录会话
def issue_session(
    user_id: str, session_version: int, ttl_hours: int | None = None
) -> tuple[str, datetime]:
    hours = ttl_hours if ttl_hours is not None else settings.session_ttl_hours
    expires = datetime.now(timezone.utc) + timedelta(hours=max(1, hours))
    payload = {
        "k": KIND_SESSION,
        "u": user_id,
        "s": session_version,
        "e": int(expires.timestamp()),
        "n": secrets.token_hex(4),
    }
    return encode(payload), expires


def verify_session(token: str) -> SessionPayload:
    payload = decode(token, KIND_SESSION)
    return SessionPayload(
        user_id=str(payload.get("u") or ""),
        session_version=int(payload.get("s") or 0),
        expires_at=int(payload.get("e") or 0),
    )


def build_reply_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/reply/{token}"
