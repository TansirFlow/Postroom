"""系统状态：健康检查、配置概览（不泄露密钥）。"""
from __future__ import annotations

import platform
import time

from fastapi import APIRouter, Depends, Request

from .. import storage
from ..config import settings
from ..responses import ok
from ..security import Principal, current_principal
from ..services import mailer

router = APIRouter(prefix="/api/v1", tags=["系统 System"])

_STARTED_AT = time.time()


def _smtp_ready() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


@router.get("/health", summary="健康检查（无需鉴权）")
async def health(request: Request):
    return ok(
        request,
        {
            "status": "ok",
            "app": settings.app_name,
            "version": settings.app_version,
            "env": settings.env,
            "uptime_seconds": int(time.time() - _STARTED_AT),
            "smtp_configured": _smtp_ready(),
            "python": platform.python_version(),
        },
    )


@router.get("/overview", summary="控制台概览数据")
async def overview(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:read")
    stats = storage.mail_stats()
    return ok(
        request,
        {
            "app": {
                "name": settings.app_name,
                "version": settings.app_version,
                "env": settings.env,
                "uptime_seconds": int(time.time() - _STARTED_AT),
            },
            "smtp": {
                "host": settings.smtp_host,
                "port": settings.smtp_port,
                "ssl": settings.smtp_use_ssl,
                "user": settings.smtp_user,
                "from_email": settings.from_email,
                "from_name": settings.smtp_from_name,
                "configured": _smtp_ready(),
                "password_set": bool(settings.smtp_password),
            },
            "mail_stats": stats,
            "task_stats": storage.task_stats(),
            "keys": {"count": storage.count_api_keys(), "rate_limit_per_hour": settings.rate_limit_per_hour},
            "test_recipients": settings.test_recipient_list,
            "principal": {"name": principal.name, "scopes": principal.scopes, "is_admin": principal.is_admin},
            "capabilities": [
                "mail.send",
                "mail.logs",
                "mail.templates",
                "tasks.create",
                "tasks.thread",
                "tasks.reply_link",
                "keys.manage",
            ],
        },
    )


@router.get("/whoami", summary="校验当前 API Key 与权限")
async def whoami(request: Request, principal: Principal = Depends(current_principal)):
    return ok(
        request,
        {
            "name": principal.name,
            "key_id": principal.key_id,
            "scopes": principal.scopes,
            "is_admin": principal.is_admin,
        },
    )


@router.get("/smtp-check", summary="SMTP 连通性检查")
async def smtp_check(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:send")
    return ok(request, mailer.check_connection())
