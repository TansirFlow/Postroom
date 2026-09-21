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
            "smtp_configured": mailer.default_smtp().configured,
            "python": platform.python_version(),
        },
    )


@router.get("/site", summary="公开站点信息（无需鉴权）")
async def site(request: Request):
    """前端页脚 / 品牌 / 登录页信息。只暴露与站点展示相关、可公开的字段。"""
    return ok(
        request,
        {
            "app": settings.app_name,
            "version": settings.app_version,
            "icp_license": settings.icp_license,
            "icp_license_url": settings.icp_license_url,
            "multi_user": True,
            "login_required": True,
        },
    )


@router.get("/overview", summary="控制台概览数据")
async def overview(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:read")
    smtp = mailer.smtp_for_user(principal.user_id)
    stats = storage.mail_stats(user_id=principal.user_id)
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
                "host": smtp.host,
                "port": smtp.port,
                "ssl": smtp.use_ssl,
                "starttls": smtp.starttls,
                "user": smtp.user,
                "from_email": smtp.from_addr,
                "from_name": smtp.from_name,
                "configured": smtp.configured,
                "password_set": bool(smtp.password),
                # 用服务器全局配置，还是用户自己的
                "source": smtp.source,
                "own_config": bool((storage.get_user_settings(principal.user_id) or {}).get("smtp")),
            },
            "public_base_url": mailer.base_url_for_user(principal.user_id, request),
            "mail_stats": stats,
            "task_stats": storage.task_stats(user_id=principal.user_id),
            "conversation_stats": storage.conversation_stats(user_id=principal.user_id),
            "inbox": {
                "watermark": storage.get_inbox_watermark(principal.name, principal.user_id),
                "latest_seq": storage.current_seq(),
            },
            "keys": {
                "count": storage.count_api_keys(user_id=principal.user_id),
                "rate_limit_per_hour": settings.rate_limit_per_hour,
            },
            "test_recipients": (
                [
                    o.strip()
                    for o in str(
                        (storage.get_user_settings(principal.user_id) or {}).get("test_recipients") or ""
                    ).split(",")
                    if o.strip()
                ]
                or settings.test_recipient_list
            ),
            "principal": principal.to_dict(),
            "capabilities": [
                "mail.send",
                "mail.logs",
                "mail.templates",
                "conversations.ensure",
                "inbox.pull",
                "inbox.ack",
                "tasks.create",
                "tasks.thread",
                "tasks.reply_link",
                "keys.manage",
                "settings.manage",
                "users.manage" if principal.is_admin else None,
            ],
        },
    )


@router.get("/whoami", summary="校验当前凭证与权限")
async def whoami(request: Request, principal: Principal = Depends(current_principal)):
    return ok(request, principal.to_dict())


@router.get("/smtp-check", summary="SMTP 连通性检查（用当前账号生效的配置）")
async def smtp_check(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:send")
    return ok(request, mailer.check_connection(mailer.smtp_for_user(principal.user_id)))
