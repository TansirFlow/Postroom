"""API 密钥管理（需要 keys:manage 权限；密钥按用户隔离）。"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .. import storage
from ..responses import fail, ok
from ..schemas import KeyCreateRequest, KeySecretImportRequest
from ..security import (
    ALL_SCOPES,
    SCOPES,
    Principal,
    current_principal,
    generate_key,
    hash_key,
    seal_api_key,
    unseal_api_key,
)

router = APIRouter(prefix="/api/v1/keys", tags=["密钥 API Keys"])


def _visible_scopes(principal: Principal) -> list[str]:
    """非管理员不能给自己发 users:manage。"""
    if principal.is_admin:
        return list(ALL_SCOPES)
    return [s for s in ALL_SCOPES if s != "users:manage"]


@router.get("", summary="密钥列表（不含明文，仅本账号）")
async def list_keys(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("keys:manage")
    allowed = _visible_scopes(principal)
    return ok(
        request,
        {
            "items": storage.list_api_keys(user_id=principal.user_id),
            "available_scopes": [
                {"scope": k, "label": v} for k, v in SCOPES.items() if k in allowed
            ],
        },
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="创建密钥（支持后续复制）")
async def create_key(
    payload: KeyCreateRequest, request: Request, principal: Principal = Depends(current_principal)
):
    principal.require("keys:manage")
    allowed = _visible_scopes(principal)
    invalid = [s for s in payload.scopes if s not in ALL_SCOPES]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail("invalid_scope", f"非法权限：{invalid}", available=ALL_SCOPES),
        )
    forbidden = [s for s in payload.scopes if s not in allowed]
    if forbidden:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=fail("scope_not_allowed", f"你没有权限发放这些权限：{forbidden}"),
        )
    if not payload.scopes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail("empty_scope", "至少需要一个权限"),
        )

    raw = generate_key()
    key_id = storage.insert_api_key(
        name=payload.name,
        key_hash=hash_key(raw),
        prefix=raw[:12],
        scopes=payload.scopes,
        note=payload.note,
        user_id=principal.user_id,
        secret_ciphertext=seal_api_key(raw),
    )
    return ok(
        request,
        {
            "id": key_id,
            "name": payload.name,
            "api_key": raw,
            "scopes": payload.scopes,
            "created_at": storage.now_iso(),
            "warning": "密钥原文已加密保存，之后可在密钥列表中复制。请勿分享给无关人员。",
        },
    )


@router.patch("/{key_id}", summary="启用 / 停用密钥")
async def toggle_key(
    key_id: str,
    enabled: bool,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    principal.require("keys:manage")
    if not storage.set_api_key_enabled(
        key_id, enabled, user_id=principal.user_id, scoped=True
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=fail("key_not_found", f"找不到密钥 {key_id}")
        )
    return ok(request, {"id": key_id, "enabled": enabled})


@router.get("/{key_id}/secret", summary="复制现有密钥原文（仅本账号）")
async def get_key_secret(
    key_id: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(current_principal),
):
    principal.require("keys:manage")
    response.headers["Cache-Control"] = "no-store"
    current = storage.get_api_key(key_id, user_id=principal.user_id, scoped=True)
    sealed = storage.get_api_key_secret(key_id, user_id=principal.user_id, scoped=True)
    raw = unseal_api_key(sealed)
    if not current:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=fail("key_not_found", f"找不到密钥 {key_id}"),
        )
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=fail(
                "key_secret_unavailable",
                "该密钥由旧版本创建，服务器没有保存可恢复的原文；哈希无法反推原密钥。",
            ),
        )
    return ok(
        request,
        {
            "id": key_id,
            "name": current["name"],
            "api_key": raw,
            "scopes": current["scopes"],
            "warning": "请勿把密钥写入公开日志、代码仓库或分享给无关人员。",
        },
    )


@router.post("/{key_id}/secret", summary="保存已有密钥原文（不改变密钥）")
async def import_key_secret(
    key_id: str,
    payload: KeySecretImportRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    principal.require("keys:manage")
    current = storage.get_api_key(key_id, user_id=principal.user_id, scoped=True)
    if not current:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=fail("key_not_found", f"找不到密钥 {key_id}"),
        )
    if not hmac.compare_digest(hash_key(payload.api_key), current["key_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail("key_secret_mismatch", "提供的原文与该密钥不匹配，未保存任何内容。"),
        )
    storage.set_api_key_secret(key_id, seal_api_key(payload.api_key))
    return ok(request, {"id": key_id, "saved": True})


@router.delete("/{key_id}", summary="删除密钥")
async def delete_key(key_id: str, request: Request, principal: Principal = Depends(current_principal)):
    principal.require("keys:manage")
    if not storage.delete_api_key(key_id, user_id=principal.user_id, scoped=True):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=fail("key_not_found", f"找不到密钥 {key_id}")
        )
    return ok(request, {"id": key_id, "deleted": True})
