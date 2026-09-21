"""
Postroom —— 应用入口。

启动：  python run.py
文档：  http://127.0.0.1:8077/docs
控制台：http://127.0.0.1:8077/  （需先构建前端）
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import storage
from .config import FRONTEND_DIST, settings
from .responses import fail
from .routers import agent, keys, mail, reply, system, tasks
from .security import ALL_SCOPES, generate_key, hash_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent-api")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "面向 AI Agent 的工具型 API 服务器。\n\n"
        "- 统一响应包裹：`{ok, data, error, request_id}`\n"
        "- 鉴权：`X-API-Key` 头（或 `Authorization: Bearer <key>`）\n"
        "- 工具自描述：`GET /api/v1/agent/tools`"
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ middleware
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Response-Time-ms"] = f"{elapsed:.1f}"
    if request.url.path.startswith("/api"):
        logger.info(
            "%s %s -> %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
    return response


# ------------------------------------------------------------------ errors
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    error = detail if isinstance(detail, dict) else {"code": "http_error", "message": str(detail)}
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "data": None,
            "error": error,
            "request_id": getattr(request.state, "request_id", None),
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "ok": False,
            "data": None,
            "error": fail(
                "validation_error",
                "请求参数校验失败",
                details=[
                    {"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]}
                    for e in exc.errors()
                ],
            ),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "data": None,
            "error": fail("internal_error", f"服务器内部错误：{exc}"),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


# ------------------------------------------------------------------ routers
app.include_router(system.router)
app.include_router(mail.router)
app.include_router(tasks.router)
app.include_router(reply.router)
app.include_router(keys.router)
app.include_router(agent.router)


# ------------------------------------------------------------------ startup
@app.on_event("startup")
async def on_startup() -> None:
    storage.init_db()

    # 管理员主密钥：.env 未配置时，从 data/admin_key.txt 复用，没有才生成并落盘
    # （保证服务重启后密钥不变，不会每次都新增一条 admin 记录）
    if not settings.admin_api_key:
        data_dir = Path(settings.db_path).parent
        admin_file = data_dir / "admin_key.txt"
        if admin_file.exists() and admin_file.read_text(encoding="utf-8").strip():
            settings.admin_api_key = admin_file.read_text(encoding="utf-8").strip()
        else:
            settings.admin_api_key = generate_key("sk-admin")
            data_dir.mkdir(parents=True, exist_ok=True)
            admin_file.write_text(settings.admin_api_key, encoding="utf-8")
    # 若数据库里还没有这个管理员密钥，落库一份，便于控制台查看
    if not storage.find_api_key_by_hash(hash_key(settings.admin_api_key)):
        storage.insert_api_key(
            name="admin",
            key_hash=hash_key(settings.admin_api_key),
            prefix=settings.admin_api_key[:12],
            scopes=ALL_SCOPES,
            note="服务启动时自动创建的根密钥",
        )

    # 首次启动自动创建一个 agent 默认密钥
    agent_key = settings.bootstrap_api_key
    if storage.count_api_keys() <= 1:
        if not agent_key:
            agent_key = generate_key()
            settings.bootstrap_api_key = agent_key
        storage.insert_api_key(
            name="agent-default",
            key_hash=hash_key(agent_key),
            prefix=agent_key[:12],
            scopes=["mail:send", "mail:read", "tasks:write", "tasks:read"],
            note="首次启动自动创建",
        )
        _dump_keys(agent_key)

    logger.info("%s v%s 已启动，SMTP=%s:%s", settings.app_name, settings.app_version, settings.smtp_host, settings.smtp_port)
    logger.info("API 文档: http://%s:%s/docs", settings.host, settings.port)


def _dump_keys(agent_key: str) -> None:
    lines = [
        "=" * 62,
        " 首次启动：已自动创建 API 密钥（明文只在这里出现，请保存）",
        "=" * 62,
        f" [管理员]  ADMIN_API_KEY  = {settings.admin_api_key}",
        f" [Agent ]  AGENT_API_KEY  = {agent_key}",
        "=" * 62,
    ]
    text = "\n".join(lines)
    print("\n" + text + "\n", flush=True)
    try:
        path = storage.Path(settings.db_path).parent / "keys.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"# {storage.now_iso()}\n{text}\n\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入 keys.txt 失败：%s", exc)


# ------------------------------------------------------------------ 前端静态资源
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")

else:

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "ok": True,
            "data": {
                "message": "前端尚未构建（frontend/dist 不存在）",
                "docs": "/docs",
                "agent_tools": "/api/v1/agent/tools",
                "build_frontend": "cd frontend && npm install && npm run build",
            },
        }
