"""统一响应包装工具。"""
from __future__ import annotations

from typing import Any

from fastapi import Request


def request_id_of(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def ok(request: Request, data: Any = None) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None, "request_id": request_id_of(request)}


def fail(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}
