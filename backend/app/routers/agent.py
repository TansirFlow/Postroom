"""
面向 AI Agent 的「工具自描述」端点。

agent 启动时拉一次 /api/v1/agent/tools，即可拿到 OpenAI function-calling 风格的
工具定义，直接注册进自己的 tool 列表，无需人工写 prompt。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..config import settings
from ..responses import ok
from ..services import templates

router = APIRouter(prefix="/api/v1/agent", tags=["Agent 工具发现"])


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _normalize(tools: list[dict]) -> list[dict]:
    """把 endpoint/method/auth/scope 提升到工具顶层，保证 function 字段严格符合 OpenAI schema。"""
    out = []
    for tool in tools:
        fn = dict(tool["function"])
        meta = {k: fn.pop(k) for k in ("endpoint", "method", "auth", "scope") if k in fn}
        out.append({"type": tool["type"], "function": fn, **meta})
    return out


def _tools(base: str) -> list[dict]:
    send_params = {
        "type": "object",
        "properties": {
            "to": {
                "type": "array",
                "items": {"type": "string", "format": "email"},
                "description": "收件人邮箱列表，至少一个",
            },
            "subject": {"type": "string", "description": "邮件主题，最长 300 字；使用 template 时可省略"},
            "body": {"type": "string", "description": "纯文本正文"},
            "html": {"type": "string", "description": "HTML 正文（可选，与 body 同时给出则生成富文本邮件）"},
            "cc": {"type": "array", "items": {"type": "string"}, "description": "抄送"},
            "bcc": {"type": "array", "items": {"type": "string"}, "description": "密送"},
            "reply_to": {"type": "string", "description": "回复到的地址"},
            "template": {
                "type": "string",
                "enum": [t["name"] for t in templates.list_templates()],
                "description": "内置模板名；使用后 subject/body 会被模板渲染结果覆盖",
            },
            "variables": {"type": "object", "description": "模板变量键值对"},
            "attachments": {
                "type": "array",
                "description": "附件列表（base64）",
                "items": {
                    "type": "object",
                    "required": ["filename", "content_base64"],
                    "properties": {
                        "filename": {"type": "string"},
                        "content_base64": {"type": "string"},
                        "mime_type": {"type": "string"},
                    },
                },
            },
            "idempotency_key": {
                "type": "string",
                "description": "幂等键。同一 key 重复调用只会真正发送一次，适合 agent 重试场景",
            },
            "task_id": {
                "type": "string",
                "description": (
                    "任务 ID（由 create_task 获得）。提供后邮件正文会自动附带"
                    "「免登录回复链接」，并把本封内容记入任务会话"
                ),
            },
            "attach_reply_link": {
                "type": "boolean",
                "default": True,
                "description": "task_id 存在时是否追加回复链接，默认追加",
            },
        },
        "required": ["to"],
    }

    return _normalize(
        [
        {
            "type": "function",
            "function": {
                "name": "send_email",
                "description": (
                    "通过配置好的 SMTP 账号发送一封邮件。支持纯文本/HTML 正文、抄送密送、"
                    "base64 附件，以及内置模板。返回发送结果与记录 id。"
                ),
                "parameters": send_params,
                "endpoint": f"{base}/api/v1/mail/send",
                "method": "POST",
                "auth": "header:X-API-Key",
                "scope": "mail:send",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_email_logs",
                "description": "查询历史发信记录，支持分页、状态过滤（sent/failed）与关键词搜索。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {"type": "integer", "default": 1},
                        "page_size": {"type": "integer", "default": 20},
                        "status": {"type": "string", "enum": ["sent", "failed"]},
                        "q": {"type": "string", "description": "按主题或收件人搜索"},
                    },
                },
                "endpoint": f"{base}/api/v1/mail/logs",
                "method": "GET",
                "auth": "header:X-API-Key",
                "scope": "mail:read",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_email_templates",
                "description": "列出可用的内置邮件模板及其变量名。",
                "parameters": {"type": "object", "properties": {}},
                "endpoint": f"{base}/api/v1/mail/templates",
                "method": "GET",
                "auth": "header:X-API-Key",
                "scope": "mail:send",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_mail_connection",
                "description": "检查 SMTP 服务器连通性与登录状态，排查发信失败原因时先调用它。",
                "parameters": {"type": "object", "properties": {}},
                "endpoint": f"{base}/api/v1/mail/verify-connection",
                "method": "POST",
                "auth": "header:X-API-Key",
                "scope": "mail:send",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_task",
                "description": (
                    "任务启动时调用：创建一个任务会话并拿到 task_id 与「免登录回复链接」。"
                    "把 task_id 传给 send_email，用户就能在网页里直接回信；"
                    "之后用 get_task_messages 取回用户的回复。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "任务标题，会显示在回复页与邮件里"},
                        "agent_name": {"type": "string", "description": "Agent 显示名"},
                        "meta": {
                            "type": "object",
                            "description": "任意业务上下文（如 run_id、checkpoint 路径），原样带回",
                        },
                        "reply_expires_days": {
                            "type": "integer",
                            "description": "回复链接有效期（天），默认 30",
                        },
                    },
                },
                "endpoint": f"{base}/api/v1/tasks",
                "method": "POST",
                "auth": "header:X-API-Key",
                "scope": "tasks:write",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_task_messages",
                "description": (
                    "读取任务会话消息（用户回信就在其中）。支持 after_id 增量拉取，"
                    "以及 wait_seconds 长轮询阻塞等待用户回复，避免空转轮询。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "任务 ID"},
                        "after_id": {"type": "string", "description": "只取该消息之后的新消息（增量）"},
                        "role": {"type": "string", "enum": ["user", "agent", "system"]},
                        "wait_seconds": {
                            "type": "integer",
                            "default": 0,
                            "description": "长轮询秒数（0-60）。没有人回复时会一直等到超时",
                        },
                        "mark_read": {"type": "boolean", "default": True},
                        "include_link": {"type": "boolean", "default": False},
                    },
                    "required": ["task_id"],
                },
                "endpoint": f"{base}/api/v1/tasks/{{task_id}}/messages",
                "method": "GET",
                "auth": "header:X-API-Key",
                "scope": "tasks:read",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "post_task_message",
                "description": (
                    "向任务会话追加一条 Agent 消息。可选 notify_email：同一条消息"
                    "直接以邮件推给用户（自动附带回复链接），无需再单独调 send_email。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "content": {"type": "string", "description": "消息内容"},
                        "author": {"type": "string"},
                        "notify_email": {
                            "type": "array",
                            "items": {"type": "string", "format": "email"},
                            "description": "可选：同时邮件通知这些地址",
                        },
                        "notify_subject": {"type": "string"},
                    },
                    "required": ["task_id", "content"],
                },
                "endpoint": f"{base}/api/v1/tasks/{{task_id}}/messages",
                "method": "POST",
                "auth": "header:X-API-Key",
                "scope": "tasks:write",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "close_task",
                "description": "关闭任务，回复链接随之失效（用户无法再回信）。",
                "parameters": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
                "endpoint": f"{base}/api/v1/tasks/{{task_id}}/close",
                "method": "POST",
                "auth": "header:X-API-Key",
                "scope": "tasks:write",
            },
        },
        ]
    )


@router.get("/tools", summary="Agent 工具清单（OpenAI function-calling 风格）")
async def tools(request: Request):
    base = _base_url(request)
    return ok(
        request,
        {
            "name": settings.app_name,
            "version": settings.app_version,
            "base_url": base,
            "auth": {
                "type": "api_key",
                "header": "X-API-Key",
                "alternative": "Authorization: Bearer <key>",
            },
            "response_format": {
                "success": {"ok": True, "data": {}, "error": None, "request_id": "req_xxx"},
                "error": {"ok": False, "data": None, "error": {"code": "...", "message": "..."}, "request_id": "req_xxx"},
            },
            "docs": f"{base}/api/docs",
            "openapi": f"{base}/openapi.json",
            "tools": _tools(base),
        },
    )


@router.get("/manifest", summary="服务能力摘要（给 agent 做规划用）")
async def manifest(request: Request):
    base = _base_url(request)
    return ok(
        request,
        {
            "service": settings.app_name,
            "version": settings.app_version,
            "summary": "面向 AI Agent 的工具型 API 服务器：邮件发送 + 任务会话（邮件内嵌免登录回复链接，用户可在网页里直接与 Agent 对话）。",
            "task_flow": [
                "1. 任务启动：POST /api/v1/tasks → 拿到 task_id 与 reply_url",
                "2. 汇报进展：POST /api/v1/mail/send（带 task_id）→ 邮件正文自动附带回复链接",
                "3. 等用户回复：GET /api/v1/tasks/{task_id}/messages?wait_seconds=30（长轮询）",
                "4. 继续对话：POST /api/v1/tasks/{task_id}/messages 追加消息，可 notify_email 顺带发信",
                "5. 任务结束：POST /api/v1/tasks/{task_id}/close → 回复链接失效",
            ],
            "modules": [
                {
                    "id": "tasks",
                    "name": "任务会话",
                    "status": "ready",
                    "endpoints": [
                        "POST /api/v1/tasks",
                        "GET  /api/v1/tasks",
                        "GET  /api/v1/tasks/{task_id}",
                        "PATCH /api/v1/tasks/{task_id}",
                        "POST /api/v1/tasks/{task_id}/messages",
                        "GET  /api/v1/tasks/{task_id}/messages",
                        "POST /api/v1/tasks/{task_id}/reply-link",
                        "POST /api/v1/tasks/{task_id}/close",
                    ],
                    "public_reply_endpoints": [
                        "GET  /api/v1/reply/{token}",
                        "GET  /api/v1/reply/{token}/messages",
                        "POST /api/v1/reply/{token}",
                    ],
                },
                {
                    "id": "mail",
                    "name": "邮件",
                    "status": "ready",
                    "endpoints": [
                        "POST /api/v1/mail/send",
                        "GET  /api/v1/mail/logs",
                        "GET  /api/v1/mail/logs/{id}",
                        "GET  /api/v1/mail/templates",
                        "GET  /api/v1/mail/stats",
                        "POST /api/v1/mail/verify-connection",
                    ],
                },
                {
                    "id": "keys",
                    "name": "密钥管理",
                    "status": "ready",
                    "endpoints": [
                        "GET    /api/v1/keys",
                        "POST   /api/v1/keys",
                        "PATCH  /api/v1/keys/{id}?enabled=true|false",
                        "DELETE /api/v1/keys/{id}",
                    ],
                },
            ],
            "quickstart": {
                "python": (
                    "import requests\n"
                    f"r = requests.post('{base}/api/v1/mail/send',\n"
                    "    headers={'X-API-Key': 'YOUR_KEY'},\n"
                    "    json={'to': ['someone@example.com'], 'subject': 'Hi', 'body': 'from agent'})\n"
                    "print(r.json())"
                ),
                "curl": (
                    f"curl -X POST {base}/api/v1/mail/send \\\n"
                    "  -H 'Content-Type: application/json' \\\n"
                    "  -H 'X-API-Key: YOUR_KEY' \\\n"
                    "  -d '{\"to\":[\"someone@example.com\"],\"subject\":\"Hi\",\"body\":\"from agent\"}'"
                ),
            },
        },
    )
