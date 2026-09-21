"""开发/生产启动脚本：python run.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402

if __name__ == "__main__":
    print(f"→ http://{settings.host}:{settings.port}   控制台 /   Swagger /api/docs")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level="info",
    )
