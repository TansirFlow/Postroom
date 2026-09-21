"""任务会话 + 邮件内免登录回复链接 + 对话/收件箱 + 多用户隔离 端到端回归测试。

覆盖：
  A. 建任务 → Agent 发消息 → 邮件带链接 → 用户网页回帖 → Agent 增量取回
     → 令牌轮换/篡改/关闭/删除等安全路径；
  A2. 拉取式回复：对话幂等 ensure、同一对话多线程、/inbox 增量拉取与按会话分组、
     ack 水位只增不减、至少一次语义（不重复投递）、显式 cursor 重放；
  B. 多用户：账号密码登录、按用户数据隔离（含对话与收件箱水位按密钥隔离）、
     每用户 SMTP 设置、仅管理员可建账号、重置密码踢下线、删除账号级联清理。

注意：**不含长轮询**——回复一律通过 GET /api/v1/inbox 拉取。

前置：后端已在 http://127.0.0.1:8077 运行。密钥与口令**通过环境变量传入**，
仓库里不含任何默认凭据。

用法（在项目根目录下）：

    # 仅本地链路（不发信）
    AGENT_API_KEY=sk-agent-xxx backend\\.venv\\Scripts\\python.exe tests\\test_task_flow.py

    # 额外真实发一封测试邮件，必须显式指定收件人
    AGENT_API_KEY=sk-agent-xxx TEST_RECIPIENT=you@example.com \\
        backend\\.venv\\Scripts\\python.exe tests\\test_task_flow.py --send

    # 追加上多用户段落（需要管理员账号）
    AGENT_API_KEY=sk-agent-xxx \\
    ADMIN_USERNAME=admin ADMIN_PASSWORD=xxxxxx \\
        backend\\.venv\\Scripts\\python.exe tests\\test_task_flow.py
"""
import json
import os
import secrets
import string
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
ADMIN_USER = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "")
PASS, FAIL = [], []

if not KEY:
    sys.exit("缺少 AGENT_API_KEY 环境变量（密钥见 backend/data/keys.txt）")
if DO_SEND and not SEND_TO:
    sys.exit("--send 需要同时给出 TEST_RECIPIENT=<收件邮箱>")

# 允许直接 import app.services.replylink 做纯函数校验
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


def call(method, path, body=None, auth=True, token=None, key=None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    elif auth:
        req.add_header("X-API-Key", key or KEY)
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


def code_of(resp):
    return ((resp or {}).get("error") or {}).get("code")


def rand(n=6, alphabet=string.ascii_lowercase + string.digits):
    return "".join(secrets.choice(alphabet) for _ in range(n))


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

# ---------- 7) Agent 增量拉取（已无长轮询） ----------
print("\n7) Agent 增量拉取用户回复")
st, r = call("GET", f"/api/v1/tasks/{tid}/messages?role=user")
d = r.get("data") or {}
check("messages 与 items 双键一致", d.get("messages") == d.get("items"))
check("拉到 1 条用户消息", len(d.get("messages") or []) == 1, f"count={len(d.get('messages') or [])}")
check("消息带游标 seq", all(isinstance(m.get("seq"), int) for m in (d.get("messages") or [])),
      str([m.get("seq") for m in (d.get("messages") or [])]))
check("返回 next_cursor", isinstance(d.get("next_cursor"), int), str(d.get("next_cursor")))
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


# ==================================================================
# A2. 对话 + 收件箱：Agent 不等回复，定时任务拉取后按对话分发
# ==================================================================
print("\n11) 对话幂等 ensure（定时任务重启不用记住 id）")
ext_id = f"regression:conv:{rand(8)}"
st, r = call("POST", "/api/v1/conversations", {
    "external_id": ext_id, "title": "回归 · 拉取式对话", "agent_name": "regression-bot"})
d = r.get("data") or {}
cid = d.get("conversation_id") or ""
check("POST /conversations 200", st == 200, f"status={st}")
check("首次 ensure created=true", d.get("created") is True)
check("返回 conversation_id", bool(cid), cid)

st, r = call("POST", "/api/v1/conversations", {"external_id": ext_id})
d2 = r.get("data") or {}
check("同一 external_id 重复 ensure created=false", st == 200 and d2.get("created") is False, f"status={st}")
check("重复 ensure 复用同一条会话", d2.get("conversation_id") == cid, str(d2.get("conversation_id")))
check("幂等 ensure 不会新增会话", (call("GET", f"/api/v1/conversations?q={ext_id}")[1].get("data") or {}).get("total") == 1)

st, r = call("GET", f"/api/v1/conversations/{cid}")
convo = (r.get("data") or {}).get("conversation") or {}
check("会话详情 200", st == 200, f"status={st}")
check("external_id 原样带回", convo.get("external_id") == ext_id, str(convo.get("external_id")))

print("\n12) 同一对话下发两封邮件（各开一条任务线程）")
thread_tokens, thread_tasks = [], []
for i, title in enumerate(["第一封 · 环境对齐", "第二封 · 延迟排查"], start=1):
    st, r = call("POST", "/api/v1/tasks", {"title": title, "external_id": ext_id})
    dd = r.get("data") or {}
    check(f"第 {i} 封建线程 201 且自动挂到对话",
          st == 201 and dd.get("conversation_id") == cid, f"status={st}")
    check(f"第 {i} 封 ensure 复用已有对话（不再新建）", dd.get("conversation_created") is False,
          str(dd.get("conversation_created")))
    thread_tokens.append(((dd.get("reply_url") or "").rsplit("/", 1)[-1]))
    thread_tasks.append(dd.get("task_id") or "")
st, r = call("POST", "/api/v1/tasks", {"title": "第三封 · 挂到指定对话", "conversation_id": cid})
t_c = (r.get("data") or {}).get("task_id") or ""
check("显式 conversation_id 也能挂上", st == 201 and bool(t_c), f"status={st}")
st, r = call("GET", f"/api/v1/conversations/{cid}")
check("会话下挂着 3 条任务线程",
      ((r.get("data") or {}).get("conversation") or {}).get("task_count") == 3,
      str(((r.get("data") or {}).get("conversation") or {}).get("task_count")))
st, r = call("GET", f"/api/v1/tasks?conversation_id={cid}")
check("按对话过滤任务列表", (r.get("data") or {}).get("total") == 3, f"total={(r.get('data') or {}).get('total')}")

print("\n13) 建立拉取基线（先取一次再 ack 水位）")
st, r = call("GET", "/api/v1/inbox")
d = r.get("data") or {}
base = d.get("next_cursor") or 0
check("GET /inbox 200（非阻塞，立即返回）", st == 200, f"status={st}")
check("返回 by_conversation 分组字段", isinstance(d.get("by_conversation"), list), str(type(d.get("by_conversation"))))
check("返回 watermark 字段", "watermark" in d, str(d.get("watermark")))
st, r = call("POST", "/api/v1/inbox/ack", {"upto_seq": base})
check("ack 推进水位", st == 200 and (r.get("data") or {}).get("watermark") == base, f"status={st}")

print("\n14) 用户回帖 → 定时任务一次拉走全部对话的回复")
st, r = call("POST", f"/api/v1/reply/{thread_tokens[0]}",
             {"content": "第一个方向先别动，帮我确认仿真步长", "author": "回归用户"}, auth=False)
check("用户回帖 201", st == 201, f"status={st}")
check("agent_hint 指向 /inbox 拉取（不再提长轮询）",
      "/api/v1/inbox" in str((r.get("data") or {}).get("agent_hint")), str((r.get("data") or {}).get("agent_hint"))[:70])
st, r = call("POST", f"/api/v1/reply/{thread_tokens[1]}", {"content": "第二个方向换成 GPU 训练试试"}, auth=False)
check("第二封线程也回帖 201", st == 201, f"status={st}")

st, r = call("GET", "/api/v1/inbox")
d = r.get("data") or {}
items = d.get("items") or []
check("一次拉到两条回复（无须逐任务轮询）", len(items) == 2, f"count={d.get('count')}")
check("每条都带 conversation_id（可直接路由）", all(i.get("conversation_id") == cid for i in items))
check("两条分属不同任务线程", len({i.get("task_id") for i in items}) == 2, str(sorted({i.get("task_id") for i in items})))
check("按 seq 升序返回", [i["seq"] for i in items] == sorted(i["seq"] for i in items), str([i["seq"] for i in items]))
check("消息自带游标 seq", all(isinstance(i.get("seq"), int) and i["seq"] > base for i in items))
bc = d.get("by_conversation") or []
check("按会话归组：1 个会话 / 2 条消息", len(bc) == 1 and bc[0].get("count") == 2, str(bc))
check("分组里含 2 条任务线程", len(bc[0].get("task_ids") or []) == 2, str(bc[0].get("task_ids") if bc else None))
check("next_cursor 已前进", (d.get("next_cursor") or 0) > base, f"{base} -> {d.get('next_cursor')}")

print("\n15) ack 之后不重复投递（至少一次语义）")
new_cursor = d.get("next_cursor") or 0
st, r = call("POST", "/api/v1/inbox/ack", {"upto_seq": new_cursor})
ad = r.get("data") or {}
check("ack 200", st == 200, f"status={st}")
check("水位 = next_cursor", ad.get("watermark") == new_cursor, str(ad.get("watermark")))
check("清掉 2 个线程的待回复计数", ad.get("tasks_marked_read") == 2, str(ad.get("tasks_marked_read")))
st, r = call("GET", "/api/v1/inbox")
check("不带 cursor 再拉为空（不重复投递）", not ((r.get("data") or {}).get("items")), str((r.get("data") or {}).get("items")))
st, r = call("POST", "/api/v1/inbox/ack", {"upto_seq": 0})
check("水位只增不减（传旧值不回退）", (r.get("data") or {}).get("watermark") == new_cursor, str((r.get("data") or {}).get("watermark")))
WM_FINAL = new_cursor

print("\n16) 显式 cursor 可重放 + 概览统计")
st, r = call("GET", f"/api/v1/inbox?cursor={base}")
check("显式 cursor 能重放那两条", st == 200 and len((r.get("data") or {}).get("items") or []) == 2, f"status={st}")
st, r = call("GET", f"/api/v1/inbox?cursor={new_cursor}&role=all")
check("role=all 可看全部角色消息", st == 200 and len((r.get("data") or {}).get("items") or []) >= 0, f"status={st}")
st, r = call("GET", "/api/v1/inbox/stats")
sd = r.get("data") or {}
check("inbox/stats 200 且含水位与待取条数",
      st == 200 and "watermark" in sd and "pending_messages" in sd, f"status={st}")
check("stats 显示待取为 0", sd.get("pending_messages") == 0, str(sd.get("pending_messages")))
check("stats 显示会话未读已清", (sd.get("conversations") or {}).get("unread") == 0, str(sd.get("conversations")))
check("stats 含该对话", (sd.get("conversations") or {}).get("total", 0) >= 1, str(sd.get("conversations")))
st, r = call("GET", "/api/v1/overview")
check("控制台概览带 conversation_stats 与 inbox",
      "conversation_stats" in (r.get("data") or {}) and "inbox" in (r.get("data") or {}), f"status={st}")

print("\n17) 错误路径")
st, r = call("GET", "/api/v1/conversations/conv_doesnotexist")
check("不存在的对话 404 conversation_not_found", st == 404 and code_of(r) == "conversation_not_found", f"status={st}")
st, r = call("POST", "/api/v1/tasks", {"title": "x", "conversation_id": "conv_doesnotexist"})
check("挂到不存在的对话 404", st == 404 and code_of(r) == "conversation_not_found", f"status={st}")
st, r = call("GET", "/api/v1/inbox", auth=False)
check("无凭证拉取收件箱 401", st == 401, f"status={st}")
st, r = call("POST", "/api/v1/inbox/ack", {"upto_seq": new_cursor}, auth=False)
check("无凭证 ack 401", st == 401, f"status={st}")
st, r = call("GET", "/api/v1/inbox?limit=999")
check("limit 超上限 422", st == 422, f"status={st}")

print("\n18) 自清理：删对话 + 删线程")
st, r = call("DELETE", f"/api/v1/conversations/{cid}")
check("DELETE 对话 200", st == 200 and (r.get("data") or {}).get("deleted") is True, f"status={st}")
st, r = call("GET", f"/api/v1/tasks/{t_c}")
check("删对话后任务仍在（只解关联）",
      st == 200 and ((r.get("data") or {}).get("task") or {}).get("conversation_id") is None,
      str(((r.get("data") or {}).get("task") or {}).get("conversation_id")))
st, r = call("GET", f"/api/v1/reply/{thread_tokens[0]}", auth=False)
check("删对话后回复链接仍可用（任务未被连带删除）", st == 200, f"status={st}")
for leftover in [*thread_tasks, t_c]:
    call("DELETE", f"/api/v1/tasks/{leftover}")
st, r = call("GET", f"/api/v1/conversations?q={ext_id}")
check("测试对话已清理", (r.get("data") or {}).get("total") == 0, f"total={(r.get('data') or {}).get('total')}")
st, r = call("GET", "/api/v1/tasks?page_size=200")
left = [t for t in ((r.get("data") or {}).get("items") or []) if (t.get("title") or "").startswith("第")
        or (t.get("title") or "").startswith("第三封")]
check("测试线程已清理干净", not left, str([t.get("title") for t in left]))


# ==================================================================
# B. 多用户：登录 / 隔离 / 设置 / 账号管理
# ==================================================================
if not (ADMIN_USER and ADMIN_PASS):
    print("\nB) 跳过多用户段落（需要 ADMIN_USERNAME / ADMIN_PASSWORD 环境变量）")
else:
    created_ids = []
    uniq = rand()
    user_name = f"t_{uniq}"
    user_pass = "Test-" + rand(10) + "!9"

    print("\nB1) 账号密码登录")
    st, r = call("POST", "/api/v1/auth/login", {"username": ADMIN_USER, "password": ADMIN_PASS}, auth=False)
    admin_token = (r.get("data") or {}).get("token") or ""
    check("管理员登录 200", st == 200 and bool(admin_token), f"status={st}")
    check("登录返回 expires_at", bool((r.get("data") or {}).get("expires_at")))
    check("登录返回 user.is_admin", ((r.get("data") or {}).get("user") or {}).get("is_admin") is True)

    st, r = call("POST", "/api/v1/auth/login", {"username": ADMIN_USER, "password": "definitely-wrong"}, auth=False)
    check("错误密码 401 invalid_credentials", st == 401 and code_of(r) == "invalid_credentials", f"status={st}")
    st, r = call("POST", "/api/v1/auth/login", {"username": f"no-such-{uniq}", "password": "whatever123"}, auth=False)
    check("不存在用户也是 401（不泄露账号是否存在）", st == 401 and code_of(r) == "invalid_credentials", f"status={st}")

    st, r = call("GET", "/api/v1/auth/me", token=admin_token)
    check("会话令牌访问 /auth/me 200", st == 200 and (r.get("data") or {}).get("via") == "session", f"status={st}")
    admin_id = ((r.get("data") or {}).get("user") or {}).get("id") or ""
    st, r = call("GET", "/api/v1/overview", token=admin_token)
    check("会话令牌访问 /overview 200", st == 200, f"status={st}")
    st, r = call("GET", "/api/v1/overview", auth=False)
    check("无凭证访问 /overview 401", st == 401 and code_of(r) == "missing_credentials", f"status={st}")
    st, r = call("GET", "/api/v1/keys", token="not-a-real-token")
    check("伪造会话令牌 401", st == 401, f"status={st}")

    print("\nB2) 仅管理员可建账号")
    st, r = call("POST", "/api/v1/users", {"username": user_name, "display_name": "回归用户", "password": user_pass, "role": "user"}, token=admin_token)
    new_user = (r.get("data") or {}).get("user") or {}
    user_id = new_user.get("id") or ""
    if user_id:
        created_ids.append(user_id)
    check("管理员建账号 201", st == 201 and bool(user_id), f"status={st}")
    check("新账号 role=user / is_admin=False", new_user.get("role") == "user" and new_user.get("is_admin") is False)
    check("响应不回传密码哈希", "password_hash" not in new_user)

    st, r = call("POST", "/api/v1/users", {"username": user_name, "password": user_pass}, token=admin_token)
    check("重复用户名 409 username_taken", st == 409 and code_of(r) == "username_taken", f"status={st}")

    st, r = call("POST", "/api/v1/auth/login", {"username": user_name, "password": user_pass}, auth=False)
    user_token = (r.get("data") or {}).get("token") or ""
    check("新账号可登录", st == 200 and bool(user_token), f"status={st}")

    st, r = call("GET", "/api/v1/users", token=user_token)
    check("普通用户访问用户管理 403 admin_required", st == 403 and code_of(r) == "admin_required", f"status={st}")
    st, r = call("POST", "/api/v1/users", {"username": f"x_{uniq}"}, token=user_token)
    check("普通用户建账号 403", st == 403, f"status={st}")

    print("\nB3) 按用户数据隔离")
    st, r = call("GET", "/api/v1/tasks?page_size=1", token=user_token)
    check("新账号任务数为 0", st == 200 and (r.get("data") or {}).get("total") == 0, f"total={(r.get('data') or {}).get('total')}")
    st, r = call("GET", "/api/v1/mail/logs?page_size=1", token=user_token)
    check("新账号发信记录为 0", st == 200 and (r.get("data") or {}).get("total") == 0, f"total={(r.get('data') or {}).get('total')}")
    st, r = call("GET", "/api/v1/keys", token=user_token)
    check("新账号密钥列表为空", st == 200 and (r.get("data") or {}).get("items") == [], f"items={(r.get('data') or {}).get('items')}")

    # 新账号自己的密钥，只能看自己的数据
    st, r = call("POST", "/api/v1/keys", {"name": f"k-{uniq}", "scopes": ["mail:send", "mail:read", "tasks:write", "tasks:read", "keys:manage"]}, token=user_token)
    user_key = (r.get("data") or {}).get("api_key") or ""
    check("新账号可自建密钥", st == 201 and bool(user_key), f"status={st}")
    st, r = call("POST", "/api/v1/keys", {"name": f"k2-{uniq}", "scopes": ["users:manage"]}, token=user_token)
    check("普通账号不能发放 users:manage", st == 403 and code_of(r) == "scope_not_allowed", f"status={st}")

    st, r = call("POST", "/api/v1/tasks", {"title": f"隔离校验-{uniq}"}, token=user_token)
    utid = (r.get("data") or {}).get("task_id") or ""
    check("新账号建任务 201", st == 201 and bool(utid), f"status={st}")
    st, r = call("GET", f"/api/v1/tasks/{utid}", key=user_key)
    check("该账号密钥能看到自己的任务", st == 200, f"status={st}")
    st, r = call("GET", f"/api/v1/tasks/{utid}", token=admin_token)
    check("管理员看不到别人的任务（404）", st == 404 and code_of(r) == "task_not_found", f"status={st}")
    st, r = call("GET", f"/api/v1/tasks/{utid}", key=KEY)
    check("原 Agent 密钥看不到别人的任务（404）", st == 404, f"status={st}")

    st, r = call("POST", "/api/v1/conversations", {"external_id": ext_id}, key=user_key)
    other_cid = (r.get("data") or {}).get("conversation_id") or ""
    check("同 external_id 在另一账号下是另一条会话（唯一索引按账号隔离）",
          st == 200 and bool(other_cid) and other_cid != cid, f"status={st} id={other_cid}")
    st, r = call("GET", "/api/v1/inbox", key=user_key)
    check("新账号收件箱看不到别人的回复",
          st == 200 and not ((r.get("data") or {}).get("items")), f"status={st}")
    st, r = call("GET", f"/api/v1/inbox/stats", key=user_key)
    check("新账号收件箱水位从 0 开始", (r.get("data") or {}).get("watermark") == 0, f"status={st}")
    st, r = call("GET", f"/api/v1/tasks?conversation_id={cid}", key=user_key)
    check("新账号按别人的对话过滤查不到任务",
          st == 200 and not ((r.get("data") or {}).get("items")), f"status={st}")
    st, r = call("GET", f"/api/v1/conversations/{cid}", key=user_key)
    check("新账号读别人的对话 404", st == 404, f"status={st}")
    st, r = call("POST", "/api/v1/inbox/ack", {"upto_seq": 999999}, key=user_key)
    check("新账号 ack 只推进自己的水位", st == 200 and (r.get("data") or {}).get("watermark") == 999999, f"status={st}")
    st, r = call("GET", "/api/v1/inbox")
    check("原账号水位未被他人推进（水位按密钥隔离）",
          (r.get("data") or {}).get("watermark") == WM_FINAL,
          f"{(r.get('data') or {}).get('watermark')} vs {WM_FINAL}")
    call("DELETE", f"/api/v1/conversations/{other_cid}", key=user_key)

    print("\nB4) 每用户独立 SMTP 设置")
    st, r = call("GET", "/api/v1/settings", token=user_token)
    d = r.get("data") or {}
    check("GET /settings 200", st == 200, f"status={st}")
    check("尚未配置自己的 SMTP 时回落全局", d.get("smtp_configured") is False and (d.get("effective_smtp") or {}).get("source") == "env")
    check("全局配置视图不含明文密码", "password" not in ((d.get("global_smtp") or {})))

    own_host = f"smtp.{uniq}.example.com"
    st, r = call("PUT", "/api/v1/settings", {
        "smtp": {"host": own_host, "port": 587, "use_ssl": False, "starttls": True,
                 "user": f"{uniq}@example.com", "password": "Pw-" + rand(8),
                 "from_email": f"{uniq}@example.com", "from_name": "回归用户"},
        "public_base_url": f"https://{uniq}.example.com",
        "test_recipients": f"{uniq}@example.com",
    }, token=user_token)
    eff = (r.get("data") or {}).get("effective_smtp") or {}
    check("PUT /settings 保存成功", st == 200 and (r.get("data") or {}).get("saved") is True, f"status={st}")
    check("生效 SMTP 用用户自己的主机", eff.get("host") == own_host, str(eff.get("host")))
    check("生效 SMTP source=user", eff.get("source") == "user", str(eff.get("source")))
    check("接口不回传明文密码", "password" not in eff and eff.get("password_set") is True)
    check("回复链接域名生效", (r.get("data") or {}).get("effective_public_base_url") == f"https://{uniq}.example.com")

    st, r = call("GET", "/api/v1/settings", token=admin_token)
    check("管理员设置未被影响（仍是全局）", (r.get("data") or {}).get("smtp_configured") is False, str((r.get('data') or {}).get('smtp_configured')))

    # 用户自己的域名应体现在自己任务的回复链接里
    st, r = call("POST", f"/api/v1/tasks/{utid}/reply-link", {}, token=user_token)
    ulink = (r.get("data") or {}).get("reply_url") or ""
    check("回复链接使用用户自己的域名", ulink.startswith(f"https://{uniq}.example.com/reply/"), ulink[:70])

    st, r = call("POST", "/api/v1/settings/smtp-test", {"smtp": {"host": "", "port": 465}}, token=user_token)
    check("smtp-test 返回结构完整", st == 200 and "ok" in (r.get("data") or {}), f"status={st}")

    print("\nB5) 改密 / 重置 / 停用 / 删除")
    st, r = call("PUT", "/api/v1/settings", {"public_base_url": "not-a-url"}, token=user_token)
    check("非法对外地址 400 invalid_base_url", st == 400 and code_of(r) == "invalid_base_url", f"status={st}")

    st, r = call("POST", "/api/v1/auth/password", {"old_password": "wrong-pass", "new_password": "Another-1!pass"}, token=user_token)
    check("旧密码错误 400 wrong_password", st == 400 and code_of(r) == "wrong_password", f"status={st}")

    new_pass = "Changed-" + rand(8) + "!7"
    st, r = call("POST", "/api/v1/auth/password", {"old_password": user_pass, "new_password": new_pass}, token=user_token)
    rotated = (r.get("data") or {}).get("token") or ""
    check("改密成功并返回新令牌", st == 200 and bool(rotated), f"status={st}")
    st, r = call("GET", "/api/v1/auth/me", token=user_token)
    check("改密后旧令牌失效（401）", st == 401, f"status={st}")
    st, r = call("GET", "/api/v1/auth/me", token=rotated)
    check("新令牌可用", st == 200, f"status={st}")
    st, r = call("POST", "/api/v1/auth/login", {"username": user_name, "password": new_pass}, auth=False)
    user_token = (r.get("data") or {}).get("token") or ""
    check("新密码可登录", st == 200 and bool(user_token), f"status={st}")

    st, r = call("DELETE", f"/api/v1/users/{admin_id}", token=admin_token)
    check("管理员不能删除自己 400 cannot_delete_self", st == 400 and code_of(r) == "cannot_delete_self", f"status={st}")
    st, r = call("PATCH", f"/api/v1/users/{admin_id}", {"enabled": False}, token=admin_token)
    check("管理员不能停用自己 400 cannot_disable_self", st == 400 and code_of(r) == "cannot_disable_self", f"status={st}")
    st, r = call("POST", "/api/v1/users", {"username": f"no-admin-{uniq}", "role": "admin", "password": "Tmp-" + rand(8) + "!3"}, token=admin_token)
    tmp_admin_id = ((r.get("data") or {}).get("user") or {}).get("id") or ""
    if tmp_admin_id:
        created_ids.append(tmp_admin_id)
    check("可再建一个管理员账号", st == 201 and bool(tmp_admin_id), f"status={st}")
    st, r = call("DELETE", f"/api/v1/users/{tmp_admin_id}", token=admin_token)
    check("第二个管理员可被删除（非最后一个）", st == 200, f"status={st}")
    if tmp_admin_id in created_ids:
        created_ids.remove(tmp_admin_id)

    st, r = call("POST", f"/api/v1/users/{user_id}/password", {}, token=admin_token)
    reset_pw = (r.get("data") or {}).get("new_password") or ""
    check("管理员重置密码返回新密码", st == 200 and len(reset_pw) >= 8, f"status={st}")
    st, r = call("GET", "/api/v1/auth/me", token=user_token)
    check("被重置后旧会话失效", st == 401, f"status={st}")
    st, r = call("POST", "/api/v1/auth/login", {"username": user_name, "password": reset_pw}, auth=False)
    user_token = (r.get("data") or {}).get("token") or ""
    check("重置后的密码可登录", st == 200 and bool(user_token), f"status={st}")

    st, r = call("PATCH", f"/api/v1/users/{user_id}", {"enabled": False}, token=admin_token)
    check("停用账号 200", st == 200 and ((r.get("data") or {}).get("user") or {}).get("enabled") is False, f"status={st}")
    st, r = call("POST", "/api/v1/auth/login", {"username": user_name, "password": reset_pw}, auth=False)
    check("停用后登录 403 account_disabled", st == 403 and code_of(r) == "account_disabled", f"status={st}")
    st, r = call("GET", "/api/v1/auth/me", token=user_token)
    check("停用后已有会话失效", st == 401, f"status={st}")

    st, r = call("PATCH", f"/api/v1/users/{user_id}", {"enabled": True, "display_name": "回归用户2"}, token=admin_token)
    check("重新启用 200", st == 200 and ((r.get("data") or {}).get("user") or {}).get("enabled") is True, f"status={st}")

    st, r = call("DELETE", f"/api/v1/users/{user_id}", token=admin_token)
    check("删除账号 200（级联清理）", st == 200 and (r.get("data") or {}).get("deleted") is True, f"status={st}")
    check("级联清理统计返回", isinstance((r.get("data") or {}).get("removed"), dict), str((r.get("data") or {}).get("removed")))
    if user_id in created_ids:
        created_ids.remove(user_id)
    st, r = call("GET", f"/api/v1/tasks/{utid}", token=admin_token)
    check("被删账号的任务已不可见", st == 404, f"status={st}")
    st, r = call("GET", "/api/v1/auth/me", key=user_key)
    check("被删账号的密钥失效", st == 401, f"status={st}")

    st, r = call("GET", "/api/v1/users", token=admin_token)
    items = (r.get("data") or {}).get("items") or []
    check("用户列表包含管理员自己", any(u.get("is_self") for u in items), f"users={len(items)}")

    # 兜底清理：万一中途异常，把遗留账号删掉
    for leftover in created_ids:
        call("DELETE", f"/api/v1/users/{leftover}", token=admin_token)

print(f"\n===== 结果: PASS={len(PASS)}  FAIL={len(FAIL)} =====")
if FAIL:
    print("失败项: " + ", ".join(FAIL))
print("TASK_ID=" + (tid or ""))
