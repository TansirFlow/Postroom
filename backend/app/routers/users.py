"""
用户管理（仅管理员）。账号只能由管理员创建 —— 登录页没有注册入口。

安全约束：
- 不能停用 / 删除自己（避免把自己锁在门外）；
- 不能删除、停用或降级「最后一个启用的管理员」；
- 删除账号会连带清掉该账号的密钥、发信记录、任务与会话消息（不可恢复）。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import passwords, storage
from ..responses import fail, ok
from ..security import Principal, admin_only

router = APIRouter(prefix="/api/v1/users", tags=["用户管理 Users"])


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=40, pattern=r"^[A-Za-z0-9._@-]+$")
    display_name: str = Field(default="", max_length=60)
    password: str | None = Field(
        default=None, max_length=200, description="留空则自动生成强随机密码并在响应里返回一次"
    )
    role: Literal["admin", "user"] = "user"
    note: str = Field(default="", max_length=200)
    must_change_password: bool = True


class UserPatchRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=60)
    role: Literal["admin", "user"] | None = None
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=200)


class PasswordResetRequest(BaseModel):
    password: str | None = Field(
        default=None, max_length=200, description="留空则自动生成强随机密码并在响应里返回一次"
    )


def _load_or_404(user_id: str) -> dict:
    user = storage.get_user(user_id, include_secret=True)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=fail("user_not_found", f"找不到用户 {user_id}")
        )
    return user


def _guard_last_admin(user: dict, action: str) -> None:
    if user["role"] == "admin" and user["enabled"] and storage.count_users_by_role("admin") <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail("last_admin", f"这是最后一个启用中的管理员，不能{action}"),
        )


@router.get("", summary="用户列表")
async def list_users(request: Request, principal: Principal = Depends(admin_only)):
    users = storage.list_users()
    for u in users:
        u["is_self"] = u["id"] == principal.user_id
    return ok(
        request,
        {
            "items": users,
            "total": len(users),
            "password_policy": {
                "min_length": passwords.MIN_PASSWORD_LENGTH,
                "rule": "至少 8 位，且包含字母/数字/符号中的至少两类",
            },
        },
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="新建账号（明文密码仅返回一次）")
async def create_user(
    payload: UserCreateRequest, request: Request, principal: Principal = Depends(admin_only)
):
    username = payload.username.strip()
    if storage.get_user_by_username(username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=fail("username_taken", f"用户名 {username} 已存在"),
        )

    initial = payload.password or ""
    generated = False
    if initial:
        try:
            passwords.check_policy(initial)
        except passwords.PasswordError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=fail("weak_password", str(exc))
            ) from exc
    else:
        initial = passwords.generate_password()
        generated = True

    user_id = storage.create_user(
        username=username,
        password_hash=passwords.hash_password(initial),
        display_name=payload.display_name.strip() or username,
        role=payload.role,
        note=payload.note,
        must_change_password=payload.must_change_password,
    )
    user = storage.get_user(user_id)
    return ok(
        request,
        {
            "user": user,
            "initial_password": initial,
            "password_generated": generated,
            "warning": "明文密码仅此一次返回，请转交该用户后立即修改。",
        },
    )


@router.patch("/{user_id}", summary="修改账号（显示名 / 角色 / 启停）")
async def patch_user(
    user_id: str,
    payload: UserPatchRequest,
    request: Request,
    principal: Principal = Depends(admin_only),
):
    user = _load_or_404(user_id)

    if payload.enabled is False:
        if user_id == principal.user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=fail("cannot_disable_self", "不能停用当前登录的账号"),
            )
        _guard_last_admin(user, "停用")

    if payload.role == "user" and user["role"] == "admin":
        _guard_last_admin(user, "降级")

    updates = {
        "display_name": payload.display_name.strip() if payload.display_name is not None else None,
        "role": payload.role,
        "enabled": payload.enabled,
        "note": payload.note,
    }
    changed = storage.update_user(user_id, **{k: v for k, v in updates.items() if v is not None})

    # 停用或降级后，让对方已发出的会话令牌立即失效
    if (payload.enabled is False) or (payload.role == "user" and user["role"] == "admin"):
        storage.bump_session_version(user_id)

    updated = storage.get_user(user_id)
    if updated:
        updated["is_self"] = user_id == principal.user_id
    return ok(request, {"user": updated, "updated": changed})


@router.post("/{user_id}/password", summary="重置密码（旧会话全部失效，新密码仅返回一次）")
async def reset_password(
    user_id: str,
    payload: PasswordResetRequest,
    request: Request,
    principal: Principal = Depends(admin_only),
):
    user = _load_or_404(user_id)
    new_password = payload.password or ""
    generated = False
    if new_password:
        try:
            passwords.check_policy(new_password)
        except passwords.PasswordError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=fail("weak_password", str(exc))
            ) from exc
    else:
        new_password = passwords.generate_password()
        generated = True

    storage.update_user(
        user_id,
        password_hash=passwords.hash_password(new_password),
        must_change_password=True,
    )
    version = storage.bump_session_version(user_id)
    return ok(
        request,
        {
            "user_id": user_id,
            "username": user["username"],
            "new_password": new_password,
            "password_generated": generated,
            "session_version": version,
            "warning": "明文密码仅此一次返回；该用户所有已登录设备已失效。",
        },
    )


@router.delete("/{user_id}", summary="删除账号（连带其密钥、发信记录与任务，不可恢复）")
async def delete_user(
    user_id: str, request: Request, principal: Principal = Depends(admin_only)
):
    user = _load_or_404(user_id)
    if user_id == principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail("cannot_delete_self", "不能删除当前登录的账号"),
        )
    _guard_last_admin(user, "删除")

    removed = storage.delete_user(user_id)
    return ok(
        request,
        {
            "user_id": user_id,
            "username": user["username"],
            "deleted": True,
            "removed": removed,
            "note": "该账号的 API 密钥、发信记录、任务与会话消息已一并删除。",
        },
    )
