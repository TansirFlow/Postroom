"""
Postroom —— 应用入口。

启动：  python run.py
文档：  http://127.0.0.1:8077/api/docs
控制台：http://127.0.0.1:8077/  （账号密码登录；需先构建前端）

多用户：首次启动会自动创建管理员账号（用户名见 BOOTSTRAP_ADMIN_USERNAME，默认 admin），
随机密码与两把密钥会打印到控制台并写入 backend/data/keys.txt，请立即保存并修改密码。
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

from . import passwords, storage
from .config import FRONTEND_DIST, settings
from .responses import fail
from .routers import (
    agent,
    auth,
    conversations,
    inbox,
    keys,
    mail,
    reply,
    settings as settings_router,
    system,
    tasks,
    users,
)
from .security import ALL_SCOPES, generate_key, hash_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("postroom")

AGENT_DEFAULT_SCOPES = ["mail:send", "mail:read", "tasks:write", "tasks:read"]

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "面向 AI Agent 的工具型 API 服务器（多用户）。\n\n"
        "- 统一响应包裹：`{ok, data, error, request_id}`\n"
        "- 人用网页：`POST /api/v1/auth/login` 换会话令牌，之后用 `Authorization: Bearer <token>`\n"
        "- Agent 用：`X-API-Key` 头（或 `Authorization: Bearer <key>`）\n"
        "- 数据隔离：密钥 / 邮件记录 / 任务 / 设置全部按用户隔离\n"
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
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(settings_router.router)
app.include_router(mail.router)
app.include_router(conversations.router)
app.include_router(tasks.router)
app.include_router(inbox.router)
app.include_router(reply.router)
app.include_router(keys.router)
app.include_router(agent.router)


# ------------------------------------------------------------------ startup
@app.on_event("startup")
async def on_startup() -> None:
    storage.init_db()
    boot = _bootstrap()
    if boot.get("first_run"):
        _dump_first_run(boot)

    logger.info(
        "%s v%s 已启动 | 多用户模式 | 账号 %d 个 | 全局 SMTP=%s:%s",
        settings.app_name,
        settings.app_version,
        storage.count_users(),
        settings.smtp_host or "(未配置)",
        settings.smtp_port,
    )
    logger.info("控制台: http://%s:%s/  接口文档: http://%s:%s/api/docs", settings.host, settings.port, settings.host, settings.port)


def _bootstrap() -> dict:
    """保证「至少一个管理员账号 + 一把根密钥 + 一把默认 Agent 密钥」存在。"""
    data_dir = Path(settings.db_path).parent
    data_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {"first_run": False}

    # ---- 1) 管理员账号 ----
    admin = storage.first_admin()
    if not admin:
        username = (settings.bootstrap_admin_username or "admin").strip() or "admin"
        password = settings.bootstrap_admin_password or passwords.generate_password()
        if storage.get_user_by_username(username):
            # 用户名被普通账号占了，退让一个后缀，避免启动失败
            username = f"{username}-admin"
        user_id = storage.create_user(
            username=username,
            password_hash=passwords.hash_password(password),
            display_name="管理员",
            role="admin",
            note="服务启动时自动创建",
            must_change_password=False,
        )
        admin = storage.get_user(user_id)
        result.update(
            first_run=True,
            admin_username=username,
            admin_password=password,
            admin_password_generated=not bool(settings.bootstrap_admin_password),
        )

    assert admin is not None  # 上面已保证

    # ---- 2) 根密钥（.env 优先，其次 data/admin_key.txt，最后生成）----
    admin_file = data_dir / "admin_key.txt"
    if not settings.admin_api_key:
        if admin_file.exists():
            settings.admin_api_key = admin_file.read_text(encoding="utf-8").strip()
        if not settings.admin_api_key:
            settings.admin_api_key = generate_key("sk-admin")
            admin_file.write_text(settings.admin_api_key, encoding="utf-8")
            admin_file.chmod(0o600)
    if not storage.find_api_key_by_hash(hash_key(settings.admin_api_key)):
        storage.insert_api_key(
            name="admin",
            key_hash=hash_key(settings.admin_api_key),
            prefix=settings.admin_api_key[:12],
            scopes=ALL_SCOPES,
            note="服务启动时自动创建的根密钥",
            user_id=admin["id"],
        )

    # ---- 3) 管理员账号下的默认 Agent 密钥 ----
    if storage.count_api_keys(user_id=admin["id"]) <= 1:
        agent_key = settings.bootstrap_api_key or generate_key()
        settings.bootstrap_api_key = agent_key
        storage.insert_api_key(
            name="agent-default",
            key_hash=hash_key(agent_key),
            prefix=agent_key[:12],
            scopes=AGENT_DEFAULT_SCOPES,
            note="首次启动自动创建",
            user_id=admin["id"],
        )
        result.update(first_run=True, agent_api_key=agent_key)

    result["admin_username"] = result.get("admin_username") or admin["username"]
    return result


def _dump_first_run(boot: dict) -> None:
    lines = [
        "=" * 66,
        " 首次启动：已创建管理员账号与 API 密钥（明文只在这里出现，请保存）",
        "=" * 66,
        f" [控制台]  用户名 / USERNAME  = {boot.get('admin_username')}",
    ]
    if boot.get("admin_password"):
        lines.append(f" [控制台]  密  码 / PASSWORD  = {boot['admin_password']}")
    else:
        lines.append(" [控制台]  密码沿用 .env 中的 BOOTSTRAP_ADMIN_PASSWORD")
    lines += [
        f" [管理员]  ADMIN_API_KEY     = {settings.admin_api_key}",
        f" [Agent ]  AGENT_API_KEY     = {settings.bootstrap_api_key}",
        "-" * 66,
        " 登录后请到「系统设置」修改密码并配置各自的 SMTP；",
        " 新增账号请在「用户管理」里创建（登录页不提供自助注册）。",
        "=" * 66,
    ]
    text = "\n".join(lines)
    print("\n" + text + "\n", flush=True)
    try:
        path = Path(settings.db_path).parent / "keys.txt"
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
                "docs": "/api/docs",
                "agent_tools": "/api/v1/agent/tools",
                "build_frontend": "cd frontend && npm install && npm run build",
            },
        }
