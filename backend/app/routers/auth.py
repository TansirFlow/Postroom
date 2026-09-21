"""
登录 / 会话 / 改密（网页端账号体系）。

会话令牌无状态（HMAC 签名），因此「登出」只是前端丢弃令牌；
需要真正吊销时用 ``session_version`` 自增：改密码、管理员重置密码、
调用 ``/auth/logout-all`` 都会让该用户所有已发出的令牌立即失效。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from .. import passwords, ratelimit, storage, tokens
from ..config import settings
from ..responses import fail, ok
from ..security import Principal, current_principal, session_only

router = APIRouter(prefix="/api/v1/auth", tags=["登录 Auth"])


# ------------------------------------------------------------------ models
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=8, max_length=200)


# ------------------------------------------------------------------ helpers
def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=max(1, settings.session_ttl_hours) * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.env.lower() in ("prod", "production"),
        path="/",
    )


def _issue(request: Request, response: Response, user: dict) -> dict:
    token, expires = tokens.issue_session(user["id"], int(user["session_version"]))
    _set_cookie(response, token)
    return {
        "token": token,
        "token_type": "Bearer",
        "expires_at": expires.isoformat(timespec="seconds"),
        "expires_in": max(1, settings.session_ttl_hours) * 3600,
        "user": _public_user(user),
    }


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"] or user["username"],
        "role": user["role"],
        "is_admin": user["role"] == "admin",
        "must_change_password": bool(user.get("must_change_password")),
        "last_login_at": user.get("last_login_at"),
        "created_at": user.get("created_at"),
    }


# ------------------------------------------------------------------ routes
@router.post("/login", summary="账号密码登录，返回会话令牌")
async def login(payload: LoginRequest, request: Request, response: Response):
    username = payload.username.strip()
    client_ip = request.client.host if request.client else "unknown"

    allowed, used, _ = ratelimit.hit(
        f"login:{username.lower()}:{client_ip}", limit=settings.login_rate_limit_per_hour
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=fail(
                "login_rate_limited",
                f"登录尝试过于频繁，每小时最多 {settings.login_rate_limit_per_hour} 次，请稍后再试",
                used=used,
            ),
        )

    user = storage.get_user_by_username(username, include_secret=True)
    # 统一错误信息，避免暴露「用户名是否存在」
    if not user or not passwords.verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=fail("invalid_credentials", "用户名或密码不正确"),
        )
    if not user["enabled"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=fail("account_disabled", "该账号已被停用，请联系管理员"),
        )

    if passwords.needs_rehash(user["password_hash"]):
        storage.update_user(user["id"], password_hash=passwords.hash_password(payload.password))

    storage.touch_user_login(user["id"])
    user = storage.get_user(user["id"], include_secret=True) or user
    return ok(request, _issue(request, response, user))


@router.get("/me", summary="当前登录身份（会话或 API Key）")
async def me(request: Request, principal: Principal = Depends(current_principal)):
    data = principal.to_dict()
    if principal.user_id:
        user = storage.get_user(principal.user_id)
        if user:
            data["user"] = _public_user(user)
    return ok(request, data)


@router.post("/logout", summary="退出登录（清除 Cookie；令牌本身无状态）")
async def logout(request: Request, response: Response):
    response.delete_cookie(settings.session_cookie_name, path="/")
    return ok(request, {"logged_out": True, "note": "前端同时丢弃本地保存的会话令牌即可"})


@router.post("/logout-all", summary="退出全部设备（会话版本自增）")
async def logout_all(
    request: Request,
    response: Response,
    principal: Principal = Depends(session_only),
):
    version = storage.bump_session_version(principal.user_id)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return ok(
        request,
        {"user_id": principal.user_id, "session_version": version, "note": "该账号所有已登录设备均已失效"},
    )


@router.post("/password", summary="修改自己的密码（旧令牌全部失效，返回新令牌）")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(session_only),
):
    user = storage.get_user(principal.user_id, include_secret=True)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=fail("user_not_found", "账号不存在")
        )
    if not passwords.verify_password(payload.old_password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail("wrong_password", "当前密码不正确"),
        )
    try:
        passwords.check_policy(payload.new_password)
    except passwords.PasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail("weak_password", str(exc)),
        ) from exc

    storage.update_user(
        principal.user_id,
        password_hash=passwords.hash_password(payload.new_password),
        must_change_password=False,
    )
    version = storage.bump_session_version(principal.user_id)
    user = storage.get_user(principal.user_id, include_secret=True) or user
    user["session_version"] = version or int(user["session_version"])
    storage.touch_user_login(principal.user_id)
    return ok(
        request,
        {
            **_issue(request, response, user),
            "note": "其它设备上的登录状态已全部失效",
        },
    )
