"""
轻量存储层：直接用标准库 sqlite3（WAL 模式），不引入 ORM。
两张表：api_keys（密钥）、mail_logs（发信记录）。
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
CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS mail_logs (
    id            TEXT PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS task_messages (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
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
    },
}


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


# ---------------------------------------------------------------- api keys
def insert_api_key(
    name: str, key_hash: str, prefix: str, scopes: list[str], note: str = ""
) -> str:
    key_id = new_id("key")
    with db() as conn:
        conn.execute(
            """INSERT INTO api_keys (id, name, key_hash, prefix, scopes, enabled, note, created_at)
               VALUES (?,?,?,?,?,1,?,?)""",
            (key_id, name, key_hash, prefix, ",".join(scopes), note, now_iso()),
        )
    return key_id


def list_api_keys() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """SELECT id,name,prefix,scopes,enabled,note,created_at,last_used_at,call_count
               FROM api_keys ORDER BY created_at DESC"""
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["scopes"] = [s for s in (d["scopes"] or "").split(",") if s]
        d["enabled"] = bool(d["enabled"])
        out.append(d)
    return out


def find_api_key_by_hash(key_hash: str) -> dict[str, Any] | None:
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


def set_api_key_enabled(key_id: str, enabled: bool) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET enabled = ? WHERE id = ?", (1 if enabled else 0, key_id)
        )
    return cur.rowcount > 0


def delete_api_key(key_id: str) -> bool:
    with db() as conn:
        cur = conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
    return cur.rowcount > 0


def count_api_keys() -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0])


# ---------------------------------------------------------------- mail logs
def insert_mail_log(**fields: Any) -> str:
    log_id = fields.pop("id", None) or new_id("mail")
    payload = {
        "id": log_id,
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
    page: int = 1, page_size: int = 20, status: str | None = None, q: str | None = None
) -> dict[str, Any]:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        where.append("(subject LIKE ? OR to_addrs LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    clause = f"WHERE {' AND '.join(where)}" if where else ""

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


def get_mail_log(log_id: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM mail_logs WHERE id = ?", (log_id,)).fetchone()
    return _decode(row) if row else None


def find_mail_log_by_idempotency(key: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM mail_logs WHERE idempotency_key = ?", (key,)
        ).fetchone()
    return _decode(row) if row else None


def mail_stats() -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM mail_logs GROUP BY status"
        ).fetchall()
        total = int(conn.execute("SELECT COUNT(*) FROM mail_logs").fetchone()[0])
        today = now_iso()[:10]
        today_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM mail_logs WHERE substr(created_at,1,10) = ?", (today,)
            ).fetchone()[0]
        )
        avg = conn.execute(
            "SELECT AVG(latency_ms) FROM mail_logs WHERE status = 'sent'"
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


def recent_hour_count(minutes: int = 60) -> int:
    """用于简单的滑动窗口配额统计。"""
    from datetime import timedelta

    since = (
        datetime.now(timezone.utc).astimezone() - timedelta(minutes=minutes)
    ).isoformat(timespec="seconds")
    with db() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM mail_logs WHERE created_at >= ?", (since,)
            ).fetchone()[0]
        )


# ---------------------------------------------------------------- tasks
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
) -> str:
    task_id = new_id("task")
    stamp = now_iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO tasks
               (id,title,agent_name,api_key_name,status,meta,created_at,updated_at,reply_expires_at)
               VALUES (?,?,?,?,'open',?,?,?,?)""",
            (
                task_id,
                title,
                agent_name,
                api_key_name,
                json.dumps(meta or {}, ensure_ascii=False),
                stamp,
                stamp,
                reply_expires_at,
            ),
        )
    return task_id


def get_task(task_id: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _task_row(row) if row else None


def list_tasks(
    page: int = 1, page_size: int = 20, status: str | None = None, q: str | None = None
) -> dict[str, Any]:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        where.append("(title LIKE ? OR id LIKE ? OR agent_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    clause = f"WHERE {' AND '.join(where)}" if where else ""

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
    allowed = {"title", "agent_name", "status", "meta", "reply_expires_at", "token_version"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False
    if "meta" in updates and not isinstance(updates["meta"], str):
        updates["meta"] = json.dumps(updates["meta"], ensure_ascii=False)
    updates["updated_at"] = now_iso()
    sets = ",".join(f"{k} = ?" for k in updates)
    with db() as conn:
        cur = conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*updates.values(), task_id))
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
        conn.execute(
            """INSERT INTO task_messages (id,task_id,role,author,content,created_at,meta,source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                message_id,
                task_id,
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
    return {
        "id": message_id,
        "task_id": task_id,
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
        conn.execute("DELETE FROM task_messages WHERE task_id = ?", (task_id,))
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        # 解除邮件日志上的关联，保留发信记录本身
        conn.execute(
            "UPDATE mail_logs SET task_id = NULL, reply_url = NULL WHERE task_id = ?",
            (task_id,),
        )
        return cur.rowcount > 0


def task_stats() -> dict[str, Any]:
    with db() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        open_count = int(
            conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'open'").fetchone()[0]
        )
        unread = int(conn.execute("SELECT COALESCE(SUM(unread_for_agent),0) FROM tasks").fetchone()[0])
        waiting = int(
            conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='open' AND unread_for_agent > 0"
            ).fetchone()[0]
        )
        messages = int(conn.execute("SELECT COUNT(*) FROM task_messages").fetchone()[0])
    return {
        "total": total,
        "open": open_count,
        "closed": total - open_count,
        "waiting_reply_tasks": waiting,
        "unread_user_messages": unread,
        "messages": messages,
    }
