"""
邮件能力路由 —— agent 主要调用的就是这一组接口。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .. import ratelimit, storage
from ..config import settings
from ..responses import fail, ok
from ..schemas import SendMailRequest
from ..security import Principal, current_principal
from ..services import mailer, replylink, templates

router = APIRouter(prefix="/api/v1/mail", tags=["邮件 Mail"])


def _preview(text: str | None) -> str | None:
    if not text or not settings.store_body_preview:
        return None
    return text[: settings.body_preview_chars]


def _test_recipients(user_id: str | None) -> list[str]:
    """测试收件人：用户自己配的优先，否则用服务器的全局列表。"""
    raw = (storage.get_user_settings(user_id) or {}).get("test_recipients") or ""
    items = [o.strip() for o in raw.split(",") if o.strip()]
    return items or settings.test_recipient_list


def _default_notification_email(user_id: str | None) -> str | None:
    value = (storage.get_user_settings(user_id) or {}).get("notification_email") or ""
    return value.strip() or None


@router.post("/send", summary="发送邮件（agent 主入口）")
async def send_mail(
    payload: SendMailRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    principal.require("mail:send")

    configured_email = _default_notification_email(principal.user_id)
    if not payload.to and not payload.task_id and not configured_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail(
                "missing_recipients",
                "请提供收件人，或先在网页「系统设置」中配置默认通知邮箱",
            ),
        )

    # 该用户自己的 SMTP（未配置则回落服务器全局）
    smtp = mailer.smtp_for_user(principal.user_id)

    # ---- 幂等：同一 idempotency_key 直接返回上次结果 ----
    if payload.idempotency_key:
        existed = storage.find_mail_log_by_idempotency(
            payload.idempotency_key,
            user_id=principal.user_id,
            api_key_name=principal.api_key_scope,
        )
        if existed:
            return ok(
                request,
                {
                    "id": existed["id"],
                    "status": existed["status"],
                    "message_id": existed["message_id"],
                    "latency_ms": existed["latency_ms"],
                    "size_bytes": existed["size_bytes"],
                    "to": existed["to_addrs"],
                    "cc": existed["cc_addrs"],
                    "subject": existed["subject"],
                    "created_at": existed["created_at"],
                    "deduplicated": True,
                },
            )

    # ---- 限流 ----
    allowed, used, remaining = ratelimit.hit(principal.name)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=fail(
                "rate_limit_exceeded",
                f"该密钥每小时最多发送 {settings.rate_limit_per_hour} 封邮件",
                used=used,
                limit=settings.rate_limit_per_hour,
            ),
        )

    # ---- 正文：优先模板，其次 body/html ----
    subject, text_body, html_body = payload.subject, payload.body, payload.html
    if payload.template:
        try:
            t_subject, t_text, t_html = templates.render_template(
                payload.template, payload.variables or {}
            )
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=fail(
                    "unknown_template",
                    f"未知模板：{payload.template}",
                    available=[t["name"] for t in templates.list_templates()],
                ),
            ) from None
        subject = subject or t_subject
        text_body = text_body or t_text
        html_body = html_body or t_html

    if not (text_body or html_body):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail("empty_body", "必须提供 body / html 之一，或指定 template"),
        )
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail("empty_subject", "必须提供 subject，或指定带主题的 template"),
        )

    # ---- 关联任务：把「免登录回复链接」织进正文 ----
    # 三种写法：
    #   1) 给 task_id        → 挂到已有任务线程
    #   2) 给 conversation_id → 在指定对话下**自动新开一条线程**（一个对话可以发多封）
    #   3) 给 external_id    → 先幂等 ensure 对话，再自动新开线程（定时任务最省事）
    task: dict | None = None
    reply_url: str | None = None
    resolved_task_id: str | None = payload.task_id
    conversation_id: str | None = payload.conversation_id
    conversation_created: bool | None = None

    if payload.task_id:
        task = storage.get_task(
            payload.task_id,
            user_id=principal.user_id,
            scoped=True,
            api_key_name=principal.api_key_scope,
        )
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=fail("task_not_found", f"找不到任务 {payload.task_id}"),
            )
        if task["status"] != "open":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=fail("task_closed", "该任务已关闭，无法发送带回复链接的邮件"),
            )
        conversation_id = task.get("conversation_id")
    elif payload.conversation_id or payload.external_id:
        if payload.external_id:
            convo, conversation_created = storage.ensure_conversation(
                external_id=payload.external_id.strip(),
                title=payload.thread_title,
                api_key_name=principal.storage_owner,
                user_id=principal.user_id,
            )
            conversation_id = convo["id"]
        elif not storage.get_conversation(
            payload.conversation_id,
            principal.user_id,
            scoped=True,
            api_key_name=principal.api_key_scope,
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=fail("conversation_not_found", f"找不到对话 {payload.conversation_id}"),
            )

        if settings.reply_token_ttl_days:
            from datetime import timedelta, timezone

            expires_at = (
                datetime.now(timezone.utc) + timedelta(days=settings.reply_token_ttl_days)
            ).isoformat(timespec="seconds")
        else:  # pragma: no cover - TTL 配置为 0 时不设过期
            expires_at = None

        resolved_task_id = storage.create_task(
            title=payload.thread_title or subject,
            agent_name=principal.name,
            api_key_name=principal.storage_owner,
            meta={"auto_created_by": "mail/send", "subject": subject},
            reply_expires_at=expires_at,
            user_id=principal.user_id,
            conversation_id=conversation_id,
        )
        storage.add_task_message(
            task_id=resolved_task_id,
            role="system",
            content=f"邮件发出后自动开线程：{subject}",
            author="system",
            source="system",
        )
        task = storage.get_task(resolved_task_id)

    # 会话线程里保存「原始正文」，避免把链接区块也写进对话历史
    thread_content = text_body or html_body or ""
    if task and payload.attach_reply_link:
        link = replylink.issue_for_task(
            task["id"],
            version=task["token_version"],
            request=request,
            user_id=principal.user_id,
        )
        reply_url = link["reply_url"]
        text_body, html_body = replylink.decorate(
            text=text_body,
            html=html_body,
            url=reply_url,
            expires_at=datetime.fromisoformat(link["reply_expires_at"]),
            title=task.get("title"),
        )

    if payload.to:
        to_list = [str(a) for a in payload.to]
    elif task and task.get("notification_emails"):
        to_list = list(task["notification_emails"])
    elif configured_email:
        to_list = [configured_email]
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail(
                "missing_recipients",
                "请提供收件人，或先在网页「系统设置」中配置默认通知邮箱",
            ),
        )

    cc_list = [str(a) for a in (payload.cc or [])]
    bcc_list = [str(a) for a in (payload.bcc or [])]
    att_meta = [
        {"filename": a.filename, "mime_type": a.mime_type, "bytes": 0}
        for a in (payload.attachments or [])
    ]

    client_ip = request.client.host if request.client else None
    request_id = getattr(request.state, "request_id", None)

    def log(status_: str, **extra):
        return storage.insert_mail_log(
            user_id=principal.user_id,
            request_id=request_id,
            client_ip=client_ip,
            api_key_name=principal.storage_owner,
            sender=smtp.from_addr,
            to_addrs=to_list,
            cc_addrs=cc_list,
            bcc_addrs=bcc_list,
            subject=subject,
            status=status_,
            attachments=[a["filename"] for a in att_meta],
            body_preview=_preview(text_body or html_body),
            idempotency_key=payload.idempotency_key,
            template=payload.template,
            task_id=resolved_task_id,
            reply_url=reply_url,
            **extra,
        )

    try:
        result = mailer.send(
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            subject=subject,
            text=text_body,
            html=html_body,
            reply_to=str(payload.reply_to) if payload.reply_to else None,
            attachments=payload.attachments,
            smtp=smtp,
        )
    except mailer.MailError as exc:
        log_id = log(
            "failed",
            error=f"{exc.message}",
            error_code=exc.code,
            latency_ms=0,
            attempts=exc.attempts,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=fail(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                attempts=exc.attempts,
                log_id=log_id,
                recipient=to_list,
            ),
        ) from exc

    log_id = log(
        "sent",
        message_id=result.message_id,
        latency_ms=result.latency_ms,
        size_bytes=result.size_bytes,
        attempts=result.attempts,
    )

    if resolved_task_id:
        # 首次发信确定该任务线程的通知目标；后续 post_task_message 会沿用它。
        storage.set_task_notification_emails(resolved_task_id, to_list)

    # 记入任务会话：这样用户在回复页就能看到 Agent 发过的邮件内容
    if task:
        storage.add_task_message(
            task_id=task["id"],
            role="agent",
            content=thread_content,
            author=task.get("agent_name") or principal.name,
            source="email",
            meta={
                "mail_log_id": log_id,
                "message_id": result.message_id,
                "to": to_list,
                "subject": subject,
            },
        )

    return ok(
        request,
        {
            "id": log_id,
            "status": "sent",
            "message_id": result.message_id,
            "latency_ms": result.latency_ms,
            "size_bytes": result.size_bytes,
            "to": to_list,
            "cc": cc_list,
            "subject": subject,
            "accepted": result.accepted,
            "refused": result.refused,
            "attempts": result.attempts,
            "created_at": storage.now_iso(),
            "task_id": resolved_task_id,
            "conversation_id": conversation_id,
            "conversation_created": conversation_created,
            "reply_url": reply_url,
            "rate_limit": {"used": used, "remaining": remaining, "limit": settings.rate_limit_per_hour},
        },
    )


@router.get("/logs", summary="查询发送记录")
async def list_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None, description="按主题/收件人模糊搜索"),
    principal: Principal = Depends(current_principal),
):
    principal.require("mail:read")
    return ok(
        request,
        storage.list_mail_logs(
            page=page,
            page_size=page_size,
            status=status_filter,
            q=q,
            user_id=principal.user_id,
            api_key_name=principal.api_key_scope,
        ),
    )


@router.get("/logs/{log_id}", summary="查询单封邮件详情")
async def get_log(log_id: str, request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:read")
    record = storage.get_mail_log(
        log_id, user_id=principal.user_id, api_key_name=principal.api_key_scope
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=fail("log_not_found", f"找不到记录 {log_id}"),
        )
    return ok(request, record)


@router.get("/stats", summary="发信统计")
async def stats(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:read")
    data = storage.mail_stats(
        user_id=principal.user_id, api_key_name=principal.api_key_scope
    )
    data["rate_limit"] = ratelimit.snapshot(principal.name)
    return ok(request, data)


@router.get("/templates", summary="内置邮件模板列表")
async def list_templates(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:send")
    return ok(request, templates.list_templates())


@router.post("/verify-connection", summary="测试 SMTP 连通性与登录")
async def verify_connection(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:send")
    return ok(request, mailer.check_connection(mailer.smtp_for_user(principal.user_id)))


@router.get("/test-recipients", summary="测试收件人列表")
async def test_recipients(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:send")
    return ok(request, {"recipients": _test_recipients(principal.user_id)})
