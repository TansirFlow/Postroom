"""
对话（Conversation）—— 比任务更高一层的容器。

模型：
- Agent 侧的**一个对话**（codex / claude code 等客户端里的一个会话）在这里对应一条
  conversation，靠 ``external_id`` 标识；
- 其下可以挂**多条任务线程**，每封带回复链接的邮件 = 一条任务线程；
- ``POST /api/v1/conversations`` 是**幂等 ensure**：同一个 ``external_id`` 重复提交
  只会复用已有对话（``created=false``），所以定时任务重启后不必自己记住 conversation_id；
- 用户的回复统一走 ``GET /api/v1/inbox`` 增量拉取，再按 ``conversation_id`` 分发回各对话。

权限沿用 ``tasks:*``（不新增 scope，避免已发出的密钥失效）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .. import storage
from ..responses import fail, ok
from ..schemas import ConversationEnsureRequest, ConversationPatchRequest
from ..security import Principal, current_principal

router = APIRouter(prefix="/api/v1/conversations", tags=["对话 Conversations"])


def _conversation_public(convo: dict) -> dict:
    return {
        "id": convo["id"],
        "conversation_id": convo["id"],
        "external_id": convo.get("external_id"),
        "title": convo.get("title"),
        "agent_name": convo.get("agent_name"),
        "status": convo["status"],
        "meta": convo.get("meta") or {},
        "created_at": convo["created_at"],
        "updated_at": convo["updated_at"],
        "last_message_at": convo["last_message_at"],
        "task_count": convo["task_count"],
        "message_count": convo["message_count"],
        "user_message_count": convo["user_message_count"],
        "agent_message_count": convo["agent_message_count"],
        "unread_for_agent": convo["unread_for_agent"],
        "waiting_reply": convo["status"] == "open" and convo["unread_for_agent"] > 0,
    }


def _load_conversation_or_404(conversation_id: str, principal: Principal) -> dict:
    convo = storage.get_conversation(
        conversation_id,
        principal.user_id,
        scoped=True,
        api_key_name=principal.api_key_scope,
    )
    if not convo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=fail("conversation_not_found", f"找不到对话 {conversation_id}"),
        )
    return convo


@router.post(
    "",
    summary="幂等创建/复用对话（Agent 每个对话调用一次；有 external_id 就不会重复建）",
)
async def ensure_conversation(
    payload: ConversationEnsureRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:write")
    convo, created = storage.ensure_conversation(
        external_id=(payload.external_id or "").strip() or None,
        title=payload.title,
        agent_name=payload.agent_name,
        api_key_name=principal.storage_owner,
        meta=payload.meta,
        user_id=principal.user_id,
    )
    return ok(
        request,
        {
            "conversation": _conversation_public(convo),
            "conversation_id": convo["id"],
            "id": convo["id"],
            "created": created,
            "usage": (
                "拿到 conversation_id 后：发信用 POST /api/v1/mail/send 带 "
                "conversation_id（或 external_id）；收用户回复用 GET /api/v1/inbox 增量拉取后按 "
                "conversation_id 分发。"
            ),
        },
    )


@router.get("", summary="对话列表")
async def list_conversations(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None, description="按标题 / 对话 ID / external_id / Agent 名搜索"),
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:read")
    data = storage.list_conversations(
        page=page,
        page_size=page_size,
        status=status_filter,
        q=q,
        user_id=principal.user_id,
        api_key_name=principal.api_key_scope,
    )
    data["items"] = [_conversation_public(c) for c in data["items"]]
    data["stats"] = storage.conversation_stats(
        user_id=principal.user_id, api_key_name=principal.api_key_scope
    )
    return ok(request, data)


@router.get("/{conversation_id}", summary="对话详情（含旗下任务线程）")
async def get_conversation(
    conversation_id: str,
    request: Request,
    task_limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:read")
    convo = _load_conversation_or_404(conversation_id, principal)
    tasks = storage.list_conversation_tasks(
        conversation_id, limit=task_limit, api_key_name=principal.api_key_scope
    )
    from .tasks import _task_public

    return ok(
        request,
        {
            "conversation": _conversation_public(convo),
            "tasks": [_task_public(t) for t in tasks],
            "task_count": len(tasks),
        },
    )


@router.patch("/{conversation_id}", summary="更新对话（标题 / 状态 / 外部标识 / 上下文）")
async def patch_conversation(
    conversation_id: str,
    payload: ConversationPatchRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    principal.require("tasks:write")
    _load_conversation_or_404(conversation_id, principal)
    updated = storage.update_conversation(
        conversation_id,
        title=payload.title,
        agent_name=payload.agent_name,
        status=payload.status,
        external_id=payload.external_id,
        meta=payload.meta,
    )
    return ok(
        request,
        {
            "conversation": _conversation_public(
                _load_conversation_or_404(conversation_id, principal)
            ),
            "updated": updated,
        },
    )


@router.post("/{conversation_id}/close", summary="关闭对话（旗下任务线程的回复链接随之失效）")
async def close_conversation(
    conversation_id: str, request: Request, principal: Principal = Depends(current_principal)
):
    principal.require("tasks:write")
    _load_conversation_or_404(conversation_id, principal)
    storage.update_conversation(conversation_id, status="closed")
    closed = 0
    for task in storage.list_conversation_tasks(
        conversation_id, limit=200, api_key_name=principal.api_key_scope
    ):
        if task["status"] == "open":
            storage.update_task(task["id"], status="closed")
            closed += 1
    return ok(
        request,
        {
            "conversation": _conversation_public(
                _load_conversation_or_404(conversation_id, principal)
            ),
            "closed_tasks": closed,
        },
    )


@router.post("/{conversation_id}/reopen", summary="重新打开对话")
async def reopen_conversation(
    conversation_id: str, request: Request, principal: Principal = Depends(current_principal)
):
    principal.require("tasks:write")
    _load_conversation_or_404(conversation_id, principal)
    storage.update_conversation(conversation_id, status="open")
    return ok(
        request,
        {
            "conversation": _conversation_public(
                _load_conversation_or_404(conversation_id, principal)
            )
        },
    )


@router.delete("/{conversation_id}", summary="删除对话（其下任务只解除关联，不连带删除）")
async def delete_conversation(
    conversation_id: str, request: Request, principal: Principal = Depends(current_principal)
):
    principal.require("tasks:write")
    _load_conversation_or_404(conversation_id, principal)
    removed = storage.delete_conversation(conversation_id)
    return ok(
        request,
        {
            "conversation_id": conversation_id,
            "deleted": removed,
            "note": "对话已删除；其下任务线程被解除关联但保留，回复链接仍然可用。",
        },
    )
