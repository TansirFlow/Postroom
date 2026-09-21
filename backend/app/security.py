"""
鉴权：API Key（X-API-Key / Authorization: Bearer）。
密钥只存 sha256 摘要，明文仅在创建时返回一次。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from fastapi import Header, HTTPException, Query, Request, status

from . import storage
from .config import settings

SCOPES = {
    "mail:send": "发送邮件",
    "mail:read": "查看发送记录",
    "tasks:write": "创建任务、向任务线程发消息",
    "tasks:read": "查看任务与会话内容",
    "keys:manage": "管理 API 密钥",
}
ALL_SCOPES = list(SCOPES.keys())


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_key(prefix: str = "sk-agent") -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


@dataclass
class Principal:
    name: str
    key_id: str | None = None
    scopes: list[str] = field(default_factory=list)
    is_admin: bool = False

    def require(self, scope: str) -> None:
        if self.is_admin or scope in self.scopes:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "insufficient_scope", "message": f"缺少权限：{scope}"},
        )


def _extract_key(
    x_api_key: str | None,
    authorization: str | None,
    api_key: str | None,
) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if api_key:
        return api_key.strip()
    return None


async def current_principal(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
    api_key: str | None = Query(default=None, description="备用：?api_key=xxx"),
) -> Principal:
    raw = _extract_key(x_api_key, authorization, api_key)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "missing_api_key", "message": "缺少 API Key（X-API-Key 头）"},
        )

    if settings.admin_api_key and hmac.compare_digest(raw, settings.admin_api_key):
        principal = Principal(name="admin(env)", scopes=ALL_SCOPES, is_admin=True)
    else:
        record = storage.find_api_key_by_hash(hash_key(raw))
        if not record:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "invalid_api_key", "message": "API Key 无效或已禁用"},
            )
        principal = Principal(
            name=record["name"],
            key_id=record["id"],
            scopes=record["scopes"],
            is_admin="keys:manage" in record["scopes"],
        )
        storage.touch_api_key(record["id"])

    request.state.principal = principal
    return principal
