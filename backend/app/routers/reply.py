"""
免登录回复接口：链接本身即凭证。

令牌里只有「任务 + 版本 + 过期时间」，并由服务端密钥签名，
因此拿到链接的人只能看/回这一条会话，无法触达其它任务。

这里只管「收下用户的话」——Agent 侧不在这里等，也不长轮询；
定时任务用 GET /api/v1/inbox 增量拉取后按 conversation_id 分发回各对话。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query, Request, status

from .. import ratelimit, storage, tokens
from ..config import settings
from ..responses import fail, ok
from ..schemas import ReplyMessageRequest
from ..services import replylink

router = APIRouter(prefix="/api/v1/reply", tags=["免登录回复 Reply"])


def _resolve(token: str) -> tuple[dict, tokens.TokenPayload]:
    try:
        payload = tokens.verify_token(token)
    except tokens.TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=fail(exc.code, exc.message),
        ) from exc

    task = storage.get_task(payload.task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=fail("task_not_found", "会话不存在或已被删除"),
        )
    if int(task["token_version"]) != payload.version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=fail("token_revoked", "该回复链接已失效，请让 Agent 重新发送邮件"),
        )
    return task, payload


def _session(task: dict, payload: tokens.TokenPayload) -> dict:
    open_ = task["status"] == "open"
    return {
        "task_id": task["id"],
        "title": task["title"] or "未命名任务",
        "agent_name": task["agent_name"] or "Agent",
        "status": task["status"],
        "can_reply": open_,
        "message_count": task["message_count"],
        "created_at": task["created_at"],
        "last_message_at": task["last_message_at"],
        "link_expires_at": time.strftime(
            "%Y-%m-%dT%H:%M:%S%z", time.localtime(payload.expires_at)
        ),
    }


@router.get("/{token}", summary="打开会话（校验链接并返回线程）")
async def open_session(
    token: str,
    request: Request,
    limit: int = Query(300, ge=1, le=500),
):
    task, payload = _resolve(token)
    messages = storage.list_task_messages(task["id"], limit=limit)
    return ok(
        request,
        {
            "session": _session(task, payload),
            "messages": messages,
            "server_time": storage.now_iso(),
        },
    )


@router.get("/{token}/messages", summary="增量拉取新消息（不阻塞，用户页面前的主动刷新用）")
async def poll_messages(
    token: str,
    request: Request,
    after_id: str | None = Query(None),
):
    task, payload = _resolve(token)
    messages = storage.list_task_messages(task["id"], after_id=after_id, limit=300)
    return ok(
        request,
        {
            "messages": messages,
            "count": len(messages),
            "session": _session(task, payload),
            "server_time": storage.now_iso(),
        },
    )


@router.post("/{token}", status_code=status.HTTP_201_CREATED, summary="用户回信")
async def post_reply(
    token: str,
    payload: ReplyMessageRequest,
    request: Request,
):
    task, token_payload = _resolve(token)
    if task["status"] != "open":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=fail("task_closed", "该任务已关闭，无法继续回复"),
        )

    allowed, used, _ = ratelimit.hit(
        f"reply:{task['id']}", limit=settings.reply_rate_limit_per_hour
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=fail(
                "reply_rate_limited",
                f"回复过于频繁，每小时最多 {settings.reply_rate_limit_per_hour} 条",
                used=used,
            ),
        )

    content = payload.content.strip()
    if len(content) > settings.message_max_chars:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=fail(
                "message_too_long",
                f"单条消息最长 {settings.message_max_chars} 字",
                length=len(content),
            ),
        )

    author = (payload.author or "").strip() or "用户"
    message = storage.add_task_message(
        task_id=task["id"],
        role="user",
        content=content,
        author=author,
        source="web",
        meta={"client_ip": request.client.host if request.client else None},
    )
    task = storage.get_task(task["id"]) or task
    return ok(
        request,
        {
            "message": message,
            "session": _session(task, token_payload),
            "agent_hint": (
                "回复已入库，Agent 会在下一次定时拉取时取走："
                f"GET /api/v1/inbox（或本线程增量 GET /api/v1/tasks/{task['id']}/messages"
                f"?after_id={message['id']}）。你随时可以再回来补充或调整方向。"
            ),
        },
    )


@router.get("/{token}/link", summary="用旧链接换一个新链接（自助续期）")
async def refresh_link(token: str, request: Request):
    task, _ = _resolve(token)
    if task["status"] != "open":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=fail("task_closed", "该任务已关闭"),
        )
    return ok(
        request,
        {
            "task_id": task["id"],
            **replylink.issue_for_task(
                task["id"],
                version=task["token_version"],
                request=request,
                user_id=task.get("user_id"),
            ),
        },
    )
