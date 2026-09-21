"""
系统设置（按用户存库）。

每个账号有自己的一份配置，存在 ``user_settings``，所以「同一个部署里
不同用户可以用不同的 SMTP 服务器 / 不同域名」：

- ``smtp``            —— 该用户自己的发信通道；留空（host 为空）则回落到服务器 .env 的全局配置
- ``public_base_url`` —— 该用户回复链接的对外根地址（多域名/多站点时特别有用）
- ``test_recipients`` —— 该用户自己的测试收件人

写入需要网页登录会话（人操作）；Agent 只需要能发信，不需要改服务器设置。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import storage
from ..config import settings as app_settings
from ..responses import fail, ok
from ..security import Principal, current_principal, session_only
from ..services import mailer

router = APIRouter(prefix="/api/v1/settings", tags=["系统设置 Settings"])

SMTP_FIELDS = (
    "host",
    "port",
    "use_ssl",
    "starttls",
    "user",
    "password",
    "from_email",
    "from_name",
    "timeout",
    "max_retries",
)


class SmtpIn(BaseModel):
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    use_ssl: bool | None = None
    starttls: bool | None = None
    user: str | None = None
    password: str | None = Field(default=None, max_length=200)
    from_email: str | None = None
    from_name: str | None = None
    timeout: int | None = Field(default=None, ge=5, le=120)
    max_retries: int | None = Field(default=None, ge=0, le=5)


class SettingsUpdateRequest(BaseModel):
    smtp: SmtpIn | None = Field(default=None, description="传 null 表示清空，回落到服务器全局 SMTP")
    public_base_url: str | None = Field(default=None, max_length=200)
    test_recipients: str | None = Field(default=None, max_length=1000)


class SmtpTestRequest(BaseModel):
    smtp: SmtpIn | None = Field(default=None, description="留空则测试当前已保存的配置")


def _public_base() -> dict[str, Any]:
    base = mailer.default_smtp().public()
    return {
        "host": base["host"],
        "port": base["port"],
        "use_ssl": base["use_ssl"],
        "starttls": base["starttls"],
        "user": base["user"],
        "from_email": base["from_email"],
        "from_name": base["from_name"],
        "password_set": base["password_set"],
        "configured": base["configured"],
    }


@router.get("", summary="读取当前用户的设置（密码只回 password_set 标志）")
async def read_settings(request: Request, principal: Principal = Depends(current_principal)):
    if not ({"mail:send", "mail:read"} & set(principal.scopes)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=fail("insufficient_scope", "缺少权限：mail:send 或 mail:read"),
        )

    saved = storage.get_user_settings(principal.user_id)
    effective = mailer.smtp_for_user(principal.user_id)
    return ok(
        request,
        {
            "user_id": principal.user_id,
            "username": principal.username,
            # 该用户自己保存的 SMTP（未保存过则为空对象）
            "smtp": saved.get("smtp") or {},
            "smtp_configured": bool((saved.get("smtp") or {}).get("host")),
            # 实际生效的配置（已合并全局兜底，不含明文密码）
            "effective_smtp": effective.public(),
            "global_smtp": _public_base(),
            "public_base_url": saved.get("public_base_url") or "",
            "effective_public_base_url": mailer.base_url_for_user(principal.user_id, request),
            "global_public_base_url": app_settings.public_base_url,
            "test_recipients": saved.get("test_recipients") or "",
            "global_test_recipients": app_settings.test_recipient_list,
            "password_hint": "SMTP 密码仅存服务端数据库，接口只返回是否已设置；留空表示不修改",
        },
    )


@router.put("", summary="保存当前用户的设置")
async def update_settings(
    payload: SettingsUpdateRequest,
    request: Request,
    principal: Principal = Depends(session_only),
):
    user_id = principal.user_id
    current = storage.get_user_settings(user_id)
    data = dict(current)

    if payload.smtp is not None:
        raw = payload.smtp.model_dump()
        # 只有显式给出非空值才覆盖，避免前端不回填密码时把密码抹掉
        existing_smtp = dict(data.get("smtp") or {})
        patch = {k: v for k, v in raw.items() if k in SMTP_FIELDS and v is not None}
        if not patch.get("password"):
            patch.pop("password", None)
            if existing_smtp.get("password"):
                patch["password"] = existing_smtp["password"]
        merged = {**existing_smtp, **patch}
        # 换了主机就丢掉旧密码，避免把 A 服务商的密码带给 B 服务商
        if patch.get("host") and patch["host"] != existing_smtp.get("host"):
            if not raw.get("password"):
                merged.pop("password", None)
        data["smtp"] = {k: v for k, v in merged.items() if v not in (None, "")}

    if payload.public_base_url is not None:
        value = payload.public_base_url.strip()
        if value and not value.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=fail("invalid_base_url", "对外地址需以 http:// 或 https:// 开头"),
            )
        data["public_base_url"] = value.rstrip("/")

    if payload.test_recipients is not None:
        data["test_recipients"] = payload.test_recipients.strip()

    storage.save_user_settings(user_id, data)
    effective = mailer.smtp_for_user(user_id)
    return ok(
        request,
        {
            "saved": True,
            "smtp": storage.get_user_settings(user_id).get("smtp") or {},
            "effective_smtp": effective.public(),
            "public_base_url": storage.get_user_settings(user_id).get("public_base_url") or "",
            "effective_public_base_url": mailer.base_url_for_user(user_id, request),
        },
    )


@router.post("/smtp-test", summary="测试 SMTP 连通性（可先测未保存的配置）")
async def smtp_test(
    payload: SmtpTestRequest,
    request: Request,
    principal: Principal = Depends(session_only),
):
    saved = mailer.smtp_for_user(principal.user_id)
    if payload.smtp is None:
        return ok(request, mailer.check_connection(saved))

    patch = {
        k: v
        for k, v in payload.smtp.model_dump().items()
        if k in SMTP_FIELDS and v is not None
    }
    if not patch.get("password"):
        patch["password"] = saved.password
    candidate = mailer.smtp_from_dict(patch, saved)
    return ok(request, mailer.check_connection(candidate))
