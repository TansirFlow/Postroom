"""
轻量存储层：直接用标准库 sqlite3（WAL 模式），不引入 ORM。

表结构：
- ``users``          —— 控制台账号（口令 pbkdf2 哈希、角色、会话版本）
- ``user_settings``  —— 每个用户自己的配置（SMTP 等），JSON 一行
- ``api_keys``       —— API 密钥（归属于某个用户）
- ``mail_logs``      —— 发信记录（归属于某个用户）
- ``tasks``          —— 任务会话（归属于某个用户）
- ``task_messages``  —— 会话消息（经 task_id 间接归属）

数据隔离约定：凡带 ``user_id`` 参数的查询，传入具体值就只看该用户的数据，
传入 ``None`` 则只看「无归属」的历史数据（永远不会退化成「看全部」）。
需要跨用户读取的只有两处：按哈希查密钥（登录/鉴权）与按令牌打开会话（回复页）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   TEXT PRIMARY KEY,
    username             TEXT NOT NULL UNIQUE,
    display_name         TEXT NOT NULL DEFAULT '',
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'user',   -- admin | user
    enabled              INTEGER NOT NULL DEFAULT 1,
    session_version      INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    note                 TEXT DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    last_login_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id     TEXT PRIMARY KEY,
    data        TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY,
    user_id       TEXT,
    name          TEXT NOT NULL,
    key_hash      TEXT NOT NULL UNIQUE,
    prefix        TEXT NOT NULL,
    scopes        TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1,
    note          TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    last_used_at  TEXT,
    call_count    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);

CREATE TABLE IF NOT EXISTS mail_logs (
    id            TEXT PRIMARY KEY,
    user_id       TEXT,
    created_at    TEXT NOT NULL,
    request_id    TEXT,
    client_ip     TEXT,
    api_key_name  TEXT,
    sender        TEXT,
    to_addrs      TEXT NOT NULL DEFAULT '[]',
    cc_addrs      TEXT NOT NULL DEFAULT '[]',
    bcc_addrs     TEXT NOT NULL DEFAULT '[]',
    subject       TEXT,
    status        TEXT NOT NULL,
    error         TEXT,
    error_code    TEXT,
    message_id    TEXT,
    latency_ms    INTEGER DEFAULT 0,
    size_bytes    INTEGER DEFAULT 0,
    attachments   TEXT NOT NULL DEFAULT '[]',
    body_preview  TEXT,
    attempts      INTEGER DEFAULT 1,
    idempotency_key TEXT,
    template      TEXT
);

CREATE INDEX IF NOT EXISTS idx_mail_logs_created ON mail_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mail_logs_status  ON mail_logs(status);
CREATE INDEX IF NOT EXISTS idx_mail_logs_user    ON mail_logs(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS conversations (
    id                TEXT PRIMARY KEY,
    user_id           TEXT,
    external_id       TEXT,                              -- Agent 侧的对话标识（如 codex/claude 会话 id）
    title             TEXT,
    agent_name        TEXT,
    api_key_name      TEXT,
    status            TEXT NOT NULL DEFAULT 'open',      -- open | closed
    meta              TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    last_message_at   TEXT,
    task_count        INTEGER NOT NULL DEFAULT 0,
    message_count     INTEGER NOT NULL DEFAULT 0,
    user_message_count  INTEGER NOT NULL DEFAULT 0,
    agent_message_count INTEGER NOT NULL DEFAULT 0,
    unread_for_agent  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);

-- 同一账号下 external_id 唯一：Agent 重复 ensure 同一个对话不会产生重复会话
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_ext
    ON conversations(user_id, external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS counters (
    name   TEXT PRIMARY KEY,
    value  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS inbox_watermarks (
    owner        TEXT PRIMARY KEY,   -- 拉取方标识（API 密钥名），NULL 归属用 '__anon__'
    user_id      TEXT,
    watermark    INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    user_id           TEXT,
    conversation_id   TEXT,
    title             TEXT,
    agent_name        TEXT,
    api_key_name      TEXT,
    status            TEXT NOT NULL DEFAULT 'open',   -- open | closed
    meta              TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    last_message_at   TEXT,
    message_count     INTEGER NOT NULL DEFAULT 0,
    user_message_count  INTEGER NOT NULL DEFAULT 0,
    agent_message_count INTEGER NOT NULL DEFAULT 0,
    unread_for_agent  INTEGER NOT NULL DEFAULT 0,
    token_version     INTEGER NOT NULL DEFAULT 1,
    reply_expires_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_user    ON tasks(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS task_messages (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    seq         INTEGER,                -- 全局单调递增游标（按账号），供 /inbox 增量拉取
    role        TEXT NOT NULL,          -- agent | user | system
    author      TEXT,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}',
    source      TEXT                    -- email | web | api | system
);

CREATE INDEX IF NOT EXISTS idx_task_messages_task
    ON task_messages(task_id, created_at);
"""

# 老库升级用：表名 -> {列名: 列定义}
MIGRATIONS: dict[str, dict[str, str]] = {
    "mail_logs": {
        "idempotency_key": "TEXT",
        "template": "TEXT",
        "task_id": "TEXT",
        "reply_url": "TEXT",
        "user_id": "TEXT",
    },
    "api_keys": {
        "user_id": "TEXT",
    },
    "tasks": {
        "user_id": "TEXT",
        "conversation_id": "TEXT",
    },
    "task_messages": {
        "seq": "INTEGER",
    },
}

# 引用了「迁移新增列」的索引，必须在 ALTER TABLE 之后才能建。
POST_MIGRATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON tasks(conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_messages_seq ON task_messages(seq)",
)

# 单值设置的键名（不允许出现在 user_settings 里）
USER_SETTING_KEYS = ("smtp", "public_base_url", "test_recipients")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _db_file() -> Path:
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_file(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_logs_idem
               ON mail_logs(idempotency_key) WHERE idempotency_key IS NOT NULL"""
        )
        for ddl in POST_MIGRATE_INDEXES:
            conn.execute(ddl)
        _backfill_message_seq(conn)


def _backfill_message_seq(conn: sqlite3.Connection) -> None:
    """给老库补齐 seq，并把全局计数器推到当前最大值。"""
    conn.execute("INSERT OR IGNORE INTO counters(name, value) VALUES('msg_seq', 0)")
    row = conn.execute("SELECT value FROM counters WHERE name = 'msg_seq'").fetchone()
    known = int(row["value"]) if row else 0

    pending = conn.execute(
        "SELECT id FROM task_messages WHERE seq IS NULL ORDER BY created_at ASC, id ASC"
    ).fetchall()
    if pending:
        conn.executemany(
            "UPDATE task_messages SET seq = ? WHERE id = ?",
            [(known + offset, r["id"]) for offset, r in enumerate(pending, start=1)],
        )
        known += len(pending)

    highest = conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM task_messages").fetchone()["m"]
    conn.execute(
        "UPDATE counters SET value = ? WHERE name = 'msg_seq'", (max(known, int(highest or 0)),)
    )


def _next_seq(conn: sqlite3.Connection) -> int:
    """取下一个全局消息序号。

    计数器在同一个隐式事务里自增，WAL 下写锁保证串行，因此游标严格单调；
    不能用 rowid —— 删掉最后一行后 rowid 会被复用，游标会倒退。
    """
    conn.execute("INSERT OR IGNORE INTO counters(name, value) VALUES('msg_seq', 0)")
    conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'msg_seq'")
    row = conn.execute("SELECT value FROM counters WHERE name = 'msg_seq'").fetchone()
    return int(row["value"]) if row else 0


def _migrate(conn: sqlite3.Connection) -> None:
    """给已存在的旧库补列，保证升级不丢数据。"""
    for table, columns in MIGRATIONS.items():
        try:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if not existing:
            continue
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _scope(user_id: str | None, where: list[str], params: list[Any]) -> None:
    """把「归属过滤」追加进 where。

    传入具体 user_id → 只看该用户；传入 None → 只看无归属数据（绝不等于全表）。
    """
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    else:
        where.append("user_id IS NULL")


def _owned_user_id(user_id: str | None) -> str | None:
    return user_id or None


# ================================================================ users
_USER_FIELDS = (
    "username",
    "display_name",
    "password_hash",
    "role",
    "enabled",
    "session_version",
    "must_change_password",
    "note",
)
_USER_PUBLIC = (
    "id,username,display_name,role,enabled,note,created_at,updated_at,last_login_at,"
    "must_change_password"
)


def _user_public(row: sqlite3.Row | dict) -> dict[str, Any]:
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    d["must_change_password"] = bool(d.get("must_change_password"))
    d["is_admin"] = d.get("role") == "admin"
    return d


def create_user(
    *,
    username: str,
    password_hash: str,
    display_name: str = "",
    role: str = "user",
    note: str = "",
    must_change_password: bool = False,
) -> str:
    user_id = new_id("usr")
    stamp = now_iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO users
               (id,username,display_name,password_hash,role,enabled,session_version,
                must_change_password,note,created_at,updated_at)
               VALUES (?,?,?,?,?,1,1,?,?,?,?)""",
            (
                user_id,
                username,
                display_name or username,
                password_hash,
                role,
                1 if must_change_password else 0,
                note,
                stamp,
                stamp,
            ),
        )
    return user_id


def get_user(user_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
    cols = "*" if include_secret else _USER_PUBLIC
    with db() as conn:
        row = conn.execute(f"SELECT {cols} FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return None
    return dict(row) if include_secret else _user_public(row)


def get_user_by_username(username: str, *, include_secret: bool = False) -> dict[str, Any] | None:
    cols = "*" if include_secret else _USER_PUBLIC
    with db() as conn:
        row = conn.execute(
            f"SELECT {cols} FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
    if not row:
        return None
    return dict(row) if include_secret else _user_public(row)


def list_users() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(f"SELECT {_USER_PUBLIC} FROM users ORDER BY created_at ASC").fetchall()
    return [_user_public(r) for r in rows]


def count_users() -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def count_users_by_role(role: str) -> int:
    with db() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = ? AND enabled = 1", (role,)
            ).fetchone()[0]
        )


def first_admin() -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            f"SELECT {_USER_PUBLIC} FROM users WHERE role = 'admin' "
            "ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    return _user_public(row) if row else None


def update_user(user_id: str, **fields: Any) -> bool:
    updates = {k: v for k, v in fields.items() if k in _USER_FIELDS and v is not None}
    if not updates:
        return False
    for key in ("enabled", "must_change_password"):
        if key in updates:
            updates[key] = 1 if updates[key] else 0
    updates["updated_at"] = now_iso()
    sets = ",".join(f"{k} = ?" for k in updates)
    with db() as conn:
        cur = conn.execute(f"UPDATE users SET {sets} WHERE id = ?", (*updates.values(), user_id))
    return cur.rowcount > 0


def bump_session_version(user_id: str) -> int | None:
    """让该用户已发出的所有会话令牌立即失效。返回新版本号。"""
    with db() as conn:
        cur = conn.execute(
            "UPDATE users SET session_version = session_version + 1, updated_at = ? WHERE id = ?",
            (now_iso(), user_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT session_version FROM users WHERE id = ?", (user_id,)).fetchone()
    return int(row["session_version"]) if row else None


def touch_user_login(user_id: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), user_id),
        )


def delete_user(user_id: str) -> dict[str, int]:
    """删除账号，并连带清掉其密钥、发信记录、任务与会话消息。"""
    with db() as conn:
        task_ids = [
            r[0] for r in conn.execute("SELECT id FROM tasks WHERE user_id = ?", (user_id,))
        ]
        for tid in task_ids:
            conn.execute("DELETE FROM task_messages WHERE task_id = ?", (tid,))
        removed = {
            "messages": len(task_ids),
            "tasks": len(task_ids),
            "mail_logs": 0,
            "api_keys": 0,
        }
        removed["mail_logs"] = conn.execute(
            "DELETE FROM mail_logs WHERE user_id = ?", (user_id,)
        ).rowcount
        removed["api_keys"] = conn.execute(
            "DELETE FROM api_keys WHERE user_id = ?", (user_id,)
        ).rowcount
        conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return removed


# ================================================================ user settings
def get_user_settings(user_id: str | None) -> dict[str, Any]:
    """读取用户配置；没有记录返回空 dict（调用方自行回退到全局默认）。"""
    if not user_id:
        return {}
    with db() as conn:
        row = conn.execute(
            "SELECT data FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row["data"] or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_user_settings(user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """整块覆盖写入用户配置（只保留白名单键，避免写入奇怪字段）。"""
    clean = {k: v for k, v in (data or {}).items() if k in USER_SETTING_KEYS}
    payload = json.dumps(clean, ensure_ascii=False)
    stamp = now_iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO user_settings (user_id, data, updated_at) VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
            (user_id, payload, stamp),
        )
    return clean


# ================================================================ api keys
def insert_api_key(
    name: str,
    key_hash: str,
    prefix: str,
    scopes: list[str],
    note: str = "",
    user_id: str | None = None,
) -> str:
    key_id = new_id("key")
    with db() as conn:
        conn.execute(
            """INSERT INTO api_keys (id,user_id,name,key_hash,prefix,scopes,enabled,note,created_at)
               VALUES (?,?,?,?,?,?,1,?,?)""",
            (key_id, _owned_user_id(user_id), name, key_hash, prefix, ",".join(scopes), note, now_iso()),
        )
    return key_id


def list_api_keys(user_id: str | None = None) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    _scope(user_id, where, params)
    with db() as conn:
        rows = conn.execute(
            f"""SELECT id,user_id,name,prefix,scopes,enabled,note,created_at,last_used_at,call_count
                FROM api_keys WHERE {' AND '.join(where)} ORDER BY created_at DESC""",
            params,
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["scopes"] = [s for s in (d["scopes"] or "").split(",") if s]
        d["enabled"] = bool(d["enabled"])
        out.append(d)
    return out


def find_api_key_by_hash(key_hash: str) -> dict[str, Any] | None:
    """按密钥哈希查记录（鉴权入口，跨用户，不带归属过滤）。"""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND enabled = 1", (key_hash,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["scopes"] = [s for s in (d["scopes"] or "").split(",") if s]
    return d


def touch_api_key(key_id: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE api_keys SET last_used_at = ?, call_count = call_count + 1 WHERE id = ?",
            (now_iso(), key_id),
        )


def set_api_key_enabled(
    key_id: str, enabled: bool, user_id: str | None = None, *, scoped: bool = False
) -> bool:
    """``scoped=True`` 时只允许操作本账号的密钥（控制台/Agent 一律用它）。"""
    where = ["id = ?"]
    params: list[Any] = [1 if enabled else 0, key_id]
    if scoped:
        _scope(user_id, where, params)
    with db() as conn:
        cur = conn.execute(
            f"UPDATE api_keys SET enabled = ? WHERE {' AND '.join(where)}", params
        )
    return cur.rowcount > 0


def delete_api_key(key_id: str, user_id: str | None = None, *, scoped: bool = False) -> bool:
    where = ["id = ?"]
    params: list[Any] = [key_id]
    if scoped:
        _scope(user_id, where, params)
    with db() as conn:
        cur = conn.execute(f"DELETE FROM api_keys WHERE {' AND '.join(where)}", params)
    return cur.rowcount > 0


def count_api_keys(user_id: str | None = None) -> int:
    where: list[str] = []
    params: list[Any] = []
    _scope(user_id, where, params)
    with db() as conn:
        return int(
            conn.execute(
                f"SELECT COUNT(*) FROM api_keys WHERE {' AND '.join(where)}", params
            ).fetchone()[0]
        )


def find_api_key_by_name(name: str, user_id: str | None = None) -> dict[str, Any] | None:
    """按名称在本账号下找密钥（用于「同名密钥已存在」之类的判断）。"""
    where = ["name = ?"]
    params: list[Any] = [name]
    _scope(user_id, where, params)
    with db() as conn:
        row = conn.execute(
            f"SELECT * FROM api_keys WHERE {' AND '.join(where)} LIMIT 1", params
        ).fetchone()
    return dict(row) if row else None


# ================================================================ mail logs
def insert_mail_log(**fields: Any) -> str:
    log_id = fields.pop("id", None) or new_id("mail")
    payload = {
        "id": log_id,
        "user_id": _owned_user_id(fields.get("user_id")),
        "created_at": fields.get("created_at") or now_iso(),
        "request_id": fields.get("request_id"),
        "client_ip": fields.get("client_ip"),
        "api_key_name": fields.get("api_key_name"),
        "sender": fields.get("sender"),
        "to_addrs": json.dumps(fields.get("to_addrs") or [], ensure_ascii=False),
        "cc_addrs": json.dumps(fields.get("cc_addrs") or [], ensure_ascii=False),
        "bcc_addrs": json.dumps(fields.get("bcc_addrs") or [], ensure_ascii=False),
        "subject": fields.get("subject"),
        "status": fields.get("status", "unknown"),
        "error": fields.get("error"),
        "error_code": fields.get("error_code"),
        "message_id": fields.get("message_id"),
        "latency_ms": int(fields.get("latency_ms") or 0),
        "size_bytes": int(fields.get("size_bytes") or 0),
        "attachments": json.dumps(fields.get("attachments") or [], ensure_ascii=False),
        "body_preview": fields.get("body_preview"),
        "attempts": int(fields.get("attempts") or 1),
        "idempotency_key": fields.get("idempotency_key"),
        "template": fields.get("template"),
        "task_id": fields.get("task_id"),
        "reply_url": fields.get("reply_url"),
    }
    cols = ",".join(payload.keys())
    marks = ",".join("?" for _ in payload)
    with db() as conn:
        conn.execute(f"INSERT INTO mail_logs ({cols}) VALUES ({marks})", tuple(payload.values()))
    return log_id


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k in ("to_addrs", "cc_addrs", "bcc_addrs", "attachments"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except json.JSONDecodeError:
            d[k] = []
    return d


def list_mail_logs(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    q: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    _scope(user_id, where, params)
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        where.append("(subject LIKE ? OR to_addrs LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    clause = f"WHERE {' AND '.join(where)}"

    with db() as conn:
        total = int(
            conn.execute(f"SELECT COUNT(*) FROM mail_logs {clause}", params).fetchone()[0]
        )
        rows = conn.execute(
            f"""SELECT * FROM mail_logs {clause}
                ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [_decode(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


def get_mail_log(log_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    """按归属取单条发信记录（永远按 user_id 过滤，不会跨用户读到别人的记录）。"""
    where = ["id = ?"]
    params: list[Any] = [log_id]
    _scope(user_id, where, params)
    with db() as conn:
        row = conn.execute(
            f"SELECT * FROM mail_logs WHERE {' AND '.join(where)}", params
        ).fetchone()
    return _decode(row) if row else None


def find_mail_log_by_idempotency(key: str, user_id: str | None = None) -> dict[str, Any] | None:
    """幂等键按用户隔离：两个用户用同一个 key 互不影响。"""
    where = ["idempotency_key = ?"]
    params: list[Any] = [key]
    _scope(user_id, where, params)
    with db() as conn:
        row = conn.execute(
            f"SELECT * FROM mail_logs WHERE {' AND '.join(where)}", params
        ).fetchone()
    return _decode(row) if row else None


def mail_stats(user_id: str | None = None) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    _scope(user_id, where, params)
    clause = f"WHERE {' AND '.join(where)}"
    with db() as conn:
        rows = conn.execute(
            f"SELECT status, COUNT(*) AS c FROM mail_logs {clause} GROUP BY status", params
        ).fetchall()
        total = int(conn.execute(f"SELECT COUNT(*) FROM mail_logs {clause}", params).fetchone()[0])
        today = now_iso()[:10]
        today_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM mail_logs {clause} AND substr(created_at,1,10) = ?",
                params + [today],
            ).fetchone()[0]
        )
        avg = conn.execute(
            f"SELECT AVG(latency_ms) FROM mail_logs {clause} AND status = 'sent'", params
        ).fetchone()[0]
    by_status = {r["status"]: r["c"] for r in rows}
    return {
        "total": total,
        "today": today_count,
        "sent": by_status.get("sent", 0),
        "failed": by_status.get("failed", 0),
        "avg_latency_ms": round(float(avg or 0)),
        "by_status": by_status,
    }


def recent_hour_count(minutes: int = 60, user_id: str | None = None) -> int:
    """用于简单的滑动窗口配额统计。"""
    from datetime import timedelta

    since = (
        datetime.now(timezone.utc).astimezone() - timedelta(minutes=minutes)
    ).isoformat(timespec="seconds")
    where = ["created_at >= ?"]
    params: list[Any] = [since]
    _scope(user_id, where, params)
    with db() as conn:
        return int(
            conn.execute(
                f"SELECT COUNT(*) FROM mail_logs WHERE {' AND '.join(where)}", params
            ).fetchone()[0]
        )


# ================================================================ tasks
def _task_row(row: sqlite3.Row) -> dict[str, Any]:
    task = dict(row)
    try:
        task["meta"] = json.loads(task.get("meta") or "{}")
    except json.JSONDecodeError:
        task["meta"] = {}
    return task


def create_task(
    *,
    title: str | None,
    agent_name: str | None,
    api_key_name: str | None,
    meta: dict[str, Any] | None = None,
    reply_expires_at: str | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> str:
    task_id = new_id("task")
    stamp = now_iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO tasks
               (id,user_id,conversation_id,title,agent_name,api_key_name,status,meta,
                created_at,updated_at,reply_expires_at)
               VALUES (?,?,?,?,?,?,'open',?,?,?,?)""",
            (
                task_id,
                _owned_user_id(user_id),
                conversation_id,
                title,
                agent_name,
                api_key_name,
                json.dumps(meta or {}, ensure_ascii=False),
                stamp,
                stamp,
                reply_expires_at,
            ),
        )
        if conversation_id:
            conn.execute(
                """UPDATE conversations
                   SET task_count = task_count + 1, updated_at = ?
                 WHERE id = ?""",
                (stamp, conversation_id),
            )
    return task_id


def get_task(task_id: str, user_id: str | None = None, *, scoped: bool = False) -> dict[str, Any] | None:
    """取任务。

    ``scoped=False``（默认）不带归属过滤 —— 供回复页按令牌打开的路径使用；
    控制台/Agent 侧一律传 ``scoped=True`` + 自己的 user_id，越权直接当作不存在。
    """
    where = ["id = ?"]
    params: list[Any] = [task_id]
    if scoped:
        _scope(user_id, where, params)
    with db() as conn:
        row = conn.execute(
            f"SELECT * FROM tasks WHERE {' AND '.join(where)}", params
        ).fetchone()
    return _task_row(row) if row else None


def list_tasks(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    q: str | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    _scope(user_id, where, params)
    if status:
        where.append("status = ?")
        params.append(status)
    if conversation_id:
        where.append("conversation_id = ?")
        params.append(conversation_id)
    if q:
        where.append("(title LIKE ? OR id LIKE ? OR agent_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    clause = f"WHERE {' AND '.join(where)}"

    with db() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM tasks {clause}", params).fetchone()[0])
        rows = conn.execute(
            f"""SELECT * FROM tasks {clause}
                ORDER BY COALESCE(last_message_at, created_at) DESC LIMIT ? OFFSET ?""",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [_task_row(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


def update_task(task_id: str, **fields: Any) -> bool:
    allowed = {
        "title",
        "agent_name",
        "status",
        "meta",
        "reply_expires_at",
        "token_version",
        "conversation_id",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False
    if "meta" in updates and not isinstance(updates["meta"], str):
        updates["meta"] = json.dumps(updates["meta"], ensure_ascii=False)
    updates["updated_at"] = now_iso()
    sets = ",".join(f"{k} = ?" for k in updates)
    with db() as conn:
        before = None
        if "conversation_id" in updates:
            row = conn.execute(
                "SELECT conversation_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            before = row["conversation_id"] if row else None
        cur = conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*updates.values(), task_id))
        if before != updates.get("conversation_id"):
            for convo_id in {before, updates.get("conversation_id")} - {None}:
                _recount_conversation(conn, convo_id)
    return cur.rowcount > 0


def rotate_reply_token(task_id: str) -> int | None:
    """令牌版本 +1，旧链接立即失效。返回新版本号。"""
    with db() as conn:
        cur = conn.execute(
            "UPDATE tasks SET token_version = token_version + 1, updated_at = ? WHERE id = ?",
            (now_iso(), task_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT token_version FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return int(row["token_version"]) if row else None


def add_task_message(
    *,
    task_id: str,
    role: str,
    content: str,
    author: str | None = None,
    source: str = "api",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message_id = new_id("msg")
    stamp = now_iso()
    is_user = role == "user"
    with db() as conn:
        seq = _next_seq(conn)
        conn.execute(
            """INSERT INTO task_messages (id,task_id,seq,role,author,content,created_at,meta,source)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                message_id,
                task_id,
                seq,
                role,
                author,
                content,
                stamp,
                json.dumps(meta or {}, ensure_ascii=False),
                source,
            ),
        )
        conn.execute(
            """UPDATE tasks SET
                 updated_at = ?, last_message_at = ?,
                 message_count = message_count + 1,
                 user_message_count = user_message_count + ?,
                 agent_message_count = agent_message_count + ?,
                 unread_for_agent = unread_for_agent + ?
               WHERE id = ?""",
            (stamp, stamp, 1 if is_user else 0, 0 if is_user else 1, 1 if is_user else 0, task_id),
        )
        row = conn.execute("SELECT conversation_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conversation_id = row["conversation_id"] if row else None
        if conversation_id:
            conn.execute(
                """UPDATE conversations SET
                     updated_at = ?, last_message_at = ?,
                     message_count = message_count + 1,
                     user_message_count = user_message_count + ?,
                     agent_message_count = agent_message_count + ?,
                     unread_for_agent = unread_for_agent + ?
                   WHERE id = ?""",
                (
                    stamp,
                    stamp,
                    1 if is_user else 0,
                    0 if is_user else 1,
                    1 if is_user else 0,
                    conversation_id,
                ),
            )
    return {
        "id": message_id,
        "task_id": task_id,
        "seq": seq,
        "role": role,
        "author": author,
        "content": content,
        "created_at": stamp,
        "source": source,
        "meta": meta or {},
    }


def _message_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["meta"] = json.loads(d.get("meta") or "{}")
    except json.JSONDecodeError:
        d["meta"] = {}
    return d


def list_task_messages(
    task_id: str, after_id: str | None = None, limit: int = 200, role: str | None = None
) -> list[dict[str, Any]]:
    where = ["task_id = ?"]
    params: list[Any] = [task_id]
    if role:
        where.append("role = ?")
        params.append(role)
    if after_id:
        # 用 created_at+id 定位，避免依赖自增序号
        with db() as conn:
            anchor = conn.execute(
                "SELECT created_at FROM task_messages WHERE id = ?", (after_id,)
            ).fetchone()
        if anchor:
            where.append("(created_at > ? OR (created_at = ? AND id > ?))")
            params += [anchor["created_at"], anchor["created_at"], after_id]
    params.append(max(1, min(limit, 500)))
    with db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM task_messages WHERE {' AND '.join(where)}
                ORDER BY created_at ASC, id ASC LIMIT ?""",
            params,
        ).fetchall()
    return [_message_row(r) for r in rows]


def mark_task_read(task_id: str) -> None:
    with db() as conn:
        conn.execute("UPDATE tasks SET unread_for_agent = 0 WHERE id = ?", (task_id,))


def delete_task(task_id: str) -> bool:
    """彻底删除任务及其全部会话消息（不可恢复）。"""
    with db() as conn:
        row = conn.execute("SELECT conversation_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conversation_id = row["conversation_id"] if row else None
        conn.execute("DELETE FROM task_messages WHERE task_id = ?", (task_id,))
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        # 解除邮件日志上的关联，保留发信记录本身
        conn.execute(
            "UPDATE mail_logs SET task_id = NULL, reply_url = NULL WHERE task_id = ?",
            (task_id,),
        )
        if conversation_id:
            _recount_conversation(conn, conversation_id)
        return cur.rowcount > 0


def _recount_conversation(conn: sqlite3.Connection, conversation_id: str) -> None:
    """按当前挂在该会话下的任务重新汇总计数（删除任务后调用）。"""
    row = conn.execute(
        """SELECT COUNT(*)                          AS task_count,
                  COALESCE(SUM(message_count),0)       AS message_count,
                  COALESCE(SUM(user_message_count),0)  AS user_message_count,
                  COALESCE(SUM(agent_message_count),0) AS agent_message_count,
                  COALESCE(SUM(unread_for_agent),0)    AS unread_for_agent,
                  MAX(COALESCE(last_message_at, created_at)) AS last_message_at
             FROM tasks WHERE conversation_id = ?""",
        (conversation_id,),
    ).fetchone()
    conn.execute(
        """UPDATE conversations SET
             task_count = ?, message_count = ?, user_message_count = ?,
             agent_message_count = ?, unread_for_agent = ?, last_message_at = ?, updated_at = ?
           WHERE id = ?""",
        (
            int(row["task_count"]),
            int(row["message_count"]),
            int(row["user_message_count"]),
            int(row["agent_message_count"]),
            int(row["unread_for_agent"]),
            row["last_message_at"],
            now_iso(),
            conversation_id,
        ),
    )


def task_stats(user_id: str | None = None) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    _scope(user_id, where, params)
    clause = f"WHERE {' AND '.join(where)}"
    with db() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM tasks {clause}", params).fetchone()[0])
        open_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM tasks {clause} AND status = 'open'", params
            ).fetchone()[0]
        )
        unread = int(
            conn.execute(
                f"SELECT COALESCE(SUM(unread_for_agent),0) FROM tasks {clause}", params
            ).fetchone()[0]
        )
        waiting = int(
            conn.execute(
                f"SELECT COUNT(*) FROM tasks {clause} AND status='open' AND unread_for_agent > 0",
                params,
            ).fetchone()[0]
        )
        messages = int(
            conn.execute(
                f"""SELECT COUNT(*) FROM task_messages WHERE task_id IN
                    (SELECT id FROM tasks {clause})""",
                params,
            ).fetchone()[0]
        )
    return {
        "total": total,
        "open": open_count,
        "closed": total - open_count,
        "waiting_reply_tasks": waiting,
        "unread_user_messages": unread,
        "messages": messages,
    }


# ============================================================ conversations
# 「对话」是比任务更高一层的容器：Agent 侧的一个会话（如 codex 客户端里的一个对话）
# 对应这里的一条 conversation，其下可以挂多条任务（每封带回复链接的邮件 = 一条任务线程）。
# external_id 是 Agent 侧的对话标识，靠它做幂等 ensure。


def _conversation_row(row: sqlite3.Row) -> dict[str, Any]:
    convo = dict(row)
    try:
        convo["meta"] = json.loads(convo.get("meta") or "{}")
    except json.JSONDecodeError:
        convo["meta"] = {}
    return convo


def create_conversation(
    *,
    external_id: str | None = None,
    title: str | None = None,
    agent_name: str | None = None,
    api_key_name: str | None = None,
    meta: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> str:
    conversation_id = new_id("conv")
    stamp = now_iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO conversations
               (id,user_id,external_id,title,agent_name,api_key_name,status,meta,created_at,updated_at)
               VALUES (?,?,?,?,?,?,'open',?,?,?)""",
            (
                conversation_id,
                _owned_user_id(user_id),
                external_id,
                title,
                agent_name,
                api_key_name,
                json.dumps(meta or {}, ensure_ascii=False),
                stamp,
                stamp,
            ),
        )
    return conversation_id


def get_conversation(
    conversation_id: str, user_id: str | None = None, *, scoped: bool = False
) -> dict[str, Any] | None:
    where = ["id = ?"]
    params: list[Any] = [conversation_id]
    if scoped:
        _scope(user_id, where, params)
    with db() as conn:
        row = conn.execute(
            f"SELECT * FROM conversations WHERE {' AND '.join(where)}", params
        ).fetchone()
    return _conversation_row(row) if row else None


def find_conversation_by_external_id(
    external_id: str, user_id: str | None = None
) -> dict[str, Any] | None:
    where = ["external_id = ?"]
    params: list[Any] = [external_id]
    _scope(user_id, where, params)
    with db() as conn:
        row = conn.execute(
            f"SELECT * FROM conversations WHERE {' AND '.join(where)}", params
        ).fetchone()
    return _conversation_row(row) if row else None


def ensure_conversation(
    *,
    external_id: str | None = None,
    title: str | None = None,
    agent_name: str | None = None,
    api_key_name: str | None = None,
    meta: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """幂等创建/复用会话：带 external_id 时，已存在就复用，否则新建。

    返回 ``(conversation, created)``。并发下靠 idx_conversations_ext 唯一索引兜底。
    """
    if external_id:
        existing = find_conversation_by_external_id(external_id, user_id)
        if existing:
            patch: dict[str, Any] = {}
            if title and not existing.get("title"):
                patch["title"] = title
            if agent_name and not existing.get("agent_name"):
                patch["agent_name"] = agent_name
            if meta:
                merged = {**(existing.get("meta") or {}), **meta}
                patch["meta"] = merged
            if patch:
                update_conversation(existing["id"], **patch)
                existing = get_conversation(existing["id"]) or existing
            return existing, False

    conversation_id = create_conversation(
        external_id=external_id,
        title=title,
        agent_name=agent_name,
        api_key_name=api_key_name,
        meta=meta,
        user_id=user_id,
    )
    created = get_conversation(conversation_id) or {}
    return created, True


def list_conversations(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    q: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    _scope(user_id, where, params)
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        where.append("(title LIKE ? OR id LIKE ? OR external_id LIKE ? OR agent_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
    clause = f"WHERE {' AND '.join(where)}"

    with db() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM conversations {clause}", params).fetchone()[0])
        rows = conn.execute(
            f"""SELECT * FROM conversations {clause}
                ORDER BY COALESCE(last_message_at, updated_at) DESC LIMIT ? OFFSET ?""",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [_conversation_row(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


def update_conversation(conversation_id: str, **fields: Any) -> bool:
    allowed = {"title", "agent_name", "status", "meta", "external_id"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False
    if "meta" in updates and not isinstance(updates["meta"], str):
        updates["meta"] = json.dumps(updates["meta"], ensure_ascii=False)
    updates["updated_at"] = now_iso()
    sets = ",".join(f"{k} = ?" for k in updates)
    with db() as conn:
        cur = conn.execute(
            f"UPDATE conversations SET {sets} WHERE id = ?", (*updates.values(), conversation_id)
        )
    return cur.rowcount > 0


def list_conversation_tasks(conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM tasks WHERE conversation_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (conversation_id, max(1, min(limit, 200))),
        ).fetchall()
    return [_task_row(r) for r in rows]


def delete_conversation(conversation_id: str) -> bool:
    """删除会话本身；其下任务只解除关联，不连带删除（避免误删会话历史）。"""
    with db() as conn:
        conn.execute(
            "UPDATE tasks SET conversation_id = NULL WHERE conversation_id = ?", (conversation_id,)
        )
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cur.rowcount > 0


def conversation_stats(user_id: str | None = None) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    _scope(user_id, where, params)
    clause = f"WHERE {' AND '.join(where)}"
    with db() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM conversations {clause}", params).fetchone()[0])
        open_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM conversations {clause} AND status = 'open'", params
            ).fetchone()[0]
        )
        unread = int(
            conn.execute(
                f"SELECT COALESCE(SUM(unread_for_agent),0) FROM conversations {clause}", params
            ).fetchone()[0]
        )
    return {"total": total, "open": open_count, "closed": total - open_count, "unread": unread}


# ==================================================================== inbox
# 定时任务（cron）的拉取入口：一次拿全部会话的增量用户回复，再按 conversation_id 分发。
# 游标是 task_messages.seq —— 全局单调递增，不依赖调用方本地状态。


def current_seq() -> int:
    with db() as conn:
        row = conn.execute("SELECT COALESCE(MAX(seq),0) AS m FROM task_messages").fetchone()
    return int(row["m"] or 0)


def inbox_fetch(
    *,
    user_id: str | None,
    cursor: int = 0,
    limit: int = 50,
    role: str | None = "user",
) -> tuple[list[dict[str, Any]], int, bool]:
    """取 seq > cursor 的消息（默认只取用户回复），按 seq 升序。"""
    where = ["t.user_id " + ("= ?" if user_id else "IS NULL"), "tm.seq > ?"]
    params: list[Any] = [user_id] if user_id else []
    params.append(max(0, int(cursor)))
    if role:
        where.append("tm.role = ?")
        params.append(role)

    size = max(1, min(limit, 200))
    with db() as conn:
        rows = conn.execute(
            f"""SELECT tm.id AS message_id, tm.seq AS seq, tm.role AS role,
                       tm.author AS author, tm.content AS content,
                       tm.created_at AS created_at, tm.source AS source,
                       tm.meta AS message_meta,
                       t.id AS task_id, t.title AS task_title, t.status AS task_status,
                       t.agent_name AS task_agent_name, t.conversation_id AS conversation_id,
                       c.external_id AS conversation_external_id,
                       c.title AS conversation_title,
                       c.status AS conversation_status
                  FROM task_messages tm
                  JOIN tasks t ON t.id = tm.task_id
             LEFT JOIN conversations c ON c.id = t.conversation_id
                 WHERE {' AND '.join(where)}
              ORDER BY tm.seq ASC
                 LIMIT ?""",
            params + [size + 1],
        ).fetchall()

    has_more = len(rows) > size
    rows = rows[:size]
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["message_meta"] = json.loads(item.get("message_meta") or "{}")
        except json.JSONDecodeError:
            item["message_meta"] = {}
        items.append(item)
    next_cursor = int(items[-1]["seq"]) if items else max(0, int(cursor))
    return items, next_cursor, has_more


def _watermark_owner(owner: str | None) -> str:
    return owner or "__anon__"


def get_inbox_watermark(owner: str | None, user_id: str | None = None) -> int:
    key = _watermark_owner(owner)
    where = ["owner = ?"]
    params: list[Any] = [key]
    _scope(user_id, where, params)
    with db() as conn:
        row = conn.execute(
            f"SELECT watermark FROM inbox_watermarks WHERE {' AND '.join(where)}", params
        ).fetchone()
    return int(row["watermark"]) if row else 0


def set_inbox_watermark(owner: str | None, watermark: int, user_id: str | None = None) -> int:
    """推进水位；只增不减，避免旧调用方把游标拉回去导致重复投递。"""
    key = _watermark_owner(owner)
    stamp = now_iso()
    target = max(0, int(watermark))
    with db() as conn:
        existing = conn.execute(
            "SELECT watermark FROM inbox_watermarks WHERE owner = ?", (key,)
        ).fetchone()
        if existing and int(existing["watermark"]) >= target:
            return int(existing["watermark"])
        conn.execute(
            """INSERT INTO inbox_watermarks (owner,user_id,watermark,updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(owner) DO UPDATE SET watermark = excluded.watermark,
                                                user_id   = excluded.user_id,
                                                updated_at = excluded.updated_at""",
            (key, _owned_user_id(user_id), target, stamp),
        )
    return target


def mark_read_upto(user_id: str | None, watermark: int) -> int:
    """把游标以内的用户回复视为「已被 Agent 取回」，清掉控制台的待回复计数。

    注意与 inbox 水位是两件事：水位管「cron 分发到哪了」，unread 管「控制台还显不显示待处理」。
    """
    clause = "t.user_id = ?" if user_id else "t.user_id IS NULL"
    params: list[Any] = [user_id] if user_id else []
    cutoff = max(0, int(watermark))
    with db() as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT t.id AS tid, t.conversation_id AS cid
                  FROM tasks t JOIN task_messages tm ON tm.task_id = t.id
                 WHERE {clause} AND tm.role = 'user' AND tm.seq <= ?
                   AND t.unread_for_agent > 0""",
            params + [cutoff],
        ).fetchall()
        if not rows:
            return 0
        task_ids = [r["tid"] for r in rows]
        convo_ids = sorted({r["cid"] for r in rows if r["cid"]})
        conn.execute(
            f"UPDATE tasks SET unread_for_agent = 0 WHERE id IN ({','.join('?' * len(task_ids))})",
            task_ids,
        )
        if convo_ids:
            # 会话的未读直接按旗下任务重算，避免漏掉仍未取回的消息
            conn.execute(
                f"""UPDATE conversations SET unread_for_agent = (
                        SELECT COALESCE(SUM(unread_for_agent),0) FROM tasks
                         WHERE conversation_id = conversations.id)
                     WHERE id IN ({','.join('?' * len(convo_ids))})""",
                convo_ids,
            )
    return len(task_ids)
