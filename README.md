# Postroom

> Postroom = post（通信）+ room（会话室）：给 AI Agent 用的一间「收发室」。

面向 **AI Agent** 的工具型 API 服务器：Python(FastAPI) 后端 + Vue 3 前端控制台。让 Agent 不只会发通知，还能**等到人的回话再继续干活**。

**多用户**：网页端用账号密码登录，账号由管理员创建（不开放自助注册）。每个账号的 **SMTP 配置、API 密钥、发信记录、任务会话全部互相隔离** —— 同一个部署可以给多个团队/多台 Agent 用，各自用自己的发信通道与回复链接域名。

三个能力模块：

1. **邮件发送**（任意 SMTP 服务商，可全局共用也可每账号独立）——Agent 直接调接口发信；
2. **任务会话 + 免登录回复链接**——Agent 启动任务时拿一个 `task_id`，之后每封邮件正文都会自动附带一个「点开即回复」的网页链接，**收件人不用登录、不用装 App**，点开就能在浏览器里和 Agent 多轮对话；Agent 用长轮询把回复取回去，形成闭环；
3. **多用户管理**——管理员在网页里建账号、重置密码、启停；每个账号在「系统设置」里填自己的 SMTP 与对外域名。

Agent 拉一次 `GET /api/v1/agent/tools` 就能拿到 OpenAI function-calling 格式的 8 个工具定义，直接注册进自己的工具列表即可，无需人工写 prompt。

```
postroom/
├─ backend/                     # FastAPI 服务
│  ├─ app/
│  │  ├─ main.py                # 应用入口：中间件、异常包装、首启建管理员、静态托管
│  │  ├─ config.py              # 配置（读 .env）
│  │  ├─ storage.py             # SQLite 存储（用户/设置/密钥/发信日志/任务/消息，含自动补列迁移）
│  │  ├─ security.py            # 双凭证鉴权（API Key 或登录会话）+ 权限校验 + 按用户隔离
│  │  ├─ passwords.py           # 口令哈希（pbkdf2_sha256）+ 强度策略 + 随机密码
│  │  ├─ ratelimit.py           # 每小时配额（发信 / 登录）
│  │  ├─ schemas.py             # 请求/响应模型
│  │  ├─ tokens.py              # 无状态 HMAC 令牌（回复链接 / 登录会话，用 kind 区分）
│  │  ├─ services/
│  │  │  ├─ mailer.py           # SMTP 发送（按用户取配置、重试、错误归类、连通性检测）
│  │  │  ├─ templates.py        # 内置邮件模板
│  │  │  └─ replylink.py        # 回复链接签发 + 把链接按钮追加进邮件正文
│  │  └─ routers/
│  │     ├─ auth.py             # 登录 / 当前身份 / 改密 / 全设备登出
│  │     ├─ users.py            # 用户管理（仅管理员）
│  │     ├─ settings.py         # 每账号设置（SMTP / 对外地址 / 测试收件人）
│  │     ├─ mail.py             # 邮件接口（agent 主入口）
│  │     ├─ tasks.py            # 任务会话（建任务/发消息/长轮询取用户回复/吊销链接）
│  │     ├─ reply.py            # 免登录回复页后端（链接即凭证，无需登录）
│  │     ├─ keys.py             # 密钥管理
│  │     ├─ system.py           # 健康检查 / 公开站点信息 / 概览
│  │     └─ agent.py            # 工具自描述（tools / manifest）
│  ├─ requirements.txt
│  ├─ run.py                    # 启动脚本
│  └─ .env.example              # 配置模板（复制为 .env 后由管理员填写；.env 不入库）
├─ frontend/                    # Vue 3 + Vite 控制台
│  └─ src/{App.vue,components/*,views/*,api.js,router.js,styles.css}
├─ tests/
│  └─ test_task_flow.py         # 任务会话 + 回复链接 + 多用户隔离端到端回归（自清理，88 项断言）
├─ screenshots/                 # 登录页 / 使用教程 / 控制台 / 设置 / 用户管理 / 回复页截图
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

打开 <http://127.0.0.1:8077/> —— 控制台登录页。

**首次启动会自动创建管理员账号，并打印在控制台**（同时写入 `backend/data/keys.txt`）：

| 用途 | 说明 |
| --- | --- |
| 控制台账号 `admin` / 随机密码 | 网页登录用。用户名可用 `BOOTSTRAP_ADMIN_USERNAME` 改，密码可用 `BOOTSTRAP_ADMIN_PASSWORD` 预先指定 |
| `ADMIN_API_KEY` | 该账号的根密钥，拥有全部权限（含密钥与用户管理） |
| `AGENT_API_KEY` | 给 Agent 用，默认 `mail:send` + `mail:read` + `tasks:write` + `tasks:read` |

登录后请先到「系统设置」改密码；新账号在「用户管理」里创建。也可以改用 API Key 直接调接口（Agent 场景）。

左侧「**开始 → 使用教程**」是一份按顺序的上手引导（配 SMTP → 建密钥 → 把接口交给 Agent），
里面有一段可以直接复制给 Agent 的接入提示词，第一次部署完照着走一遍即可。

**开发模式**：后端 `run.py`，前端 `dev-frontend.cmd`（Vite 5173，`/api` 已代理到 8077）。

界面速览（`screenshots/`）：

| 文件 | 内容 |
| --- | --- |
| `login.png` | **登录页**（账号密码，无自助注册入口） |
| `guide.png` | **使用教程**：登录后的上手引导（三步上手 / 任务闭环 / 可复制的接入提示词 / 报错排查 / 验收清单） |
| `dashboard.png` | 控制台概览，含「待回复」告警与任务统计 |
| `settings.png` | **系统设置**：本账号 SMTP / 对外地址 / 改密码 |
| `users.png` | **用户管理**（仅管理员）：建账号、重置密码、启停 |
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

**全局 SMTP（可选）**：下面这组是**服务器级默认值**。每个账号都可以在网页「系统设置」里配置自己的 SMTP；
只要该账号填了自己的主机，就完全用他自己的，否则回落到这里的全局配置。
所以只想让所有人共用一个发信通道 → 只填这里就够了。

| 变量 | 默认值 / 示例 | 说明 |
| --- | --- | --- |
| `HOST` / `PORT` | `127.0.0.1` / `8077` | 监听地址。对外提供服务时改 `0.0.0.0` |
| `SMTP_HOST` / `SMTP_PORT` | 空 / `465` | 任意 SMTP 服务商；465 端口需配 `SMTP_USE_SSL=true` |
| `SMTP_USE_SSL` | `true` | 465 端口必须为 true |
| `SMTP_USER` / `SMTP_PASSWORD` | **空（管理员填写）** | SMTP 认证账号与应用专用密码 |
| `SMTP_FROM_EMAIL` | 空 | **必须与认证账号一致**，否则多数服务商会拒发 |
| `SMTP_FROM_NAME` | `Postroom` | 收件人看到的发件人名字 |
| `RATE_LIMIT_PER_HOUR` | `120` | 每密钥每小时发信上限 |
| `TEST_RECIPIENTS` | 空 | 全局测试收件人（账号可在设置页覆盖），逗号分隔。留空则该功能不显示 |
| `ICP_LICENSE` | 空 | **页脚悬挂的 ICP 备案号**，如 `苏ICP备2026000000号`。留空则页脚不渲染 |
| `ICP_LICENSE_URL` | `https://beian.miit.gov.cn/` | 备案号点击跳转地址（默认工信部备案管理系统） |
| `STORE_BODY_PREVIEW` | `true` | 是否在日志中留正文摘要（前 1500 字） |

**多用户 / 登录**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BOOTSTRAP_ADMIN_USERNAME` | `admin` | 首次启动自动创建的管理员用户名 |
| `BOOTSTRAP_ADMIN_PASSWORD` | 空 | 留空则随机生成并打印一次（**不要**写死弱口令） |
| `SESSION_TTL_HOURS` | `72` | 登录会话有效期（小时） |
| `LOGIN_RATE_LIMIT_PER_HOUR` | `20` | 同一用户名 + IP 每小时登录尝试上限 |
| `SESSION_COOKIE_NAME` | `postroom_session` | 会话令牌同时写入的 HttpOnly Cookie 名（生产 HTTPS 下带 `Secure`） |

**任务会话 / 回复链接相关**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PUBLIC_BASE_URL` | 空 | 生成回复链接用的对外地址。留空则取当前请求的 host（本机即 `http://127.0.0.1:8077`）。**要让外部收件人点得开，必须填公网可访问的地址**，如 `https://mail.example.com`。账号可在「系统设置」里覆盖为自己的域名 |
| `REPLY_TOKEN_SECRET` | 空 | 回复链接 + 登录会话的签名密钥。留空自动生成并持久化到 `data/token_secret.txt`（改它会一次性作废所有旧链接与所有登录会话） |
| `REPLY_TOKEN_TTL_DAYS` | `30` | 回复链接默认有效期（天） |
| `REPLY_RATE_LIMIT_PER_HOUR` | `60` | 单个任务下用户每小时最多回信条数 |
| `MESSAGE_MAX_CHARS` | `4000` | 单条会话消息最大字数 |

---

## 三、多用户与权限

### 3.1 两种凭证

| 谁 | 怎么认证 | 传法 |
| --- | --- | --- |
| **人（网页控制台）** | 账号密码登录换**会话令牌** | `Authorization: Bearer <token>`（同时写入 HttpOnly Cookie） |
| **Agent / 脚本** | **API Key** | `X-API-Key: sk-agent-xxxxx`，或 `Authorization: Bearer sk-agent-xxxxx` |

两类凭证都是**无状态 HMAC 签名令牌**，服务端没有 session 表。

- 会话令牌载荷 `{u: 用户ID, s: 会话版本, e: 过期时间, n: nonce}`；用户改密码 / 被管理员重置密码 / 调 `/auth/logout-all` 时 `session_version` 自增，**该账号所有已登录设备立即失效**。
- 回复链接载荷 `{t: 任务ID, v: 链接版本, e, n}`，两者靠载荷里的 `k`（kind）字段区分，互不通用。

### 3.2 权限

| 权限 | 能做什么 |
| --- | --- |
| `mail:send` | 发信、查模板、测试 SMTP 连接 |
| `mail:read` | 查发送记录、统计 |
| `tasks:write` | 建任务、向任务线程发消息、吊销/轮换回复链接、关闭任务 |
| `tasks:read` | 查任务列表/详情、长轮询取用户回复 |
| `keys:manage` | 增删改**本账号**的 API 密钥 |
| `users:manage` | 管理用户账号（**仅管理员**，且不能给自己发放之外的账号分配） |

**网页登录的人**默认拿到前五项全权限；**API Key** 按创建时勾选的权限来。
判断权限只看 `scopes`，管理员也不做「权限直通」——一个只有 `mail:send` 的 Agent 密钥不会因为归属管理员账号就获得建账号的能力。

### 3.3 数据隔离（全部按账号）

| 数据 | 隔离方式 |
| --- | --- |
| API 密钥 | `api_keys.user_id`，列表/启停/删除都只看自己账号的 |
| 发信记录 | `mail_logs.user_id`，列表、详情、统计、幂等键全部按账号 |
| 任务会话 | `tasks.user_id`，别人的 `task_id` 一律返回 `404 task_not_found`（不泄露存在性） |
| SMTP / 对外地址 / 测试收件人 | `user_settings` 表，一行一个账号 |
| 用户账号 | `users` 表，`role` = `admin` / `user` |

管理员**看不到**别人的邮件、任务与密钥——他只能管理**账号本身**。这是刻意的：管理员是运维角色，不是数据上帝。

### 3.4 账号生命周期

| 操作 | 接口 | 说明 |
| --- | --- | --- |
| 登录 | `POST /api/v1/auth/login` | `{username, password}` → `{token, expires_at, user}`；失败统一 401，不暴露用户名是否存在 |
| 当前身份 | `GET /api/v1/auth/me` | 返回 `user_id` / `username` / `role` / `scopes` / `via`（`session` 或 `api_key`） |
| 改自己密码 | `POST /api/v1/auth/password` | 需网页会话；改完返回**新令牌**，旧设备全下线 |
| 退出登录 | `POST /api/v1/auth/logout` | 清 Cookie；令牌本身无状态，前端丢弃即可 |
| 全设备下线 | `POST /api/v1/auth/logout-all` | `session_version` +1 |
| 建账号 | `POST /api/v1/users` | **仅管理员**；密码留空则自动生成，明文只返回一次 |
| 重置密码 | `POST /api/v1/users/{id}/password` | 仅管理员；返回一次性新密码，并踢掉对方全部登录 |
| 启停 / 改角色 | `PATCH /api/v1/users/{id}` | 不能停用/删除自己，也不能删掉/降级**最后一个启用的管理员** |
| 删除账号 | `DELETE /api/v1/users/{id}` | 级联清理该账号的密钥、发信记录、任务与会话消息 |

> 注意：`/api/v1/reply/*` **不需要任何凭证** —— 链接里的签名令牌本身就是凭证，这正是「收件人点开就能回」的实现方式。它只能读写**那一个任务**。

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
| GET | `/api/v1/site` | — | 公开站点信息：应用名 / 版本 / **ICP 备案号**（无需鉴权，页脚与登录页用） |
| GET | `/api/v1/agent/tools` | — | Agent 工具清单（function-calling 格式） |
| GET | `/api/v1/agent/manifest` | — | 能力摘要 + 快速上手代码 |
| GET | `/api/v1/whoami` | 任意 | 校验凭证与权限 |
| GET | `/api/v1/overview` | `mail:read` | 控制台概览（含本账号任务统计与生效 SMTP 配置） |
| POST | `/api/v1/auth/login` | — | 账号密码登录，换会话令牌 |
| GET | `/api/v1/auth/me` | 任意 | 当前登录身份与权限 |
| POST | `/api/v1/auth/password` | 网页会话 | 修改自己的密码（旧会话全部失效） |
| GET | `/api/v1/settings` | `mail:send` / `mail:read` | 读取本账号设置（SMTP 只回 `password_set` 标志） |
| PUT | `/api/v1/settings` | 网页会话 | 保存本账号 SMTP / 对外地址 / 测试收件人 |
| POST | `/api/v1/settings/smtp-test` | 网页会话 | 测试 SMTP 连通性（可先测未保存的配置） |
| GET/POST/PATCH/DELETE | `/api/v1/users...` | `users:manage` | 用户管理（仅管理员） |
| GET | `/api/v1/mail/logs` | `mail:read` | 分页查询（`page` / `page_size` / `status` / `q`） |
| GET | `/api/v1/mail/logs/{id}` | `mail:read` | 单封详情 |
| GET | `/api/v1/mail/stats` | `mail:read` | 统计 + 限流快照 |
| GET | `/api/v1/mail/templates` | `mail:send` | 模板及变量 |
| POST | `/api/v1/mail/verify-connection` | `mail:send` | SMTP 连通性 / 登录测试（用本账号生效的配置） |
| GET/POST/PATCH/DELETE | `/api/v1/keys...` | `keys:manage` | 密钥管理（仅本账号） |

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
| `missing_credentials` | 401 | 未带任何凭证（既没登录也没 API Key） | — |
| `invalid_credentials` | 401 | 凭证无效 / 已失效 / 账号被停用；登录时也表示用户名或密码不对（统一错误，不暴露用户名是否存在） | 重新登录 |
| `account_disabled` | 403 | 账号已被停用 | 联系管理员 |
| `login_rate_limited` | 429 | 登录尝试过于频繁 | 下个窗口 |
| `session_required` | 403 | 该操作必须在网页登录后进行（不能只用 API Key） | — |
| `admin_required` | 403 | 仅管理员可执行 | — |
| `insufficient_scope` | 403 | 权限不足 | — |
| `scope_not_allowed` | 403 | 试图发放自己也没有的权限 | — |
| `invalid_api_key` | 401 | Key 无效或已停用（错误码兼容保留） | — |
| `rate_limit_exceeded` | 429 | 超出每小时配额 | 下个窗口 |
| `validation_error` | 422 | 参数校验失败（含 `details`） | — |
| `empty_body` / `empty_subject` | 400 | 缺少正文 / 主题 | — |
| `unknown_template` | 400 | 模板不存在（返回可用列表） | — |
| `attachment_too_large` | 400 | 附件超限 | — |
| `smtp_not_configured` | 502 | 本账号与全局都没配 SMTP | 先去「系统设置」填 |
| `smtp_connect_failed` | 502 | 连不上 SMTP | ✅ 服务端已自动重试 2 次 |
| `smtp_auth_failed` | 502 | 账号/密码错误，或服务商未开启 SMTP | ❌ 先修配置 |
| `sender_refused` | 502 | 发件地址与 SMTP 账号不一致 | ❌ |
| `recipient_refused` | 502 | 收件人被服务器拒绝 | ❌ |
| `task_not_found` | 404 | 任务不存在、已删除，或不属于当前账号 | — |
| `key_not_found` | 404 | 密钥不存在或不属于当前账号 | — |
| `user_not_found` | 404 | 用户不存在 | — |
| `username_taken` | 409 | 用户名已存在 | — |
| `weak_password` | 400 | 密码不符合强度策略 | — |
| `wrong_password` | 400 | 改密时当前密码不正确 | — |
| `cannot_delete_self` / `cannot_disable_self` | 400 | 不能删除 / 停用当前登录的账号 | — |
| `last_admin` | 400 | 不能停用、删除或降级最后一个启用中的管理员 | — |
| `invalid_base_url` | 400 | 对外地址需以 `http://` 或 `https://` 开头 | — |
| `invalid_token` | 401 | 令牌格式不对 / 无法解析 / 用途不匹配 | ❌ |
| `invalid_signature` | 401 | 令牌被篡改（签名校验失败） | ❌ 让 Agent 重发 |
| `token_expired` | 401 | 回复链接**或登录会话**已过期 | 重发 / 重新登录 |
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
- **自动补列迁移**：`storage._migrate()` 给已存在的旧库补新列（`user_id` / `mail_logs.task_id` / `reply_url` 等），升级不丢数据。
- **单端口交付**：前端构建产物由后端托管，`/assets` 走静态目录，其余路径回落 `index.html`（SPA 路由）。
- **页脚备案号**：配置 `ICP_LICENSE` 后，**控制台与免登录回复页**的页脚都会悬挂备案号并链接到工信部；
  留空则页脚整体不渲染。备案号属于部署方信息，仓库与默认配置里都不带。
- **离线友好**：前端依赖全部打包进本地 `dist`，不引用任何 CDN；Swagger 页面除外（走 FastAPI 默认 CDN）。

多用户的额外设计：

- **口令哈希**：`pbkdf2_sha256`，20 万次迭代 + 16 字节随机盐，存成 `pbkdf2_sha256$迭代数$盐$摘要`；校验用 `hmac.compare_digest`。迭代数偏低的老口令会在登录成功时顺手升级。
- **会话无状态**：登录不写 session 表，令牌即 `base64url(payload).base64url(HMAC-SHA256)`，载荷含用户 ID 与会话版本。改密码 / 被重置 / 主动全设备下线 → `session_version` 自增，旧令牌全部作废。
- **两类令牌不可混用**：载荷里带 `k`（`reply` / `sess`），校验时强制比对；历史回复令牌（没有 `k`）按 `reply` 兼容处理。
- **鉴权只认 scopes**：不做「管理员直通」，避免一把只有 `mail:send` 的 Agent 密钥因为归属管理员账号而获得建账号能力。
- **归属失败即 404**：越权访问别人的任务/密钥统一返回 `404`，而不是 `403`，不泄露资源是否存在。
- **设置两级回落**：账号自己的 `user_settings` 优先，未配（`smtp.host` 为空）则用服务器 `.env`；账号一旦填了自己的主机，就**完全**用自己的（不再混入全局密码），避免把 A 服务商的密码带给 B 服务商。
- **首启自举**：`main._bootstrap()` 保证「至少一个管理员账号 + 一把根密钥 + 一把默认 Agent 密钥」存在，且**只创建一次**（重启不会重复建账号或重复追加密钥）。随机密码与密钥只打印一次并写入 `data/keys.txt`。
- **前端令牌失效自愈**：任何请求收到 `401` 即清空本地会话并跳回登录页（保留 `redirect` 参数），不会卡在白屏或半登录状态。

任务会话与回复链接的额外设计：

- **无状态令牌，不存会话**：回复链接载荷只有「任务 ID + 版本号 + 过期时间戳 + 随机 nonce」，服务端不需要为每个收件人存 session。改任意一位都会签名校验失败。
- **秒级吊销**：`tasks.token_version` 自增即让此前发出去的所有链接失效（`POST /tasks/{id}/reply-link`）。适合「链接误转到群里」的补救。关闭任务同样立即拒绝回帖。
- **权限最小化**：持链接者只能读写**这一个任务**的会话，无法枚举其它任务，也拿不到任何 API Key 能力。
- **重启不失效**：密钥优先取 `.env` 的 `REPLY_TOKEN_SECRET`，否则落到 `data/token_secret.txt`，服务重启后旧链接继续可用。
- **前后端双长轮询**：Agent 侧用 `wait_seconds` 等用户回复，回复页用 `wait_seconds=25` 等 Agent 的新消息，双方都不空转轮询。
- **公开路由与登录路由分离**：`/login` 与 `/reply/:token` 在路由表里标了 `meta.bare`，直接整页渲染、不套控制台外壳，因此不会被登录守卫拦下。

---

## 九、安全提示（部署前必读）

**仓库本身不含任何凭据**：`.env`、`data/`（账号口令哈希、API 密钥、令牌签名私钥、SQLite 库）均已在 `.gitignore` 中排除；
`backend/app/config.py` 里的 SMTP 主机默认为空，账号、密码、发件地址、测试收件人的默认值全部为空字符串。凭据一律由部署后的管理员在本机/网页填写。
> 一句话：默认配置可以直接跑起来（不配 SMTP 只是发不出信），但绝不会带着任何人的真实凭据出厂。

1. 复制 `backend/.env.example` 为 `backend/.env` 后再填写 SMTP 信息；**不要**把填好的 `.env` 提交进任何仓库。
2. 建议使用**邮件服务商的应用专用密码**，而不是账号主密码。
3. `BOOTSTRAP_API_KEY` / `ADMIN_API_KEY` / `BOOTSTRAP_ADMIN_PASSWORD` 留空即让服务在首次启动时随机生成（明文只打印一次并写入 `data/keys.txt`）；**不要**在 `.env` 里写死固定口令或密钥。首次登录后请立刻改密码。
4. 对外暴露时不要直接把 `8077` 端口开到公网：请放在 Nginx / Caddy 后面加 HTTPS，并限定来源 IP。
   **没有 HTTPS 时不要开放登录页** —— 账号密码会以明文经过中间链路（会话令牌在 `ENV=prod` 下才会带 `Secure` Cookie 标志）。
5. `STORE_BODY_PREVIEW=true` 会把正文摘要存进 SQLite；处理敏感内容时请置为 `false`。
6. 新建账号后请立刻把一次性密码转交本人，并让其自行修改；管理员也应定期在「用户管理」里重置不再使用的账号密码（重置会让该账号所有设备下线）。
7. **回复链接等同于凭证**：拿到链接的人就能读写该任务会话。因此
   - 务必同时配好 `PUBLIC_BASE_URL` 与 HTTPS（明文 HTTP 下链接会在中间环节泄露）；
   - 建议把 `REPLY_TOKEN_TTL_DAYS` 调小（如 7 天），任务结束后主动 `close`；
   - 链接一旦外泄，用 `POST /api/v1/tasks/{id}/reply-link` 轮换即可立刻止血；
   - `data/token_secret.txt` 等同于签名私钥，泄露等于可以伪造任意任务的回复链接与任意登录会话，勿入库、勿外传。
8. 回复页是匿名可写的公开端点，生产环境建议再加一层 Nginx 限速 / 人机校验。
9. 提交前自查一行命令（占位符 `sk-agent-xxxxxxxx` 与这条命令自身会被过滤掉，命中真实值才会打印）：

   ```bash
   git ls-files -z | xargs -0 grep -nIE "SMTP_PASSWORD=.+|sk-(agent|admin)-[A-Za-z0-9_-]{10,}|[A-Za-z0-9._%+-]+@(qq|gmail|163|outlook|zohomail)\.[a-z]+" \
     | grep -v 'x\{8\}' | grep -v 'git ls-files' || echo "clean"
   ```

---

## 十、回归测试

```bash
# ① 仅本地链路（不发信）：需要一把 Agent 密钥
AGENT_API_KEY=sk-agent-xxxxx \
  backend/.venv/Scripts/python.exe tests/test_task_flow.py

# ② 追加上多用户段落（登录 / 隔离 / 设置 / 账号管理），需要管理员账号
AGENT_API_KEY=sk-agent-xxxxx ADMIN_USERNAME=admin ADMIN_PASSWORD=xxxxxx \
  backend/.venv/Scripts/python.exe tests/test_task_flow.py

# ③ 额外真实发一封测试邮件
AGENT_API_KEY=sk-agent-xxxxx TEST_RECIPIENT=you@example.com \
  backend/.venv/Scripts/python.exe tests/test_task_flow.py --send
```

覆盖 **88 项断言**，全程自清理（建的任务、建的账号都会删掉）：

| 段落 | 内容 |
| --- | --- |
| 0 | `replylink.decorate()` 正文装饰纯函数 |
| 1–3 | 建任务 → Agent 发消息 → 邮件内嵌回复链接（可选真实发信） |
| 4–7 | 链接轮换吊销 → 免登录打开 → 用户回帖 → Agent 增量/长轮询取回 |
| 8–10 | 篡改签名 / 畸形令牌 / 无凭证访问 / 关闭任务 / 删除任务 |
| B1–B2 | 登录成功与失败路径、会话令牌鉴权、仅管理员可建账号 |
| B3 | **数据隔离**：新账号看不到任何既有数据；管理员与原 Agent 密钥也看不到新账号的任务（404） |
| B4 | **每账号独立 SMTP**：回落全局 → 保存自己的配置 → 生效值切换 → 密码不回传 → 回复链接用自己域名 |
| B5 | 改密踢下线 / 重置密码 / 停用后登录被拒且有会话失效 / 不能删自己 / 级联删除账号 |

---

## 十一、后续扩展

新增一个能力模块只需三步：在 `app/routers/` 加路由、在 `app/services/` 写业务、在 `agent.py::_tools()` 里补一条工具定义，Agent 侧无需改代码即可发现新工具。

新增一个「按账号隔离」的数据表：建表时加 `user_id` 列，查询统一走 `storage._scope()`（传具体 ID 只看该账号，传 `None` 只看无归属数据，永远不会退化成「看全部」）。

---

## 十二、许可

[MIT](LICENSE) © 2026 TansirFlow —— 可自由使用、修改、商用，保留版权声明即可。
