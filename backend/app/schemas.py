"""请求 / 响应数据模型（Pydantic v2）。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class AttachmentIn(BaseModel):
    filename: str = Field(..., min_length=1, max_length=200)
    content_base64: str = Field(..., description="文件内容的 base64 编码")
    mime_type: str | None = Field(default=None, description="留空则按扩展名推断")


class SendMailRequest(BaseModel):
    to: list[EmailStr] = Field(..., min_length=1, description="收件人，至少一个")
    subject: str | None = Field(
        default=None, max_length=300, description="主题；使用 template 时可省略"
    )
    body: str | None = Field(default=None, description="纯文本正文")
    html: str | None = Field(default=None, description="HTML 正文（与 body 同时提供则生成多部分邮件）")
    cc: list[EmailStr] | None = None
    bcc: list[EmailStr] | None = None
    reply_to: EmailStr | None = None
    attachments: list[AttachmentIn] | None = None
    template: str | None = Field(default=None, description="内置模板名，如 welcome / alert / report")
    variables: dict[str, Any] | None = Field(default=None, description="模板变量")
    idempotency_key: str | None = Field(
        default=None, max_length=120, description="同一 key 重复提交只会真正发送一次"
    )
    task_id: str | None = Field(
        default=None,
        description="关联任务 ID。提供后邮件正文会自动附带「免登录回复链接」，并把本封内容记入任务会话",
    )
    conversation_id: str | None = Field(
        default=None,
        description=(
            "所属对话 ID。不传 task_id 时，服务端会在这个对话下自动新建一条任务线程，"
            "并把回复链接织进正文 —— 一个对话可以按需发多封邮件"
        ),
    )
    external_id: str | None = Field(
        default=None,
        max_length=200,
        description="便捷写法：只给 Agent 侧的对话标识，服务端自动 ensure 会话（没有就建、有就复用）",
    )
    thread_title: str | None = Field(
        default=None, max_length=200, description="自动新建任务线程时使用的标题"
    )
    attach_reply_link: bool = Field(
        default=True, description="task_id 存在时是否追加回复链接（默认追加）"
    )

    @field_validator("to", "cc", "bcc")
    @classmethod
    def _not_empty(cls, v):
        if v is not None and len(v) == 0:
            return None
        return v


class SendMailResponse(BaseModel):
    id: str
    status: Literal["sent", "failed"]
    message_id: str | None = None
    latency_ms: int
    size_bytes: int
    to: list[str]
    cc: list[str] = []
    subject: str
    created_at: str


class KeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["mail:send", "mail:read"])
    note: str = ""


class KeyCreatedResponse(BaseModel):
    id: str
    name: str
    api_key: str = Field(..., description="明文密钥，仅此一次返回")
    scopes: list[str]
    created_at: str


class Envelope(BaseModel):
    ok: bool
    data: Any | None = None
    error: dict | None = None
    request_id: str | None = None


# ------------------------------------------------------------ 任务 / 会话
class TaskCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200, description="任务标题")
    agent_name: str | None = Field(default=None, max_length=80, description="发起任务的 Agent 名称")
    meta: dict[str, Any] | None = Field(default=None, description="任意业务上下文，回给 Agent 时原样带回")
    reply_expires_days: int | None = Field(
        default=None, ge=1, le=365, description="回复链接有效期（天），默认取服务端配置"
    )
    conversation_id: str | None = Field(default=None, description="所属对话 ID")
    external_id: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "便捷写法：Agent 侧的对话标识（如 codex 的会话 id）。"
            "服务端按 (账号, external_id) 幂等 ensure 一个对话：没有就建，有就复用"
        ),
    )


class ConversationEnsureRequest(BaseModel):
    external_id: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Agent 侧的对话标识，同一账号下唯一。重复提交同一个值会**复用**已有对话"
            "（created=false），因此定时任务重启后不需要自己记住 conversation_id"
        ),
    )
    title: str | None = Field(default=None, max_length=200)
    agent_name: str | None = Field(default=None, max_length=80)
    meta: dict[str, Any] | None = None


class ConversationPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    agent_name: str | None = Field(default=None, max_length=80)
    status: Literal["open", "closed"] | None = None
    external_id: str | None = Field(default=None, max_length=200)
    meta: dict[str, Any] | None = None


class InboxAckRequest(BaseModel):
    upto_seq: int = Field(
        ..., ge=0, description="已成功分发到各对话的最大 seq（即上一次拉取返回的 next_cursor）"
    )
    mark_read: bool = Field(
        default=True, description="是否同时把控制台的待回复计数清零（默认清）"
    )


class TaskPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    agent_name: str | None = Field(default=None, max_length=80)
    status: Literal["open", "closed"] | None = None
    meta: dict[str, Any] | None = None
    conversation_id: str | None = Field(default=None, description="改挂到另一个对话")


class TaskMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, description="消息内容（Markdown 原样存储）")
    author: str | None = Field(default=None, max_length=80, description="显示名，默认取 Agent 名称")
    notify_email: list[EmailStr] | None = Field(
        default=None, description="可选：同时把这封消息作为邮件发出（会附带回复链接）"
    )
    notify_subject: str | None = Field(default=None, max_length=300)


class ReplyMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000, description="用户回复内容")
    author: str | None = Field(default=None, max_length=80, description="可选昵称")
