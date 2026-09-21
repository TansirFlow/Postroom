# Postroom

> Postroom = post（通信）+ room（会话室）：给 AI Agent 用的一间「收发室」。

面向 **AI Agent** 的工具型 API 服务器：Python(FastAPI) 后端 + Vue 3 前端控制台。让 Agent 不只会发通知，还能**等到人的回话再继续干活**。

两个能力模块：

1. **邮件发送**（任意 SMTP 服务商）——Agent 直接调接口发信；
2. **任务会话 + 免登录回复链接**——Agent 启动任务时拿一个 `task_id`，之后每封邮件正文都会自动附带一个「点开即回复」的网页链接，**收件人不用登录、不用装 App**，点开就能在浏览器里和 Agent 多轮对话；Agent 用长轮询把回复取回去，形成闭环。

Agent 拉一次 `GET /api/v1/agent/tools` 就能拿到 OpenAI function-calling 格式的 8 个工具定义，直接注册进自己的工具列表即可，无需人工写 prompt。

```
postroom/
├─ backend/                     # FastAPI 服务
│  ├─ app/
│  │  ├─ main.py                # 应用入口：中间件、异常包装、静态资源托管
│  │  ├─ config.py              # 配置（读 .env）
│  │  ├─ storage.py             # SQLite 存储（密钥/发信日志/任务/任务消息，含自动补列迁移）
│  │  ├─ security.py            # API Key 鉴权 + 权限校验
│  │  ├─ ratelimit.py           # 每小时配额
│  │  ├─ schemas.py             # 请求/响应模型
│  │  ├─ tokens.py              # 回复链接的 HMAC 无状态签名令牌（签发/校验/版本吊销）
│  │  ├─ services/
│  │  │  ├─ mailer.py           # SMTP 发送（重试、错误归类、连通性检测）
│  │  │  ├─ templates.py        # 内置邮件模板
│  │  │  └─ replylink.py        # 回复链接签发 + 把链接按钮追加进邮件正文
│  │  └─ routers/
│  │     ├─ mail.py             # 邮件接口（agent 主入口）
│  │     ├─ tasks.py            # 任务会话（建任务/发消息/长轮询取用户回复/吊销链接）
│  │     ├─ reply.py            # 免登录回复页后端（链接即凭证，无需 API Key）
│  │     ├─ keys.py             # 密钥管理
│  │     ├─ system.py           # 健康检查 / 概览
│  │     └─ agent.py            # 工具自描述（tools / manifest）
│  ├─ requirements.txt
│  ├─ run.py                    # 启动脚本
│  └─ .env.example              # 配置模板（复制为 .env 后由管理员填写；.env 不入库）
├─ frontend/                    # Vue 3 + Vite 控制台
│  └─ src/{App.vue,views/*,api.js,router.js,styles.css}
├─ tests/
│  └─ test_task_flow.py         # 任务会话 + 回复链接端到端回归（自清理，30 项断言）
├─ screenshots/                 # 控制台 & 回复页截图
├─ LICENSE                      # MIT
├─ run-backend.cmd              # Windows 一键启动后端
├─ dev-frontend.cmd             # 前端开发模式（热更新，端口 5173）
└─ build-frontend.cmd           # 构建前端到 frontend/dist
```

---

## 一、快速开始

**生产模式（单端口，后端顺带托管前端）**

```bash
# 1) 后端
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
.venv\Scripts\python.exe run.py

# 2) 前端构建（只需一次，构建后由后端托管）
cd ../frontend
npm install
npm run build
```

打开 <http://127.0.0.1:8077/> —— 控制台。
首次启动会**自动生成两个密钥并打印在控制台**，同时写到 `backend/data/keys.txt`：

| 用途 | 说明 |
| --- | --- |
| `ADMIN_API_KEY` | 管理员，拥有全部权限（含密钥管理） |
| `AGENT_API_KEY` | 给 Agent 用，默认 `mail:send` + `mail:read` + `tasks:write` + `tasks:read` |

也可以在 `.env` 里预先写死 `BOOTSTRAP_API_KEY` / `ADMIN_API_KEY`。

**开发模式**：后端 `run.py`，前端 `dev-frontend.cmd`（Vite 5173，`/api` 已代理到 8077）。

界面速览（`screenshots/`）：

| 文件 | 内容 |
| --- | --- |
| `dashboard.png` | 控制台概览，含「待回复」告警与任务统计 |
| `compose.png` | 发信工作台（未关联任务） |
| `compose-task.png` | 发信工作台：选中关联任务后正文自动附带回复链接 |
| `tasks.png` | 任务会话：左侧任务列表 + 右侧会话线程 + 回复链接管理 |
| `reply.png` | **免登录回复页**（邮件里点开就是这个页面） |
| `docs.png` / `docs-flow.png` | 接口文档 与「任务闭环」示例代码 |
| `logs.png` / `keys.png` | 发送记录 / API 密钥 |

---

## 二、配置（`backend/.env`）

> **仓库里不含任何真实凭据。** `.env`（含 SMTP 密码）与 `data/`（密钥、签名私钥、数据库）都在 `.gitignore` 里，
> 仓库只提供 `backend/.env.example`。**部署完成后由管理员自己复制并填写**：
>
> ```bash
> cp backend/.env.example backend/.env   # 然后编辑，填 SMTP 账号等
> ```
>
> 未配置 SMTP 时服务仍可正常启动（健康检查会返回 `smtp_configured: false`），只是无法发信。

| 变量 | 默认值 / 示例 | 说明 |
| --- | --- | --- |
| `HOST` / `PORT` | `127.0.0.1` / `8077` | 监听地址。对外提供服务时改 `0.0.0.0` |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.example.com` / `465` | 任意 SMTP 服务商；465 端口需配 `SMTP_USE_SSL=true` |
| `SMTP_USE_SSL` | `true` | 465 端口必须为 true |
| `SMTP_USER` / `SMTP_PASSWORD` | **空（管理员填写）** | SMTP 认证账号与应用专用密码 |
| `SMTP_FROM_EMAIL` | 空 | **必须与认证账号一致**，否则多数服务商会拒发 |
| `SMTP_FROM_NAME` | `Postroom` | 收件人看到的发件人名字 |
| `RATE_LIMIT_PER_HOUR` | `120` | 每密钥每小时发信上限 |
| `TEST_RECIPIENTS` | 空 | 控制台「一键测试」用的收件人，逗号分隔。留空则该功能不显示 |
| `STORE_BODY_PREVIEW` | `true` | 是否在日志中留正文摘要（前 1500 字） |

**任务会话 / 回复链接相关**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PUBLIC_BASE_URL` | 空 | 生成回复链接用的对外地址。留空则取当前请求的 host（本机即 `http://127.0.0.1:8077`）。**要让外部收件人点得开，必须填公网可访问的地址**，如 `https://mail.example.com` |
| `REPLY_TOKEN_SECRET` | 空 | 回复链接签名密钥。留空自动生成并持久化到 `data/reply_secret.txt`（改它会一次性作废所有旧链接） |
| `REPLY_TOKEN_TTL_DAYS` | `30` | 回复链接默认有效期（天） |
| `REPLY_RATE_LIMIT_PER_HOUR` | `60` | 单个任务下用户每小时最多回信条数 |
| `MESSAGE_MAX_CHARS` | `4000` | 单条会话消息最大字数 |

---

## 三、鉴权与权限

所有业务接口需要 API Key，两种传法等价：

```http
X-API-Key: sk-agent-xxxxx
Authorization: Bearer sk-agent-xxxxx
```

| 权限 | 能做什么 |
| --- | --- |
| `mail:send` | 发信、查模板、测试 SMTP 连接 |
| `mail:read` | 查发送记录、统计 |
| `tasks:write` | 建任务、向任务线程发消息、吊销/轮换回复链接、关闭任务 |
| `tasks:read` | 查任务列表/详情、长轮询取用户回复 |
| `keys:manage` | 增删改 API 密钥 |

`AGENT_API_KEY` 默认四项全给：`mail:send` + `mail:read` + `tasks:write` + `tasks:read`。

密钥只以 **sha256 摘要**落库，明文仅创建时返回一次。控制台「API 密钥」页可新建 / 停用 / 删除。

> 注意：`/api/v1/reply/*` **不需要任何 API Key** —— 链接里的签名令牌本身就是凭证，这正是「收件人点开就能回」的实现方式。

---

## 四、发邮件接口

### `POST /api/v1/mail/send`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `to` | string[] | ✅ | 收件人 |
| `subject` | string | 条件 | 用 `template` 时可省略，否则必填 |
| `body` | string | 条件 | 纯文本正文 |
| `html` | string | 条件 | HTML 正文；与 `body` 同时给出则生成多部分邮件 |
| `cc` / `bcc` | string[] | | 抄送 / 密送 |
| `reply_to` | string | | 回复地址 |
| `attachments` | object[] | | `{filename, content_base64, mime_type?}`，总大小 ≤ 10MB |
| `template` | string | | `welcome` / `alert` / `report` |
| `variables` | object | | 模板变量，如 `{"title": "...", "period": "..."}` |
| `idempotency_key` | string | | 幂等键，重试不会重复发送 |
| `task_id` | string | | 关联任务。给出后正文会自动追加「点开即回复」链接，并把本封内容记入该任务会话 |
| `attach_reply_link` | boolean | | 默认 `true`；设 `false` 可只关联任务、不加链接 |

```bash
curl -X POST http://127.0.0.1:8077/api/v1/mail/send \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk-agent-xxxxx" \
  -d '{
    "to": ["you@example.com"],
    "subject": "任务完成通知",
    "body": "2026-Q3 报表已生成，环比 +18.4%",
    "idempotency_key": "train-200k-done"
  }'
```

响应（统一包裹 `{ok, data, error, request_id}`）：

```json
{
  "ok": true,
  "data": {
    "id": "mail_31da247e4b11",
    "status": "sent",
    "message_id": "<178997156936.1.2@example.com>",
    "latency_ms": 3462,
    "size_bytes": 1235,
    "to": ["you@example.com"],
    "accepted": ["you@example.com"],
    "refused": [],
    "attempts": 1,
    "rate_limit": { "used": 1, "remaining": 119, "limit": 120 }
  },
  "error": null,
  "request_id": "req_eed254172db2"
}
```

带 `task_id` 发送时，响应里会多出 `task_id` 与 `reply_url`，方便 Agent 直接引用：

```json
"data": { "...": "...", "task_id": "task_8e7f9927b389", "reply_url": "http://127.0.0.1:8077/reply/eyJ0Ijo..." }
```

### 其他接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | — | 健康检查（无需鉴权） |
| GET | `/api/v1/agent/tools` | — | Agent 工具清单（function-calling 格式） |
| GET | `/api/v1/agent/manifest` | — | 能力摘要 + 快速上手代码 |
| GET | `/api/v1/whoami` | 任意 | 校验 Key 与权限 |
| GET | `/api/v1/overview` | `mail:read` | 控制台概览（含任务统计） |
| GET | `/api/v1/mail/logs` | `mail:read` | 分页查询（`page` / `page_size` / `status` / `q`） |
| GET | `/api/v1/mail/logs/{id}` | `mail:read` | 单封详情 |
| GET | `/api/v1/mail/stats` | `mail:read` | 统计 + 限流快照 |
| GET | `/api/v1/mail/templates` | `mail:send` | 模板及变量 |
| POST | `/api/v1/mail/verify-connection` | `mail:send` | SMTP 连通性 / 登录测试 |
| GET/POST/PATCH/DELETE | `/api/v1/keys...` | `keys:manage` | 密钥管理 |

任务会话接口见下一节。

Swagger：<http://127.0.0.1:8077/api/docs>，OpenAPI：`/openapi.json`。

---

## 五、任务会话 + 邮件内免登录回复链接

这是把**邮件变成双向对话通道**的闭环设计：

```
Agent 启动任务                用户收到邮件                用户在网页回帖            Agent 继续干活
      │                            │                          │                        │
 POST /tasks ──► task_id            │                          │                        │
      │      └► reply_url           │                          │                        │
      │                            │                          │                        │
 POST /mail/send {task_id} ────────►│ 正文末尾带「点开即回复」   │                        │
      │                            │ 按钮 ──点击──────────────►│ 打开 /reply/<token>     │
      │                            │                          │ 看完整线程，输入即发    │
      │                            │                          │                        │
 GET /tasks/{id}/messages?wait_seconds=60 ◄─────────────────────────────────────────────┘
      │  长轮询阻塞，用户一回帖立刻返回
      └► 拿到回复 → 继续执行 → 再 POST /mail/send 汇报 → …… 直到 close
```

### 1) `POST /api/v1/tasks` — 启动任务，拿 `task_id`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `title` | string | ✅ | 任务标题（会出现在邮件主题和回复页顶部） |
| `agent_name` | string | | 显示名，默认取密钥名 |
| `meta` | object | | 任意上下文（如 `{"run_id": "...", "ckpt": "..."}`） |
| `reply_expires_days` | int | | 覆盖默认有效期（天） |

响应同时给出 `task_id`、`reply_url`、`reply_token`、`reply_expires_at`。（`task_id` 与 `id` 两个键值相同，与列表接口的 `items[].id` 对齐。）

### 2) `POST /api/v1/mail/send` 带上 `task_id`

邮件正文（纯文本和 HTML 两版）末尾都会自动追加：

```
——————————————————
💬 直接回复本任务（点开即用，无需登录）
http://127.0.0.1:8077/reply/eyJ0IjoidGFza18wN2EwYjk2NTQy...
链接有效期至 2026-10-21 15:15
```

同时这封邮件的内容会作为一条 `source=email` 的 `agent` 消息记入任务线程，控制台能看到「邮件 → 会话」的完整上下文。

### 3) `GET /api/v1/reply/{token}` — 收件人侧（无需鉴权）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/reply/{token}` | 打开会话：返回 `session` + 完整 `messages` |
| GET | `/api/v1/reply/{token}/messages` | 增量拉取新消息，支持 `wait_seconds` 长轮询 |
| POST | `/api/v1/reply/{token}` | 用户回帖（`{content, author?}`），限流为每任务 60 条/小时 |
| GET | `/api/v1/reply/{token}/link` | 用旧链接自助换一个新的（续期） |

失败语义：签名不对 `401 invalid_signature`；被轮换掉 `401 token_revoked`；任务已关闭 `403 task_closed`（仍可只读查看历史）。

### 4) `GET /api/v1/tasks/{task_id}/messages` — Agent 取回用户回复

| 参数 | 说明 |
| --- | --- |
| `after_id` | 只取这条消息之后的新消息，避免重复处理 |
| `role` | `user` / `agent` / `system` 过滤 |
| `wait_seconds` | **长轮询**，最多阻塞 60 秒等新消息，用户一回帖立即返回（默认 `mark_read=true` 会把待回复计数清零） |
| `include_link` | 是否顺带签发一个新的回复链接 |

响应里 `messages` 与 `items` 是同一份数据（兼容别名），并附带上最新的 `task` 状态。

### 5) 其余任务接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/tasks` | `tasks:read` | 任务列表（`page` / `page_size` / `status` / `q`）+ 全局 `stats` |
| GET | `/api/v1/tasks/{id}` | `tasks:read` | 任务详情 + 会话线程 |
| PATCH | `/api/v1/tasks/{id}` | `tasks:write` | 改标题 / Agent 名 / 状态 / 上下文 |
| POST | `/api/v1/tasks/{id}/messages` | `tasks:write` | Agent 发消息；`notify_email: ["a@b.com"]` 可同时推一份邮件（自动带链接） |
| POST | `/api/v1/tasks/{id}/close` | `tasks:write` | 关闭任务（回帖随即被拒） |
| POST | `/api/v1/tasks/{id}/reopen` | `tasks:write` | 重新打开 |
| POST | `/api/v1/tasks/{id}/reply-link` | `tasks:write` | **轮换链接：此前发出的所有链接立即失效**（token 版本号 +1） |
| DELETE | `/api/v1/tasks/{id}` | `tasks:write` | **删除任务及其全部会话消息**（不可恢复；邮件发送记录保留但解除关联）。控制台「任务会话」页也有「删除」按钮 |

### 6) 最小可跑示例

```bash
API=http://127.0.0.1:8077
KEY=sk-agent-xxxxx

# ① 启动任务
TASK=$(curl -s -X POST $API/api/v1/tasks -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"title":"季度数据报表生成","agent_name":"ReportBot"}')
TID=$(echo "$TASK" | python -c "import sys,json;print(json.load(sys.stdin)['data']['task_id'])")

# ② 发邮件（正文自动带回复链接）
curl -s -X POST $API/api/v1/mail/send -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"to\":[\"you@example.com\"],\"subject\":\"报表已生成\",
       \"body\":\"2026-Q3 报表已生成，环比 +18.4%。要推送到正式库吗？\",
       \"task_id\":\"$TID\"}"

# ③ 长轮询等用户回帖（最多阻塞 60 秒）
curl -s "$API/api/v1/tasks/$TID/messages?role=user&wait_seconds=60" \
  -H "X-API-Key: $KEY"

# ④ 不再需要时关闭任务，链接立即失效
curl -s -X POST $API/api/v1/tasks/$TID/close -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' -d '{}'
```

---

## 六、把 Agent 接进来

```python
import requests

API, KEY = "http://127.0.0.1:8077", "sk-agent-xxxxx"

manifest = requests.get(f"{API}/api/v1/agent/tools").json()["data"]
tools = manifest["tools"]          # 直接注册给模型的 tool 列表

def dispatch(tool_name, args):
    tool = next(t for t in tools if t["function"]["name"] == tool_name)
    return requests.request(
        tool["method"], tool["endpoint"],
        headers={"X-API-Key": KEY}, json=args, timeout=60,
    ).json()
```

当前暴露 **8 个工具**：

| 工具 | 用途 |
| --- | --- |
| `send_email` | 发信。可带 `task_id`，正文自动附加回复链接 |
| `list_email_logs` | 查发送记录 |
| `list_email_templates` | 查内置模板 |
| `check_mail_connection` | SMTP 连通性自检 |
| `create_task` | **启动任务，拿 `task_id` + `reply_url`** |
| `get_task_messages` | 取会话消息（`wait_seconds` 长轮询等用户回帖） |
| `post_task_message` | 向任务线程发消息（可 `notify_email` 同时推一封） |
| `close_task` | 关闭任务，回复链接随之失效 |

一个典型 Agent 循环：

```python
tid = dispatch("create_task", {"title": "季度数据报表生成"})["data"]["task_id"]

dispatch("send_email", {
    "to": ["you@example.com"],
    "subject": "报表已生成",
    "body": "2026-Q3 报表已生成，环比 +18.4%。是否推送到正式库？",
    "task_id": tid,          # ← 关键：正文会自动附上回复链接
})

# 等用户点链接回帖（长轮询，最多阻塞 60 秒）
reply = dispatch("get_task_messages", {"task_id": tid, "role": "user", "wait_seconds": 60})

dispatch("close_task", {"task_id": tid})   # 收工，链接立即失效
```

`GET /api/v1/agent/manifest` 里另有 `task_flow` 字段，直接给出上面这条链路的中文说明，方便把整段流程喂给模型。

---

## 七、错误码

| code | HTTP | 含义 | 可重试 |
| --- | --- | --- | --- |
| `missing_api_key` | 401 | 未带 API Key | — |
| `invalid_api_key` | 401 | Key 无效或已停用 | — |
| `insufficient_scope` | 403 | 权限不足 | — |
| `rate_limit_exceeded` | 429 | 超出每小时配额 | 下个窗口 |
| `validation_error` | 422 | 参数校验失败（含 `details`） | — |
| `empty_body` / `empty_subject` | 400 | 缺少正文 / 主题 | — |
| `unknown_template` | 400 | 模板不存在（返回可用列表） | — |
| `attachment_too_large` | 400 | 附件超限 | — |
| `smtp_connect_failed` | 502 | 连不上 SMTP | ✅ 服务端已自动重试 2 次 |
| `smtp_auth_failed` | 502 | 账号/密码错误，或服务商未开启 SMTP | ❌ 先修配置 |
| `sender_refused` | 502 | 发件地址与 SMTP 账号不一致 | ❌ |
| `recipient_refused` | 502 | 收件人被服务器拒绝 | ❌ |
| `task_not_found` | 404 | 任务不存在或已删除 | — |
| `invalid_token` | 401 | 回复链接格式不对 / 无法解析 | ❌ |
| `invalid_signature` | 401 | 回复链接被篡改（签名校验失败） | ❌ 让 Agent 重发 |
| `token_expired` | 401 | 回复链接已过期 | ❌ 让 Agent 重发 |
| `token_revoked` | 401 | 链接已被轮换吊销 | ❌ 用最新那封邮件 |
| `task_closed` | 403 | 任务已关闭，不能再回帖（仍可只读查看） | — |
| `reply_rate_limited` | 429 | 该任务回帖过于频繁 | 下个窗口 |
| `message_too_long` | 400 | 单条消息超过 `MESSAGE_MAX_CHARS` | — |

---

## 八、设计要点

- **统一响应包裹**：成功 `{ok:true,data}`，失败 `{ok:false,error:{code,message,...}}`，都带 `request_id`，日志可按 `request_id` 串联。
- **自动重试**：只对可重试错误（连接失败、连接中断）重连重试，最多 2 次、指数退避；认证失败立即返回，不做无用重试。
- **幂等**：`idempotency_key` 唯一索引兜底，Agent 超时重发不会造成重复邮件，命中时返回 `deduplicated: true`。
- **限流**：每密钥每小时滑动窗口，`429` 时返回已用次数与上限；用户回帖另有一条按任务的限流。
- **日志落库**：每次发送（含失败）都写 `mail_logs`，含耗时、大小、附件名、错误码、正文摘要；带 `task_id` 的还会记下当时的 `reply_url`。
- **自动补列迁移**：`storage._migrate()` 给已存在的旧库补新列（`mail_logs.task_id` / `reply_url` 等），升级不丢数据。
- **单端口交付**：前端构建产物由后端托管，`/assets` 走静态目录，其余路径回落 `index.html`（SPA 路由）。
- **离线友好**：前端依赖全部打包进本地 `dist`，不引用任何 CDN；Swagger 页面除外（走 FastAPI 默认 CDN）。

任务会话与回复链接的额外设计：

- **无状态令牌，不存会话**：回复链接是 `base64url(payload).base64url(HMAC-SHA256)`，载荷只有「任务 ID + 版本号 + 过期时间戳 + 随机 nonce」，服务端不需要为每个收件人存 session。改任意一位都会签名校验失败。
- **秒级吊销**：`tasks.token_version` 自增即让此前发出去的所有链接失效（`POST /tasks/{id}/reply-link`）。适合「链接误转到群里」的补救。关闭任务同样立即拒绝回帖。
- **权限最小化**：持链接者只能读写**这一个任务**的会话，无法枚举其它任务，也拿不到任何 API Key 能力。
- **重启不失效**：密钥优先取 `.env` 的 `REPLY_TOKEN_SECRET`，否则落到 `data/reply_secret.txt`，服务重启后旧链接继续可用。
- **前后端双长轮询**：Agent 侧用 `wait_seconds` 等用户回复，回复页用 `wait_seconds=25` 等 Agent 的新消息，双方都不空转轮询。
- **公开路由与鉴权路由分离**：`/reply/:token` 在路由表里标了 `meta.bare`，直接整页渲染、不套控制台外壳，因此**不会**出现「请先配置 API Key」的拦截。

---

## 九、安全提示（部署前必读）

**仓库本身不含任何凭据**：`.env`、`data/`（API 密钥、回复链接签名私钥、SQLite 库）均已在 `.gitignore` 中排除；
`backend/app/config.py` 里的 SMTP 主机是 `smtp.example.com` 占位，账号、密码、发件地址、测试收件人的默认值全部为空字符串。凭据一律由部署后的管理员在本机填写。
> 一句话：默认配置可以直接跑起来（不配 SMTP 只是发不出信），但绝不会带着任何人的真实凭据出厂。

1. 复制 `backend/.env.example` 为 `backend/.env` 后再填写 SMTP 信息；**不要**把填好的 `.env` 提交进任何仓库。
2. 建议使用**邮件服务商的应用专用密码**，而不是账号主密码。
3. `BOOTSTRAP_API_KEY` / `ADMIN_API_KEY` 留空即让服务在首次启动时随机生成（明文只打印一次并写入 `data/keys.txt`）；**不要**在 `.env` 里写死固定密钥。
4. 对外暴露时不要直接把 `8077` 端口开到公网：请放在 Nginx / Caddy 后面加 HTTPS，并限定来源 IP。
5. `STORE_BODY_PREVIEW=true` 会把正文摘要存进 SQLite；处理敏感内容时请置为 `false`。
6. **回复链接等同于凭证**：拿到链接的人就能读写该任务会话。因此
   - 务必同时配好 `PUBLIC_BASE_URL` 与 HTTPS（明文 HTTP 下链接会在中间环节泄露）；
   - 建议把 `REPLY_TOKEN_TTL_DAYS` 调小（如 7 天），任务结束后主动 `close`；
   - 链接一旦外泄，用 `POST /api/v1/tasks/{id}/reply-link` 轮换即可立刻止血；
   - `data/reply_secret.txt` 等同于签名私钥，泄露等于可以伪造任意任务的链接，勿入库、勿外传。
7. 回复页是匿名可写的公开端点，生产环境建议再加一层 Nginx 限速 / 人机校验。
8. 提交前自查一行命令（占位符 `sk-agent-xxxxxxxx` 与这条命令自身会被过滤掉，命中真实值才会打印）：

   ```bash
   git ls-files -z | xargs -0 grep -nIE "SMTP_PASSWORD=.+|sk-(agent|admin)-[A-Za-z0-9_-]{10,}|[A-Za-z0-9._%+-]+@(qq|gmail|163|outlook|zohomail)\.[a-z]+" \
     | grep -v 'x\{8\}' | grep -v 'git ls-files' || echo "clean"
   ```

---

## 十、后续扩展

新增一个能力模块只需三步：在 `app/routers/` 加路由、在 `app/services/` 写业务、在 `agent.py::_tools()` 里补一条工具定义，Agent 侧无需改代码即可发现新工具。

---

## 十一、许可

[MIT](LICENSE) © 2026 TansirFlow —— 可自由使用、修改、商用，保留版权声明即可。
