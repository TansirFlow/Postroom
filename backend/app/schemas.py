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


class TaskPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    agent_name: str | None = Field(default=None, max_length=80)
    status: Literal["open", "closed"] | None = None
    meta: dict[str, Any] | None = None


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
