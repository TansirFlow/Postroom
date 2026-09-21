"""任务会话 + 邮件内免登录回复链接 端到端回归测试。

覆盖：建任务 → Agent 发消息 → 邮件带链接 → 用户网页回帖 → Agent 轮询取回
     → 令牌轮换/篡改/关闭/删除等安全路径。

前置：后端已在 http://127.0.0.1:8077 运行。密钥与收件人**通过环境变量传入**，
仓库里不含任何默认凭据。

用法（在项目根目录下）：

    # 仅本地链路（不发信）
    AGENT_API_KEY=sk-agent-xxx backend\\.venv\\Scripts\\python.exe tests\\test_task_flow.py

    # 额外真实发一封测试邮件，必须显式指定收件人
    AGENT_API_KEY=sk-agent-xxx TEST_RECIPIENT=you@example.com \\
        backend\\.venv\\Scripts\\python.exe tests\\test_task_flow.py --send
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = os.environ.get("AGENT_API_BASE", "http://127.0.0.1:8077")
KEY = os.environ.get("AGENT_API_KEY", "")
DO_SEND = "--send" in sys.argv
# --send 时必须显式给出收件人，仓库里不存任何真实邮箱
SEND_TO = os.environ.get("TEST_RECIPIENT", "")
PASS, FAIL = [], []

if not KEY:
    sys.exit("缺少 AGENT_API_KEY 环境变量（密钥见 backend/data/keys.txt）")
if DO_SEND and not SEND_TO:
    sys.exit("--send 需要同时给出 TEST_RECIPIENT=<收件邮箱>")

# 允许直接 import app.services.replylink 做纯函数校验
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


def call(method, path, body=None, auth=True):
    req = urllib.request.Request(BASE + path, method=method)
    if auth:
        req.add_header("X-API-Key", KEY)
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  {extra}" if extra else ""))


# ---------- 0) 纯函数：邮件正文装饰 ----------
print("0) 邮件正文装饰 replylink.decorate()")
from app.services import replylink  # noqa: E402

URL = "http://127.0.0.1:8077/reply/TOKEN123"
text_body, html_body = replylink.decorate(
    text="季度报表已生成。",
    html="<p>季度报表已生成。</p>",
    url=URL,
    expires_at=datetime.now(timezone.utc),
    title="季度数据报表生成",
)
check("纯文本正文含回复链接", URL in text_body)
check("HTML 正文含回复链接", URL in html_body)
check("HTML 正文含可点击按钮", "<a " in html_body and "回复" in html_body)

# ---------- 1) 创建任务 ----------
print("\n1) 创建任务（Agent 启动时拿 task_id）")
st, r = call("POST", "/api/v1/tasks", {"title": "回归 · 任务闭环校验", "agent_name": "regression-bot", "meta": {"case": "e2e"}})
d = r.get("data") or {}
tid, link = d.get("task_id"), d.get("reply_url")
tok = (link or "").rsplit("/", 1)[-1]
check("POST /tasks 201", st == 201, f"status={st}")
check("返回 task_id", bool(tid), tid)
check("返回 id 别名（与列表 items[].id 一致）", d.get("id") == tid)
check("返回 reply_url", bool(link and "/reply/" in link), link)
check("返回 reply_expires_at", bool(d.get("reply_expires_at")), d.get("reply_expires_at"))

# ---------- 2) Agent 发消息（notify_email 需为数组） ----------
print("\n2) Agent 向线程发消息")
st, r = call("POST", f"/api/v1/tasks/{tid}/messages", {"content": "回归测试：请点回复链接确认部署规格。"})
check("POST messages 201", st == 201, f"status={st}")
check("消息 role=agent", ((r.get("data") or {}).get("message") or {}).get("role") == "agent")

# ---------- 3) 邮件内嵌回复链接（可选真实发信） ----------
if DO_SEND:
    print("\n3) 真实发信：邮件正文自动附带回复链接")
    st, r = call("POST", "/api/v1/mail/send", {
        "to": [SEND_TO],
        "subject": "[回归] 任务会话邮件内嵌回复链接",
        "body": "这是一封回归测试邮件，正文末尾应自动附带「点开即回复」按钮。",
        "task_id": tid,
        "attach_reply_link": True,
    })
    d = r.get("data") or {}
    check("POST /mail/send 200", st == 200, f"status={st}")
    check("响应携带 task_id", d.get("task_id") == tid, str(d.get("task_id")))
    check("响应携带 reply_url", bool(d.get("reply_url")), str(d.get("reply_url"))[:60])
    st, r = call("GET", f"/api/v1/tasks/{tid}/messages")
    msgs = ((r.get("data") or {}).get("messages") or [])
    emailed = [m for m in msgs if m.get("source") == "email"]
    check("邮件内容被记入该任务线程", len(emailed) == 1, f"email_msgs={len(emailed)}")
else:
    print("\n3) 跳过真实发信（加 --send 启用）")

# ---------- 4) 轮换链接 ----------
print("\n4) 轮换回复链接 → 旧链接必须失效")
st, r = call("POST", f"/api/v1/tasks/{tid}/reply-link", {})
new_tok = ((r.get("data") or {}).get("reply_url") or "").rsplit("/", 1)[-1]
check("轮换返回新链接", bool(new_tok) and new_tok != tok)
st_old, r_old = call("GET", f"/api/v1/reply/{tok}", auth=False)
check("旧链接 401 token_revoked", st_old == 401 and (r_old.get("error") or {}).get("code") == "token_revoked", f"status={st_old}")

# ---------- 5) 免登录打开会话 ----------
print("\n5) 免登录打开会话（不带任何鉴权头）")
st, r = call("GET", f"/api/v1/reply/{new_tok}", auth=False)
d = r.get("data") or {}
sess = d.get("session") or {}
check("GET reply 200", st == 200, f"status={st}")
check("session.task_id 正确", sess.get("task_id") == tid, str(sess.get("task_id")))
check("session.can_reply=True", sess.get("can_reply") is True)
check("返回完整线程(>=2 条)", len(d.get("messages") or []) >= 2, f"msgs={len(d.get('messages') or [])}")

# ---------- 6) 用户网页回帖 ----------
print("\n6) 用户在网页回帖（无鉴权头）")
st, r = call("POST", f"/api/v1/reply/{new_tok}", {"author": "运营同事", "content": "确认：可以先推送，异常订单页文案记得改成中文。"}, auth=False)
d = r.get("data") or {}
check("POST reply 201", st == 201, f"status={st}")
check("返回 message.role=user", (d.get("message") or {}).get("role") == "user")
check("返回 agent_hint 取回方式", "after_id=" in str(d.get("agent_hint")), str(d.get("agent_hint"))[:70])

# ---------- 7) Agent 轮询取回 ----------
print("\n7) Agent 增量拉取用户回复")
st, r = call("GET", f"/api/v1/tasks/{tid}/messages?role=user&wait_seconds=0")
d = r.get("data") or {}
check("messages 与 items 双键一致", d.get("messages") == d.get("items"))
check("拉到 1 条用户消息", len(d.get("messages") or []) == 1, f"count={len(d.get('messages') or [])}")
check("waiting_reply 已清零", (d.get("task") or {}).get("waiting_reply") is False)

# ---------- 8) 安全路径 ----------
print("\n8) 安全路径")
st, r = call("GET", f"/api/v1/reply/{new_tok[:-4]}AAAA", auth=False)
check("篡改签名 401 invalid_signature", st == 401 and (r.get("error") or {}).get("code") == "invalid_signature", f"status={st}")
st, r = call("GET", "/api/v1/reply/not-a-token", auth=False)
check("畸形令牌 401", st == 401, f"status={st}")
st, r = call("GET", f"/api/v1/tasks/{tid}/messages", auth=False)
check("无 API Key 访问 Agent 接口 401", st == 401, f"status={st}")

# ---------- 9) 关闭任务 ----------
print("\n9) 关闭任务 → 回复链接停止可用")
call("POST", f"/api/v1/tasks/{tid}/close", {})
st, r = call("POST", f"/api/v1/reply/{new_tok}", {"content": "关闭后还能回吗"}, auth=False)
check("已关闭任务回帖 403 task_closed", st == 403 and (r.get("error") or {}).get("code") == "task_closed", f"status={st}")
st, r = call("GET", f"/api/v1/reply/{new_tok}", auth=False)
check("关闭后仍可只读查看", st == 200 and (r.get("data") or {}).get("session", {}).get("can_reply") is False)

print("\n10) 删除任务（自清理，避免留测试数据）")
st, r = call("DELETE", f"/api/v1/tasks/{tid}")
check("DELETE 任务 200", st == 200 and (r.get("data") or {}).get("deleted") is True, f"status={st}")
st, r = call("GET", f"/api/v1/reply/{new_tok}", auth=False)
check("删除后链接 404 失效", st == 404, f"status={st}")
st, r = call("GET", f"/api/v1/tasks/{tid}")
check("删除后任务 404", st == 404, f"status={st}")

print(f"\n===== 结果: PASS={len(PASS)}  FAIL={len(FAIL)} =====")
if FAIL:
    print("失败项: " + ", ".join(FAIL))
print("TASK_ID=" + (tid or ""))
