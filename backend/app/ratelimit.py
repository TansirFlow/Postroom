"""极简内存滑动窗口限流（按 API Key 维度）。"""
from __future__ import annotations

import threading
import time
from collections import deque

from .config import settings

_lock = threading.Lock()
_hits: dict[str, deque[float]] = {}


def hit(key: str, limit: int | None = None, window_seconds: int = 3600) -> tuple[bool, int, int]:
    """
    记录一次调用。
    返回 (是否放行, 窗口内已用次数, 剩余次数)
    """
    limit = limit or settings.rate_limit_per_hour
    now = time.time()
    with _lock:
        bucket = _hits.setdefault(key, deque())
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False, len(bucket), 0
        bucket.append(now)
        return True, len(bucket), max(0, limit - len(bucket))


def snapshot(key: str, limit: int | None = None, window_seconds: int = 3600) -> dict:
    limit = limit or settings.rate_limit_per_hour
    now = time.time()
    with _lock:
        bucket = _hits.get(key, deque())
        used = sum(1 for t in bucket if now - t <= window_seconds)
    return {"limit": limit, "used": used, "remaining": max(0, limit - used), "window_seconds": window_seconds}
