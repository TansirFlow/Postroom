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


@router.post("/send", summary="发送邮件（agent 主入口）")
async def send_mail(
    payload: SendMailRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    principal.require("mail:send")

    # ---- 幂等：同一 idempotency_key 直接返回上次结果 ----
    if payload.idempotency_key:
        existed = storage.find_mail_log_by_idempotency(payload.idempotency_key)
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
    task: dict | None = None
    reply_url: str | None = None
    if payload.task_id:
        task = storage.get_task(payload.task_id)
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
    # 会话线程里保存「原始正文」，避免把链接区块也写进对话历史
    thread_content = text_body or html_body or ""
    if task and payload.attach_reply_link:
        link = replylink.issue_for_task(
            task["id"], version=task["token_version"], request=request
        )
        reply_url = link["reply_url"]
        text_body, html_body = replylink.decorate(
            text=text_body,
            html=html_body,
            url=reply_url,
            expires_at=datetime.fromisoformat(link["reply_expires_at"]),
            title=task.get("title"),
        )

    to_list = [str(a) for a in payload.to]
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
            request_id=request_id,
            client_ip=client_ip,
            api_key_name=principal.name,
            sender=settings.from_email,
            to_addrs=to_list,
            cc_addrs=cc_list,
            bcc_addrs=bcc_list,
            subject=subject,
            status=status_,
            attachments=[a["filename"] for a in att_meta],
            body_preview=_preview(text_body or html_body),
            idempotency_key=payload.idempotency_key,
            template=payload.template,
            task_id=payload.task_id,
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
            "task_id": payload.task_id,
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
        storage.list_mail_logs(page=page, page_size=page_size, status=status_filter, q=q),
    )


@router.get("/logs/{log_id}", summary="查询单封邮件详情")
async def get_log(log_id: str, request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:read")
    record = storage.get_mail_log(log_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=fail("log_not_found", f"找不到记录 {log_id}"),
        )
    return ok(request, record)


@router.get("/stats", summary="发信统计")
async def stats(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:read")
    data = storage.mail_stats()
    data["rate_limit"] = ratelimit.snapshot(principal.name)
    return ok(request, data)


@router.get("/templates", summary="内置邮件模板列表")
async def list_templates(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:send")
    return ok(request, templates.list_templates())


@router.post("/verify-connection", summary="测试 SMTP 连通性与登录")
async def verify_connection(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:send")
    return ok(request, mailer.check_connection())


@router.get("/test-recipients", summary="测试收件人列表")
async def test_recipients(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("mail:send")
    return ok(request, {"recipients": settings.test_recipient_list})
