"""
任务会话（agent 启动任务 → 拿 task_id → 发信带回复链接 → 用户网页回信 → agent 轮询取回）。

设计要点：
- 一个任务 = 一条会话线程（task_messages），agent 与用户交替发言；
- 回复链接是 HMAC 签名令牌，用户点开即用，无需账号；
- agent 可用长轮询（wait_seconds）等用户回复，避免高频空转。
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .. import storage
from ..config import settings
from ..responses import fail, ok
from ..schemas import TaskCreateRequest, TaskMessageRequest, TaskPatchRequest
from ..security import Principal, current_principal
from ..services import mailer, replylink

router = APIRouter(prefix="/api/v1/tasks", tags=["任务会话 Tasks"])


def _task_public(task: dict) -> dict:
    """去掉内部字段，补上派生信息。"""
    return {
        "id": task["id"],
        "title": task["title"],
        "agent_name": task["agent_name"],
        "status": task["status"],
        "meta": task.get("meta") or {},
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
        "last_message_at": task["last_message_at"],
        "message_count": task["message_count"],
        "user_message_count": task["user_message_count"],
        "agent_message_count": task["agent_message_count"],
        "unread_for_agent": task["unread_for_agent"],
        "waiting_reply": task["status"] == "open" and task["unread_for_agent"] > 0,
        "reply_expires_at": task["reply_expires_at"],
        "token_version": task["token_version"],
    }


def _load_task_or_404(task_id: str) -> dict:
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=fail("task_not_found", f"找不到任务 {task_id}"),
        )
    return task


@router.post("", status_code=status.HTTP_201_CREATED, summary="创建任务（Agent 启动时调用，拿到 task_id）")
async def create_task(
    payload: TaskCreateRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:write")

    ttl = payload.reply_expires_days or settings.reply_token_ttl_days
    from datetime import datetime, timedelta, timezone

    expires_at = (datetime.now(timezone.utc) + timedelta(days=ttl)).isoformat(timespec="seconds")
    agent_name = payload.agent_name or principal.name

    task_id = storage.create_task(
        title=payload.title,
        agent_name=agent_name,
        api_key_name=principal.name,
        meta=payload.meta,
        reply_expires_at=expires_at,
    )
    storage.add_task_message(
        task_id=task_id,
        role="system",
        content=f"任务已创建（Agent：{agent_name}）",
        author="system",
        source="system",
    )
    task = storage.get_task(task_id) or {}
    link = replylink.issue_for_task(task_id, version=task.get("token_version", 1), request=request, ttl_days=ttl)

    return ok(
        request,
        {
            "task_id": task_id,
            # 与 GET /tasks 的 items[].id 保持一致，避免 Agent 侧字段名踩坑
            "id": task_id,
            "status": "open",
            "title": payload.title,
            "agent_name": agent_name,
            "created_at": task.get("created_at"),
            **link,
            "usage": (
                "把 task_id 传给 POST /api/v1/mail/send，邮件正文会自动带上回复链接；"
                "之后用 GET /api/v1/tasks/{task_id}/messages 取用户回复。"
            ),
        },
    )


@router.get("", summary="任务列表")
async def list_tasks(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None, description="按标题 / 任务 ID / Agent 名搜索"),
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:read")
    data = storage.list_tasks(page=page, page_size=page_size, status=status_filter, q=q)
    data["items"] = [_task_public(t) for t in data["items"]]
    data["stats"] = storage.task_stats()
    return ok(request, data)


@router.get("/{task_id}", summary="任务详情（含会话线程）")
async def get_task(
    task_id: str,
    request: Request,
    include_link: bool = Query(True, description="是否同时签发一个可用的回复链接"),
    limit: int = Query(200, ge=1, le=500),
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:read")
    task = _load_task_or_404(task_id)
    messages = storage.list_task_messages(task_id, limit=limit)
    data = {"task": _task_public(task), "messages": messages}
    if include_link:
        data.update(
            replylink.issue_for_task(task_id, version=task["token_version"], request=request)
        )
    return ok(request, data)


@router.patch("/{task_id}", summary="更新任务（标题 / 状态 / 上下文）")
async def patch_task(
    task_id: str,
    payload: TaskPatchRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:write")
    _load_task_or_404(task_id)
    updated = storage.update_task(
        task_id,
        title=payload.title,
        agent_name=payload.agent_name,
        status=payload.status,
        meta=payload.meta,
    )
    return ok(request, {"task": _task_public(_load_task_or_404(task_id)), "updated": updated})


@router.post("/{task_id}/close", summary="关闭任务（回复链接随之失效）")
async def close_task(
    task_id: str, request: Request, principal: Principal = Depends(current_principal)
):
    principal.require("tasks:write")
    _load_task_or_404(task_id)
    storage.update_task(task_id, status="closed")
    storage.add_task_message(
        task_id=task_id, role="system", content="任务已关闭", author="system", source="system"
    )
    return ok(request, {"task": _task_public(_load_task_or_404(task_id))})


@router.post("/{task_id}/reopen", summary="重新打开任务")
async def reopen_task(
    task_id: str, request: Request, principal: Principal = Depends(current_principal)
):
    principal.require("tasks:write")
    _load_task_or_404(task_id)
    storage.update_task(task_id, status="open")
    return ok(request, {"task": _task_public(_load_task_or_404(task_id))})


@router.post("/{task_id}/reply-link", summary="轮换回复链接（旧链接立即失效）")
async def rotate_link(
    task_id: str, request: Request, principal: Principal = Depends(current_principal)
):
    principal.require("tasks:write")
    _load_task_or_404(task_id)
    version = storage.rotate_reply_token(task_id)
    link = replylink.issue_for_task(task_id, version=version or 1, request=request)
    return ok(
        request,
        {"task_id": task_id, "token_version": version, **link, "note": "此前发出的所有回复链接已失效"},
    )


@router.delete("/{task_id}", summary="删除任务及其会话消息（不可恢复）")
async def delete_task(
    task_id: str, request: Request, principal: Principal = Depends(current_principal)
):
    principal.require("tasks:write")
    _load_task_or_404(task_id)
    removed = storage.delete_task(task_id)
    return ok(
        request,
        {
            "task_id": task_id,
            "deleted": removed,
            "note": "任务与其会话消息已删除，回复链接同时失效；邮件发送记录保留但已解除关联。",
        },
    )


@router.post("/{task_id}/messages", status_code=status.HTTP_201_CREATED, summary="Agent 向任务线程发消息")
async def post_message(
    task_id: str,
    payload: TaskMessageRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:write")
    task = _load_task_or_404(task_id)

    author = payload.author or task.get("agent_name") or principal.name
    message = storage.add_task_message(
        task_id=task_id, role="agent", content=payload.content, author=author, source="api"
    )

    result: dict = {"message": message}
    if payload.notify_email:
        # 把这条消息同时以邮件形式推给用户，并自动附带回复链接
        to_list = [str(a) for a in payload.notify_email]
        link = replylink.issue_for_task(task_id, version=task["token_version"], request=request)
        from datetime import datetime

        expires = datetime.fromisoformat(link["reply_expires_at"])
        text_body, html_body = replylink.decorate(
            text=payload.content,
            html=None,
            url=link["reply_url"],
            expires_at=expires,
            title=task.get("title"),
        )
        try:
            sent = mailer.send(
                to=to_list,
                subject=payload.notify_subject or f"[{task.get('title') or '任务'}] 有新消息",
                text=text_body,
                html=html_body,
            )
            storage.insert_mail_log(
                api_key_name=principal.name,
                sender=settings.from_email,
                to_addrs=to_list,
                subject=payload.notify_subject or f"[{task.get('title') or '任务'}] 有新消息",
                status="sent",
                message_id=sent.message_id,
                latency_ms=sent.latency_ms,
                size_bytes=sent.size_bytes,
                attachments=[],
                body_preview=payload.content[: settings.body_preview_chars],
                task_id=task_id,
                reply_url=link["reply_url"],
            )
            result["email"] = {"status": "sent", "to": to_list, "message_id": sent.message_id, **link}
        except mailer.MailError as exc:
            result["email"] = {"status": "failed", "to": to_list, "error": exc.to_dict()}
    return ok(request, result)


@router.get("/{task_id}/messages", summary="取会话消息（支持长轮询等用户回复）")
async def get_messages(
    task_id: str,
    request: Request,
    after_id: str | None = Query(None, description="只取此消息之后的新消息"),
    role: str | None = Query(None, description="只看某一方：user / agent / system"),
    limit: int = Query(200, ge=1, le=500),
    wait_seconds: int = Query(0, ge=0, le=60, description="长轮询：最多阻塞这么多秒等新消息"),
    mark_read: bool = Query(True, description="取回后把未读计数清零"),
    include_link: bool = Query(False, description="是否附带一个新的回复链接"),
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:read")
    task = _load_task_or_404(task_id)

    deadline = time.monotonic() + wait_seconds
    messages = storage.list_task_messages(task_id, after_id=after_id, limit=limit, role=role)
    while not messages and wait_seconds > 0 and time.monotonic() < deadline:
        await asyncio.sleep(1.5)
        messages = storage.list_task_messages(task_id, after_id=after_id, limit=limit, role=role)

    if mark_read and any(m["role"] == "user" for m in messages):
        storage.mark_task_read(task_id)

    task = storage.get_task(task_id) or task
    data = {
        "task_id": task_id,
        "messages": messages,
        # 兼容别名：与 GET /api/v1/tasks 的 items 保持一致
        "items": messages,
        "count": len(messages),
        "waited": wait_seconds if messages else 0,
        "task": _task_public(task),
    }
    if include_link and task["status"] == "open":
        data.update(
            replylink.issue_for_task(task_id, version=task["token_version"], request=request)
        )
    return ok(request, data)
