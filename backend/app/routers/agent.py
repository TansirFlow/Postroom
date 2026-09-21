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
            "conversation_id": {
                "type": "string",
                "description": (
                    "对话 ID。不给 task_id 时，服务端会在这个对话下**自动新开一条任务线程**"
                    "并把回复链接织进正文 —— 一个对话可以按需发多封邮件"
                ),
            },
            "external_id": {
                "type": "string",
                "description": (
                    "便捷写法：只给 Agent 侧的对话标识（如 codex 的会话 id），"
                    "服务端自动 ensure 对话（没有就建、有就复用）再开线程"
                ),
            },
            "thread_title": {
                "type": "string",
                "description": "自动新开任务线程时用的标题，默认取邮件主题",
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
                    "想收用户回信就带上 conversation_id 或 external_id（自动开线程并附回复链接）。"
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
                "name": "ensure_conversation",
                "description": (
                    "幂等拿一个对话：带 external_id 时，已存在就复用（created=false），"
                    "不存在就新建（created=true）。定时任务每次跑都调一次即可，"
                    "不需要自己记住 conversation_id。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "external_id": {
                            "type": "string",
                            "description": "Agent 侧的对话标识，同一账号下唯一（如 codex:<会话 id>）",
                        },
                        "title": {"type": "string", "description": "对话标题，会显示在控制台"},
                        "agent_name": {"type": "string", "description": "Agent 显示名"},
                        "meta": {"type": "object", "description": "任意业务上下文，原样带回"},
                    },
                },
                "endpoint": f"{base}/api/v1/conversations",
                "method": "POST",
                "auth": "header:X-API-Key",
                "scope": "tasks:write",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "pull_inbox",
                "description": (
                    "【定时任务主入口】**一次拉走全部对话的增量用户回复**，按 seq 升序，"
                    "返回项自带 conversation_id / external_id，按它分发回各对话即可。"
                    "不传 cursor 就用服务端水位（进程重启也不会重复投递）；"
                    "非阻塞，绝不等待用户 —— 用户过多久回复都能拉到。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cursor": {
                            "type": "integer",
                            "description": "从哪个 seq 之后开始取；省略则用当前密钥的服务端水位",
                        },
                        "limit": {"type": "integer", "default": 50, "description": "单次最多取多少条（1-200）"},
                        "role": {
                            "type": "string",
                            "description": "只看某一方：user（默认）/ agent / system；传 all 不限",
                        },
                    },
                },
                "endpoint": f"{base}/api/v1/inbox",
                "method": "GET",
                "auth": "header:X-API-Key",
                "scope": "tasks:read",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ack_inbox",
                "description": (
                    "把回复成功分发到各对话后调用，推进水位（只增不减，重复调用安全）。"
                    "之后不带 cursor 拉取就从新水位继续。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "upto_seq": {
                            "type": "integer",
                            "description": "已分发的最大 seq，即上一次 pull_inbox 返回的 next_cursor",
                        },
                        "mark_read": {
                            "type": "boolean",
                            "default": True,
                            "description": "是否同时清掉控制台的待回复计数",
                        },
                    },
                    "required": ["upto_seq"],
                },
                "endpoint": f"{base}/api/v1/inbox/ack",
                "method": "POST",
                "auth": "header:X-API-Key",
                "scope": "tasks:write",
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
                    "创建一个任务会话线程并拿到 task_id 与「免登录回复链接」。"
                    "把 task_id 传给 send_email，用户就能在网页里直接回信；"
                    "之后用 pull_inbox 增量取回用户的回复（不需要等）。"
                    "如果这个任务属于某个对话，请带上 conversation_id 或 external_id 归组。"
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
                        "conversation_id": {
                            "type": "string",
                            "description": "所属对话 ID（由 ensure_conversation 获得）",
                        },
                        "external_id": {
                            "type": "string",
                            "description": "便捷写法：只给 Agent 侧的对话标识，服务端自动 ensure 对话再挂上去",
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
                    "读取单个任务线程的消息（用户回信就在其中），支持 after_id 增量。"
                    "**不做长轮询、不阻塞**；要跨对话一次性取回全部用户回复请用 pull_inbox。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "任务 ID"},
                        "after_id": {"type": "string", "description": "只取该消息之后的新消息（增量）"},
                        "role": {"type": "string", "enum": ["user", "agent", "system"]},
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
                "note": "每个密钥归属于一个控制台账号，其邮件记录 / 任务 / SMTP 配置按账号隔离",
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
            "summary": (
                "面向 AI Agent 的工具型 API 服务器（多用户）：邮件发送 + 对话/任务会话"
                "（邮件内嵌免登录回复链接，用户可在网页里直接与 Agent 对话）。"
                "回复走**拉取式**：Agent 不需要等用户，定时任务从 /api/v1/inbox 增量拉取"
                "全部对话的新回复后分发回各自对话，用户过多久回复都可以。"
                "每个 API 密钥归属于一个控制台账号，邮件记录、对话、任务与 SMTP 配置互相隔离。"
            ),
            "multi_user": {
                "login": "POST /api/v1/auth/login（账号密码，返回会话令牌）",
                "isolation": "密钥 / 发信记录 / 对话 / 任务 / 设置均按账号（user_id）隔离",
                "settings": "GET|PUT /api/v1/settings（每个账号可以配置自己的 SMTP 与回复链接域名）",
                "account_creation": "仅管理员可在 POST /api/v1/users 创建账号；登录页无自助注册",
            },
            "task_flow": [
                "1. 开局：POST /api/v1/conversations（带 external_id）→ 幂等拿到 conversation_id",
                "2. 发信：POST /api/v1/mail/send（带 conversation_id 或 external_id）→ 自动开任务线程，正文附回复链接",
                "3. 不等：Agent 立刻继续别的活；任务不会被自动关闭，回复链接默认 30 天有效",
                "4. 定时拉取：GET /api/v1/inbox（cron，如每 30 秒）→ 拿到全部对话的增量用户回复",
                "5. 分发 + 确认：按 conversation_id 送回各对话，然后 POST /api/v1/inbox/ack {upto_seq: next_cursor}",
                "6. 收尾：POST /api/v1/conversations/{id}/close（或 /tasks/{task_id}/close）→ 回复链接失效",
            ],
            "reply_model": {
                "non_blocking": "Agent 侧不做长轮询；用户在任意时间回复都可以，消息持久保存不丢",
                "cursor": "游标是全局单调递增的 seq；不传 cursor 时用服务端水位（按 API 密钥记录），进程重启不会重复投递",
                "at_least_once": "先分发、后 ack，崩溃重跑最多重复投递但不会漏；ack 水位只增不减",
                "routing": "每条消息自带 conversation_id / external_id / task_id，直接按它分发回对应对话",
            },
            "modules": [
                {
                    "id": "conversations",
                    "name": "对话",
                    "status": "ready",
                    "endpoints": [
                        "POST   /api/v1/conversations              # 幂等 ensure（按 external_id 复用）",
                        "GET    /api/v1/conversations",
                        "GET    /api/v1/conversations/{id}",
                        "PATCH  /api/v1/conversations/{id}",
                        "POST   /api/v1/conversations/{id}/close",
                        "POST   /api/v1/conversations/{id}/reopen",
                        "DELETE /api/v1/conversations/{id}",
                    ],
                },
                {
                    "id": "inbox",
                    "name": "收件箱（定时任务入口）",
                    "status": "ready",
                    "endpoints": [
                        "GET  /api/v1/inbox           # 增量拉取全部对话的用户回复（非阻塞）",
                        "POST /api/v1/inbox/ack       # 推进水位",
                        "GET  /api/v1/inbox/stats     # 还有多少没取走",
                    ],
                },
                {
                    "id": "tasks",
                    "name": "任务会话",
                    "status": "ready",
                    "endpoints": [
                        "POST   /api/v1/tasks",
                        "GET    /api/v1/tasks",
                        "GET    /api/v1/tasks/{task_id}",
                        "PATCH  /api/v1/tasks/{task_id}",
                        "POST   /api/v1/tasks/{task_id}/messages",
                        "GET    /api/v1/tasks/{task_id}/messages",
                        "POST   /api/v1/tasks/{task_id}/reply-link",
                        "POST   /api/v1/tasks/{task_id}/close",
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
