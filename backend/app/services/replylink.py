"""
把「回复链接」织进邮件正文：纯文本追加可点击 URL，HTML 追加一个按钮区块。
"""
from __future__ import annotations

from datetime import datetime

from .. import storage, tokens
from ..config import settings


def user_base_url(user_id: str | None) -> str:
    """用户级「对外根地址」：用户在设置页填了自己的域名就用它。"""
    if user_id:
        saved = (storage.get_user_settings(user_id) or {}).get("public_base_url")
        if saved and str(saved).strip():
            return str(saved).strip().rstrip("/")
    return ""


def resolve_base_url(request=None, user_id: str | None = None) -> str:
    """确定对外可访问的根地址：用户配置 > 全局配置 > 当前请求 host。"""
    custom = user_base_url(user_id)
    if custom:
        return custom
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return f"http://{settings.host}:{settings.port}"


def issue_for_task(
    task_id: str,
    version: int = 1,
    request=None,
    ttl_days: int | None = None,
    user_id: str | None = None,
) -> dict:
    """为任务签发一个新的回复链接（同一任务可签发多个，互不影响）。"""
    token, expires_at = tokens.issue_token(task_id, version=version, ttl_days=ttl_days)
    url = tokens.build_reply_url(resolve_base_url(request, user_id), token)
    return {
        "reply_url": url,
        "reply_token": token,
        "reply_expires_at": expires_at.isoformat(timespec="seconds"),
    }


def decorate(
    *,
    text: str | None,
    html: str | None,
    url: str,
    expires_at: datetime | None,
    title: str | None = None,
) -> tuple[str | None, str | None]:
    """返回追加回复入口后的 (text, html)。"""
    ttl_note = ""
    if expires_at:
        ttl_note = f"链接有效期至 {expires_at.astimezone().strftime('%Y-%m-%d %H:%M')}"

    text_block = (
        "\n\n"
        "——————————————\n"
        "💬 直接回复本任务（点开即用，无需登录）\n"
        f"{url}\n"
    )
    if ttl_note:
        text_block += f"{ttl_note}\n"

    html_block = f"""
<div style="margin-top:26px;padding-top:18px;border-top:1px solid #e5e7eb;
            font-family:system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;
            font-size:14px;line-height:1.7;color:#1f2329">
  <div style="color:#8a9099;font-size:12.5px;margin-bottom:10px">
    直接回复本任务 —— 点开即用，无需登录
  </div>
  <a href="{url}"
     style="display:inline-block;padding:11px 22px;background:#2f6bff;color:#ffffff;
            border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
    打开对话页面回复 →
  </a>
  <div style="margin-top:12px;color:#8a9099;font-size:12px;word-break:break-all">
    若按钮无法点击，请复制此链接到浏览器：<br />
    <a href="{url}" style="color:#2f6bff">{url}</a>
    {f'<br />{ttl_note}' if ttl_note else ''}
  </div>
</div>
"""

    new_text = (text or "") + text_block
    if html:
        new_html = html + html_block
    else:
        # 原文只有纯文本时，给链接也做一个简单 HTML 版本，方便富文本客户端
        new_html = (
            "<div style=\"font-family:system-ui,-apple-system,sans-serif;font-size:14px;"
            "line-height:1.7;color:#1f2329;white-space:pre-wrap\">"
            + (text or "").replace("<", "&lt;")
            + "</div>"
            + html_block
        )
    return new_text, new_html
