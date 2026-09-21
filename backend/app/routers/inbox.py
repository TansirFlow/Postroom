"""
收件箱（Inbox）—— 定时任务（cron）的拉取入口。

和「长轮询等回复」的区别：
- 一个 Agent 可能同时有好几个对话在等用户回复，逐任务轮询既费请求又要自己维护游标；
- 这里 ``GET /api/v1/inbox`` **一次返回全部对话的增量用户回复**，按 ``seq`` 升序，
  调用方拿到后按 ``conversation_id`` 分发回各自的对话即可；
- 游标是服务端分配的全局单调递增 ``seq``，**不传 cursor 时用服务端水位**
  （按 API 密钥记录），所以定时任务即使进程重启、本地没留状态，也不会重复投递；
- 分发成功后用 ``POST /api/v1/inbox/ack`` 把水位推上去；
- 用户过多久回复都行 —— 消息持久化在 SQLite 里，Agent 没在等也不会丢。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from .. import storage
from ..responses import ok
from ..schemas import InboxAckRequest
from ..security import Principal, current_principal

router = APIRouter(prefix="/api/v1/inbox", tags=["收件箱 Inbox"])


def _group(items: list[dict]) -> list[dict]:
    """按会话聚一下，方便调用方直接分发。"""
    buckets: dict[str | None, dict] = {}
    for item in items:
        key = item.get("conversation_id")
        bucket = buckets.setdefault(
            key,
            {
                "conversation_id": key,
                "external_id": item.get("conversation_external_id"),
                "title": item.get("conversation_title"),
                "count": 0,
                "message_ids": [],
                "task_ids": [],
            },
        )
        bucket["count"] += 1
        bucket["message_ids"].append(item["message_id"])
        if item["task_id"] not in bucket["task_ids"]:
            bucket["task_ids"].append(item["task_id"])
    return list(buckets.values())


@router.get("", summary="增量拉取用户回复（定时任务入口；不传 cursor 则用服务端水位）")
async def pull_inbox(
    request: Request,
    cursor: int | None = Query(
        None, ge=0, description="从哪个 seq 之后开始取；省略则用当前密钥的服务端水位"
    ),
    limit: int = Query(50, ge=1, le=200),
    role: str | None = Query(
        "user", description="只看某一方：user（默认）/ agent / system；传 all 表示不限"
    ),
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:read")

    watermark = storage.get_inbox_watermark(principal.inbox_owner, principal.user_id)
    effective = watermark if cursor is None else cursor
    role_filter = None if (role or "").lower() in ("all", "") else role

    items, next_cursor, has_more = storage.inbox_fetch(
        user_id=principal.user_id,
        cursor=effective,
        limit=limit,
        role=role_filter,
        api_key_name=principal.api_key_scope,
    )

    return ok(
        request,
        {
            "items": items,
            "count": len(items),
            "by_conversation": _group(items),
            "cursor": effective,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "watermark": watermark,
            "server_time": storage.now_iso(),
            "usage": (
                "按 conversation_id 把每条消息分发回对应对话，全部成功后 "
                "POST /api/v1/inbox/ack {upto_seq: next_cursor} 推进水位；"
                "下次不带 cursor 拉取即从新水位继续。"
            ),
        },
    )


@router.post("/ack", summary="确认已分发到水位（只增不减，重复提交安全）")
async def ack_inbox(
    payload: InboxAckRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:write")
    watermark = storage.set_inbox_watermark(
        principal.inbox_owner, payload.upto_seq, principal.user_id
    )
    cleared = (
        storage.mark_read_upto(
            principal.user_id, payload.upto_seq, api_key_name=principal.api_key_scope
        )
        if payload.mark_read
        else 0
    )
    return ok(
        request,
        {
            "watermark": watermark,
            "requested": payload.upto_seq,
            "advanced": watermark == max(0, payload.upto_seq),
            "tasks_marked_read": cleared,
            "server_time": storage.now_iso(),
        },
    )


@router.get("/stats", summary="收件箱概览（还有多少没取走）")
async def inbox_stats(request: Request, principal: Principal = Depends(current_principal)):
    principal.require("tasks:read")
    owner = principal.inbox_owner
    watermark = storage.get_inbox_watermark(owner, principal.user_id)
    pending, next_cursor, has_more = storage.inbox_fetch(
        user_id=principal.user_id,
        cursor=watermark,
        limit=200,
        role="user",
        api_key_name=principal.api_key_scope,
    )
    return ok(
        request,
        {
            "watermark": watermark,
            "latest_seq": storage.current_seq(),
            "pending_messages": len(pending),
            "pending_more_than_200": has_more,
            "conversations_waiting": len(_group(pending)),
            "next_cursor": next_cursor,
            "tasks": storage.task_stats(
                user_id=principal.user_id, api_key_name=principal.api_key_scope
            ),
            "conversations": storage.conversation_stats(
                user_id=principal.user_id, api_key_name=principal.api_key_scope
            ),
            "server_time": storage.now_iso(),
        },
    )
