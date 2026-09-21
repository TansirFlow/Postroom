"""
内置邮件模板：{{变量}} 占位符替换。
agent 调用时传 template + variables 即可，无需自己拼 HTML。
"""
from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

TEMPLATES: dict[str, dict[str, str]] = {
    "welcome": {
        "label": "欢迎邮件",
        "description": "新用户/新成员欢迎信",
        "subject": "欢迎加入 {{product}}",
        "text": (
            "你好 {{name}}：\n\n"
            "欢迎使用 {{product}}！你的账号已准备就绪。\n\n"
            "如有任何问题，直接回复本邮件即可。\n\n"
            "—— {{sender_name}}"
        ),
        "html": (
            "<div style=\"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
            "font-size:15px;line-height:1.7;color:#1f2329\">"
            "<h2 style=\"margin:0 0 12px\">你好 {{name}} 👋</h2>"
            "<p>欢迎使用 <b>{{product}}</b>，你的账号已准备就绪。</p>"
            "<p>如有任何问题，直接回复本邮件即可。</p>"
            "<p style=\"color:#8a9099;margin-top:20px\">—— {{sender_name}}</p></div>"
        ),
    },
    "alert": {
        "label": "告警通知",
        "description": "系统/任务异常告警",
        "subject": "[{{level}}] {{title}}",
        "text": "级别：{{level}}\n对象：{{target}}\n时间：{{time}}\n\n{{message}}",
        "html": (
            "<div style=\"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
            "font-size:15px;line-height:1.7;color:#1f2329\">"
            "<div style=\"display:inline-block;padding:2px 10px;border-radius:6px;"
            "background:#fdecec;color:#d92d20;font-weight:600;font-size:13px\">{{level}}</div>"
            "<h2 style=\"margin:12px 0 8px\">{{title}}</h2>"
            "<table style=\"border-collapse:collapse;font-size:14px\">"
            "<tr><td style=\"padding:4px 12px 4px 0;color:#8a9099\">对象</td><td>{{target}}</td></tr>"
            "<tr><td style=\"padding:4px 12px 4px 0;color:#8a9099\">时间</td><td>{{time}}</td></tr>"
            "</table>"
            "<pre style=\"margin-top:14px;padding:12px;background:#f5f6f7;border-radius:8px;"
            "white-space:pre-wrap;font-size:13px\">{{message}}</pre></div>"
        ),
    },
    "report": {
        "label": "数据简报",
        "description": "周期性数据/训练进度汇报",
        "subject": "{{title}} · {{period}}",
        "text": "{{title}}（{{period}}）\n\n{{summary}}\n\n明细：{{detail}}",
        "html": (
            "<div style=\"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
            "font-size:15px;line-height:1.7;color:#1f2329\">"
            "<h2 style=\"margin:0 0 4px\">{{title}}</h2>"
            "<div style=\"color:#8a9099;font-size:13px;margin-bottom:14px\">{{period}}</div>"
            "<p style=\"margin:0 0 10px\">{{summary}}</p>"
            "<pre style=\"padding:12px;background:#f5f6f7;border-radius:8px;"
            "white-space:pre-wrap;font-size:13px\">{{detail}}</pre></div>"
        ),
    },
}


def list_templates() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "label": tpl["label"],
            "description": tpl["description"],
            "subject": tpl["subject"],
            "variables": sorted(set(_PLACEHOLDER.findall(
                tpl["subject"] + tpl["text"] + tpl["html"]
            ))),
        }
        for name, tpl in TEMPLATES.items()
    ]


def render(value: str, variables: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        cur: Any = variables
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return match.group(0)  # 未提供变量则原样保留
        return str(cur)

    return _PLACEHOLDER.sub(repl, value)


def render_template(
    name: str, variables: dict[str, Any]
) -> tuple[str, str, str]:
    if name not in TEMPLATES:
        raise KeyError(name)
    tpl = TEMPLATES[name]
    variables = {**variables}
    variables.setdefault("sender_name", "Postroom")
    return (
        render(tpl["subject"], variables),
        render(tpl["text"], variables),
        render(tpl["html"], variables),
    )
