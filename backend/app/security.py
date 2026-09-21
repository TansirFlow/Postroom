"""
鉴权：支持两种凭证，任意一种通过即可。

1. **API Key**（Agent 用）—— ``X-API-Key`` 头、``Authorization: Bearer sk-xxx``
   或 ``?api_key=`` 查询参数；数据库保存 sha256 摘要用于鉴权，同时保存经站点签名密钥加密的
   原文，只有本账号的 ``keys:manage`` 用户可以通过控制台复制。
   每个密钥归属于一个用户，故数据读写天然按用户隔离。
2. **登录会话**（人在网页用）—— ``Authorization: Bearer <session-token>``
   或 HttpOnly Cookie；无状态 HMAC 签名令牌，见 ``tokens.issue_session``。

两类凭证都不需要服务端 session 表：会话令牌里带 ``session_version``，
用户改密码 / 被重置密码时该版本自增，旧令牌立即失效。

权限模型：``Principal.scopes`` 是唯一判据（不做管理员直通），
管理员只是默认拿全部权限，避免「一个只有 mail:send 的 Agent 密钥」越权。
"""
from __future__ import annotations

import hashlib
import hmac
import base64
import secrets
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, Query, Request, status

from . import storage, tokens
from .config import settings

SCOPES = {
    "mail:send": "发送邮件",
    "mail:read": "查看发送记录",
    "tasks:write": "创建任务、向任务线程发消息",
    "tasks:read": "查看任务与会话内容",
    "keys:manage": "管理 API 密钥",
    "users:manage": "管理用户账号（仅管理员）",
}
ALL_SCOPES = list(SCOPES.keys())
API_KEY_PREFIX = "sk-"
_KEY_SECRET_LABEL = b"postroom/api-key-secret/v1"


class AuthError(HTTPException):
    def __init__(self, code: str, message: str, status_code: int = status.HTTP_401_UNAUTHORIZED):
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_key(prefix: str = "sk-agent") -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


def _key_secret_material() -> bytes:
    """从站点签名密钥派生 API Key 原文的加密材料。"""
    return hmac.new(
        tokens.get_secret().encode("utf-8"), _KEY_SECRET_LABEL, hashlib.sha256
    ).digest()


def _xor_key_stream(data: bytes, nonce: bytes, material: bytes) -> bytes:
    output = bytearray()
    for counter in range((len(data) + 31) // 32):
        block = hmac.new(
            material,
            _KEY_SECRET_LABEL + b"/stream/" + nonce + counter.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        start = counter * 32
        output.extend(value ^ block[index] for index, value in enumerate(data[start : start + 32]))
    return bytes(output)


def seal_api_key(raw: str) -> str:
    """加密保存 API Key 原文；数据库不保存可直接使用的明文。"""
    material = _key_secret_material()
    nonce = secrets.token_bytes(16)
    cipher = _xor_key_stream(raw.encode("utf-8"), nonce, material)
    tag = hmac.new(material, _KEY_SECRET_LABEL + b"/tag/" + nonce + cipher, hashlib.sha256).digest()
    encoded = lambda value: base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
    return f"v1.{encoded(nonce)}.{encoded(cipher)}.{encoded(tag)}"


def unseal_api_key(sealed: str | None) -> str | None:
    """解密 API Key 原文；旧版本没有密文时返回 None。"""
    if not sealed:
        return None
    try:
        version, nonce_text, cipher_text, tag_text = sealed.split(".", 3)
        if version != "v1":
            return None
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        nonce, cipher, tag = decode(nonce_text), decode(cipher_text), decode(tag_text)
        material = _key_secret_material()
        expected = hmac.new(
            material, _KEY_SECRET_LABEL + b"/tag/" + nonce + cipher, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(tag, expected):
            return None
        return _xor_key_stream(cipher, nonce, material).decode("utf-8")
    except (ValueError, UnicodeDecodeError, TypeError):
        return None


@dataclass
class Principal:
    name: str
    user_id: str | None = None
    username: str | None = None
    display_name: str = ""
    role: str = "user"
    key_id: str | None = None
    scopes: list[str] = field(default_factory=list)
    via: str = "api_key"  # api_key | session | env

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_session(self) -> bool:
        return self.via == "session"

    @property
    def api_key_scope(self) -> str | None:
        """API Key 按密钥 ID 隔离；网页会话查看本账号的全部数据。"""
        if self.via == "api_key":
            return self.key_id or self.name
        return self.name if self.via == "env" else None

    @property
    def storage_owner(self) -> str:
        """写入任务/会话/日志时使用的稳定归属标识。"""
        return self.api_key_scope or self.name

    @property
    def inbox_owner(self) -> str:
        """收件箱水位的归属标识；API Key 使用内部 ID，网页会话使用用户名。"""
        return self.api_key_scope or self.name

    def require(self, scope: str) -> None:
        if scope in self.scopes:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "insufficient_scope", "message": f"缺少权限：{scope}"},
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "scopes": self.scopes,
            "is_admin": self.is_admin,
            "via": self.via,
        }


# ------------------------------------------------------------------ 内部
def _principal_from_api_key(raw: str) -> Principal | None:
    # 1) .env 里的根密钥（部署期兜底）：等同于首个管理员账号
    if settings.admin_api_key and hmac.compare_digest(raw, settings.admin_api_key):
        admin = storage.first_admin()
        return Principal(
            name="admin(env)",
            user_id=admin["id"] if admin else None,
            username=admin["username"] if admin else "admin",
            display_name=(admin["display_name"] if admin else "") or "管理员",
            role="admin",
            scopes=ALL_SCOPES,
            via="env",
        )

    # 2) 数据库里的密钥：连带取出其归属用户
    record = storage.find_api_key_by_hash(hash_key(raw))
    if not record:
        return None
    user = storage.get_user(record["user_id"], include_secret=False) if record.get("user_id") else None
    if record.get("user_id") and not user:
        return None
    if user and not user["enabled"]:
        return None
    storage.touch_api_key(record["id"])
    return Principal(
        name=record["name"],
        user_id=record.get("user_id"),
        username=user["username"] if user else None,
        display_name=(user["display_name"] if user else "") or record["name"],
        role=user["role"] if user else "user",
        key_id=record["id"],
        scopes=record["scopes"],
        via="api_key",
    )


def _principal_from_session(raw: str) -> Principal | None:
    try:
        payload = tokens.verify_session(raw)
    except tokens.TokenError:
        return None
    user = storage.get_user(payload.user_id, include_secret=True)
    if not user or not user["enabled"]:
        return None
    if int(user["session_version"]) != payload.session_version:
        return None
    return Principal(
        name=user["username"],
        user_id=user["id"],
        username=user["username"],
        display_name=user["display_name"] or user["username"],
        role=user["role"],
        scopes=ALL_SCOPES,
        via="session",
    )


def _candidates(
    x_api_key: str | None,
    authorization: str | None,
    api_key: str | None,
    x_session_token: str | None,
    cookie_token: str | None,
) -> list[str]:
    out: list[str] = []
    if x_api_key and x_api_key.strip():
        out.append(x_api_key.strip())
    if authorization and authorization.lower().startswith("bearer "):
        value = authorization[7:].strip()
        if value:
            out.append(value)
    if api_key and api_key.strip():
        out.append(api_key.strip())
    if x_session_token and x_session_token.strip():
        out.append(x_session_token.strip())
    if cookie_token and cookie_token.strip():
        out.append(cookie_token.strip())
    return out


# ------------------------------------------------------------------ 依赖
async def current_principal(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None, alias="X-Session-Token"),
    api_key: str | None = Query(default=None, description="备用：?api_key=xxx"),
) -> Principal:
    raw_list = _candidates(
        x_api_key,
        authorization,
        api_key,
        x_session_token,
        request.cookies.get(settings.session_cookie_name),
    )
    if not raw_list:
        raise AuthError("missing_credentials", "未登录：请在网页登录，或为 Agent 配置 API Key")

    for raw in raw_list:
        principal = None
        if raw.startswith(API_KEY_PREFIX):
            principal = _principal_from_api_key(raw)
        else:
            principal = _principal_from_session(raw)
            if principal is None:
                # 也允许把密钥放在 Authorization 头里
                principal = _principal_from_api_key(raw)
        if principal:
            request.state.principal = principal
            return principal

    raise AuthError("invalid_credentials", "凭证无效、已失效或账号已被停用，请重新登录")


async def session_only(principal: Principal = Depends(current_principal)) -> Principal:
    """必须来自网页登录会话（改自己的密码等操作）。Agent 的 API Key 不允许。"""
    if not principal.is_session:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "session_required", "message": "该操作需要在网页登录后进行"},
        )
    return principal


async def admin_only(principal: Principal = Depends(current_principal)) -> Principal:
    """用户管理入口：既要是管理员角色，也要具备 users:manage 权限。"""
    if not principal.is_admin or "users:manage" not in principal.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "admin_required", "message": "仅管理员可执行该操作"},
        )
    return principal
