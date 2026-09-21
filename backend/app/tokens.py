"""
回复链接令牌：无状态 HMAC 签名，让用户点开链接即可回信，无需登录。

令牌只承载「哪个任务 + 第几版 + 何时过期」，并用服务端密钥签名：
- 改动任何一位都会导致签名校验失败；
- 任务侧 `token_version` 自增即可让旧链接立即失效（轮换）；
- 密钥优先取 .env，其次从 data/reply_secret.txt 读取，都没有就生成并落盘，
  保证服务重启后已发出的链接依然可用。
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


# ------------------------------------------------------------------ secret
_cached_secret: str | None = None


def get_secret() -> str:
    global _cached_secret
    if _cached_secret:
        return _cached_secret
    if settings.reply_token_secret:
        _cached_secret = settings.reply_token_secret
        return _cached_secret

    path = Path(settings.db_path).parent / "reply_secret.txt"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            _cached_secret = value
            return value

    value = secrets.token_urlsafe(48)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
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


# ------------------------------------------------------------------ api
def issue_token(task_id: str, version: int = 1, ttl_days: int | None = None) -> tuple[str, datetime]:
    days = ttl_days if ttl_days is not None else settings.reply_token_ttl_days
    expires = datetime.now(timezone.utc) + timedelta(days=max(1, days))
    payload = {
        "t": task_id,
        "v": version,
        "e": int(expires.timestamp()),
        "n": secrets.token_hex(4),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body)}", expires


def verify_token(token: str) -> TokenPayload:
    if not token or "." not in token:
        raise TokenError("invalid_token", "回复链接格式不正确")
    body, _, signature = token.partition(".")
    if not hmac.compare_digest(signature, _sign(body)):
        raise TokenError("invalid_signature", "回复链接校验失败（可能已被修改或并非本站签发）")
    try:
        payload = json.loads(_b64d(body))
    except Exception as exc:  # noqa: BLE001
        raise TokenError("invalid_token", f"回复链接无法解析：{exc}") from exc

    expires_at = int(payload.get("e") or 0)
    if time.time() > expires_at:
        raise TokenError("token_expired", "回复链接已过期，请让 Agent 重新发送邮件")
    return TokenPayload(
        task_id=str(payload.get("t") or ""),
        version=int(payload.get("v") or 0),
        expires_at=expires_at,
    )


def build_reply_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/reply/{token}"
