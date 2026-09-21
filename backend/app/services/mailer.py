"""
SMTP 邮件发送服务（通用 SMTP：465/SSL 或 587/STARTTLS）。

设计要点：
- 每次发送独立建连（SMTP_SSL），失败自动重连重试；
- 错误归类成稳定的 error_code，方便 agent 判断该不该重试；
- 不在日志里打印密码。
"""
from __future__ import annotations

import base64
import mimetypes
import smtplib
import socket
import ssl
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

from ..config import settings


class MailError(Exception):
    """带稳定错误码的邮件异常。"""

    def __init__(self, code: str, message: str, attempts: int = 1, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.attempts = attempts
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "attempts": self.attempts,
            "retryable": self.retryable,
        }


@dataclass
class SendResult:
    message_id: str
    latency_ms: int
    size_bytes: int
    attempts: int = 1
    accepted: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ helpers
def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _connect() -> smtplib.SMTP:
    """建立到 SMTP 服务器的连接（含登录）。"""
    try:
        if settings.smtp_use_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout,
                context=_ssl_context(),
            )
        else:
            client = smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout
            )
            client.ehlo()
            if settings.smtp_starttls:
                client.starttls(context=_ssl_context())
                client.ehlo()
    except (socket.timeout, socket.gaierror, OSError, ssl.SSLError) as exc:
        raise MailError("smtp_connect_failed", f"无法连接 {settings.smtp_host}:{settings.smtp_port} — {exc}", retryable=True) from exc

    try:
        if settings.smtp_user:
            client.login(settings.smtp_user, settings.smtp_password)
    except smtplib.SMTPAuthenticationError as exc:
        try:
            client.close()
        finally:
            pass
        raise MailError(
            "smtp_auth_failed",
            f"SMTP 认证失败（{settings.smtp_user}）：{exc.smtp_error!r}。"
            "请确认服务商已开启 SMTP 访问，或改用应用专用密码。",
            retryable=False,
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError("smtp_login_failed", f"SMTP 登录异常：{exc}", retryable=True) from exc

    return client


def _decode_attachments(attachments: list[Any] | None) -> list[tuple[str, bytes, str]]:
    """把 base64 附件解码为 (filename, bytes, mime_type)。"""
    out: list[tuple[str, bytes, str]] = []
    total = 0
    for item in attachments or []:
        raw = item.content_base64 if hasattr(item, "content_base64") else item["content_base64"]
        name = item.filename if hasattr(item, "filename") else item["filename"]
        mime = (
            (item.mime_type if hasattr(item, "mime_type") else item.get("mime_type"))
            or mimetypes.guess_type(name)[0]
            or "application/octet-stream"
        )
        payload_b64 = raw.split(",", 1)[-1] if raw.strip().startswith("data:") else raw
        try:
            data = base64.b64decode(payload_b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise MailError("invalid_attachment", f"附件 {name} 不是合法的 base64：{exc}") from exc
        total += len(data)
        if total > settings.max_attachment_bytes:
            raise MailError(
                "attachment_too_large",
                f"附件总大小超过限制 {settings.max_attachment_bytes // 1024 // 1024} MB",
            )
        out.append((name, data, mime))
    return out


def build_message(
    *,
    to: list[str],
    subject: str,
    text: str | None = None,
    html: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((settings.smtp_from_name, settings.from_email))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.from_email.split("@")[-1] or "localhost")

    if html and text:
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
    elif html:
        msg.set_content(html, subtype="html")
    else:
        msg.set_content(text or "")

    for name, data, mime in attachments or []:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(
            data, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=name
        )
    return msg


def send(
    *,
    to: list[str],
    subject: str,
    text: str | None = None,
    html: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    attachments: list[Any] | None = None,
) -> SendResult:
    decoded = _decode_attachments(attachments)
    msg = build_message(
        to=to,
        subject=subject,
        text=text,
        html=html,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
        attachments=decoded,
    )
    size_bytes = len(msg.as_bytes())

    envelope_to = list(to) + list(cc or []) + list(bcc or [])
    attempts = 0
    last_error: MailError | None = None
    started = time.perf_counter()

    while attempts <= max(0, settings.smtp_max_retries):
        attempts += 1
        client: smtplib.SMTP | None = None
        try:
            client = _connect()
            refused = client.send_message(msg, from_addr=settings.from_email, to_addrs=envelope_to)
            try:
                client.quit()
            except smtplib.SMTPException:
                client.close()
            latency = int((time.perf_counter() - started) * 1000)
            return SendResult(
                message_id=str(msg["Message-ID"]),
                latency_ms=latency,
                size_bytes=size_bytes,
                attempts=attempts,
                accepted=[a for a in envelope_to if a not in refused],
                refused=list(refused.keys()),
            )
        except MailError as exc:
            last_error = exc
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
            if not exc.retryable or attempts > settings.smtp_max_retries:
                exc.attempts = attempts
                raise
        except smtplib.SMTPRecipientsRefused as exc:
            raise MailError(
                "recipient_refused", f"收件人被拒绝：{exc.recipients}", attempts=attempts
            ) from exc
        except smtplib.SMTPSenderRefused as exc:
            raise MailError(
                "sender_refused",
                f"发件人被拒绝：{exc.smtp_error!r}（发件地址须与 SMTP 账号一致）",
                attempts=attempts,
            ) from exc
        except (smtplib.SMTPDataError, smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            last_error = MailError("smtp_send_failed", f"发送失败：{exc}", attempts=attempts, retryable=True)
            if attempts > settings.smtp_max_retries:
                raise last_error from exc

        time.sleep(min(3.0, 0.8 * attempts))  # 退避后重试

    raise last_error or MailError("smtp_send_failed", "发送失败（未知原因）", attempts=attempts)


def check_connection() -> dict[str, Any]:
    """只做连通性 + 登录验证，不发信。"""
    started = time.perf_counter()
    client: smtplib.SMTP | None = None
    try:
        client = _connect()
        code, banner = client.noop()
        try:
            client.quit()
        except smtplib.SMTPException:
            client.close()
        return {
            "ok": True,
            "host": settings.smtp_host,
            "port": settings.smtp_port,
            "ssl": settings.smtp_use_ssl,
            "user": settings.smtp_user,
            "from_email": settings.from_email,
            "noop_code": code,
            "banner": str(banner),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    except MailError as exc:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        return {
            "ok": False,
            "host": settings.smtp_host,
            "port": settings.smtp_port,
            "user": settings.smtp_user,
            "error": exc.to_dict(),
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
