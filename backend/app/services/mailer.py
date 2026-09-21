"""
SMTP 邮件发送服务（通用 SMTP：465/SSL 或 587/STARTTLS）。

多用户要点：
- 每个用户可以在网页上配置自己的 SMTP，存在 ``user_settings`` 里；
- ``smtp_for_user(user_id)`` 负责「用户配置优先、全局 .env 兜底」，产出 ``SmtpConfig``；
- 所有发送/连通性检查都接收 ``SmtpConfig``，不再直接读 ``settings``。

其它设计：
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
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

from .. import storage
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


@dataclass
class SmtpConfig:
    """一次发送所需的全部 SMTP 参数（与全局配置解耦，便于按用户覆盖）。"""

    host: str = ""
    port: int = 465
    use_ssl: bool = True
    starttls: bool = False
    user: str = ""
    password: str = ""
    from_email: str = ""
    from_name: str = ""
    timeout: int = 30
    max_retries: int = 2
    source: str = "env"  # env | user

    @property
    def from_addr(self) -> str:
        return self.from_email or self.user

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    def public(self) -> dict[str, Any]:
        """给前端用的安全视图：不含明文密码，只给 password_set 标志。"""
        data = asdict(self)
        data.pop("password", None)
        data["password_set"] = bool(self.password)
        data["from_addr"] = self.from_addr
        data["configured"] = self.configured
        return data


# ------------------------------------------------------------------ 配置解析
def default_smtp() -> SmtpConfig:
    """全局默认（.env），未配置自己 SMTP 的用户直接用它。"""
    return SmtpConfig(
        host=settings.smtp_host or "",
        port=int(settings.smtp_port or 465),
        use_ssl=bool(settings.smtp_use_ssl),
        starttls=bool(settings.smtp_starttls),
        user=settings.smtp_user or "",
        password=settings.smtp_password or "",
        from_email=settings.smtp_from_email or "",
        from_name=settings.smtp_from_name or "",
        timeout=int(settings.smtp_timeout or 30),
        max_retries=int(settings.smtp_max_retries or 2),
        source="env",
    )


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "ssl", "starttls")


def smtp_from_dict(raw: dict[str, Any] | None, base: SmtpConfig | None = None) -> SmtpConfig:
    """把前端提交/数据库里存的字典转成 SmtpConfig，缺省字段沿用 base。"""
    base = base or default_smtp()
    raw = raw or {}
    host = str(raw.get("host") or "").strip()
    return SmtpConfig(
        host=host or base.host,
        port=int(raw.get("port") or base.port or 465),
        use_ssl=_as_bool(raw.get("use_ssl"), base.use_ssl),
        starttls=_as_bool(raw.get("starttls"), base.starttls),
        user=str(raw.get("user") or "").strip() or (base.user if not host else ""),
        password=raw.get("password") or (base.password if not host else ""),
        from_email=str(raw.get("from_email") or "").strip(),
        from_name=str(raw.get("from_name") or "").strip() or base.from_name,
        timeout=int(raw.get("timeout") or base.timeout or 30),
        max_retries=int(raw.get("max_retries") if raw.get("max_retries") is not None else base.max_retries),
        source="user" if host else base.source,
    )


def smtp_for_user(user_id: str | None) -> SmtpConfig:
    """用户没配 SMTP（host 为空）就用全局默认；配了就完全按用户的来。"""
    base = default_smtp()
    if not user_id:
        return base
    saved = (storage.get_user_settings(user_id) or {}).get("smtp") or {}
    if not str(saved.get("host") or "").strip():
        return base
    return smtp_from_dict(saved, base)


def base_url_for_user(user_id: str | None, request=None) -> str:
    """回复链接的对外根地址：用户配置优先，其次全局，最后取当前请求 host。"""
    if user_id:
        saved = (storage.get_user_settings(user_id) or {}).get("public_base_url")
        if saved and str(saved).strip():
            return str(saved).strip().rstrip("/")
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return f"http://{settings.host}:{settings.port}"


# ------------------------------------------------------------------ helpers
def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _connect(cfg: SmtpConfig) -> smtplib.SMTP:
    """建立到 SMTP 服务器的连接（含登录）。"""
    try:
        if cfg.use_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                cfg.host, cfg.port, timeout=cfg.timeout, context=_ssl_context()
            )
        else:
            client = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout)
            client.ehlo()
            if cfg.starttls:
                client.starttls(context=_ssl_context())
                client.ehlo()
    except (socket.timeout, socket.gaierror, OSError, ssl.SSLError) as exc:
        raise MailError(
            "smtp_connect_failed", f"无法连接 {cfg.host}:{cfg.port} — {exc}", retryable=True
        ) from exc

    try:
        if cfg.user:
            client.login(cfg.user, cfg.password)
    except smtplib.SMTPAuthenticationError as exc:
        try:
            client.close()
        finally:
            pass
        raise MailError(
            "smtp_auth_failed",
            f"SMTP 认证失败（{cfg.user}）：{exc.smtp_error!r}。"
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
    smtp: SmtpConfig | None = None,
) -> EmailMessage:
    cfg = smtp or default_smtp()
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.from_name, cfg.from_addr))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=cfg.from_addr.split("@")[-1] or "localhost")

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
    smtp: SmtpConfig | None = None,
) -> SendResult:
    cfg = smtp or default_smtp()
    if not cfg.configured:
        raise MailError(
            "smtp_not_configured",
            "尚未配置 SMTP（主机/账号/密码）。请在控制台「系统设置」里填写后再发送。",
            retryable=False,
        )

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
        smtp=cfg,
    )
    size_bytes = len(msg.as_bytes())

    envelope_to = list(to) + list(cc or []) + list(bcc or [])
    attempts = 0
    last_error: MailError | None = None
    started = time.perf_counter()

    while attempts <= max(0, cfg.max_retries):
        attempts += 1
        client: smtplib.SMTP | None = None
        try:
            client = _connect(cfg)
            refused = client.send_message(msg, from_addr=cfg.from_addr, to_addrs=envelope_to)
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
            if not exc.retryable or attempts > cfg.max_retries:
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
            last_error = MailError(
                "smtp_send_failed", f"发送失败：{exc}", attempts=attempts, retryable=True
            )
            if attempts > cfg.max_retries:
                raise last_error from exc

        time.sleep(min(3.0, 0.8 * attempts))  # 退避后重试

    raise last_error or MailError("smtp_send_failed", "发送失败（未知原因）", attempts=attempts)


def check_connection(smtp: SmtpConfig | None = None) -> dict[str, Any]:
    """只做连通性 + 登录验证，不发信。"""
    cfg = smtp or default_smtp()
    started = time.perf_counter()
    base = {
        "host": cfg.host,
        "port": cfg.port,
        "ssl": cfg.use_ssl,
        "starttls": cfg.starttls,
        "user": cfg.user,
        "from_email": cfg.from_addr,
        "source": cfg.source,
    }
    if not cfg.host:
        return {**base, "ok": False, "error": {"code": "smtp_not_configured", "message": "尚未配置 SMTP 主机"}}

    client: smtplib.SMTP | None = None
    try:
        client = _connect(cfg)
        code, banner = client.noop()
        try:
            client.quit()
        except smtplib.SMTPException:
            client.close()
        return {
            **base,
            "ok": True,
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
            **base,
            "ok": False,
            "error": exc.to_dict(),
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
