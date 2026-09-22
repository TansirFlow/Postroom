<script setup>
import { computed, ref } from 'vue'
import { state, toast } from '../api'

const base = computed(() => window.location.origin)
const isAdmin = computed(() => Boolean(state.user && state.user.is_admin))
const copied = ref('')

const KEY_PLACEHOLDER = 'sk-agent-xxxxxxxx'

// ---------------------------------------------------------------- 三步上手
const steps = [
  {
    title: '配好发信通道',
    badge: '必做',
    badgeClass: 'badge-warn',
    text: '打开「系统设置 → 邮件通道」，填 SMTP 主机 / 端口 / 认证账号 / 认证密码，并填写默认通知邮箱。按服务商选 SSL（465）或 STARTTLS（587）。先点「测试连接」，看到成功提示后再「保存设置」。',
    hint: '认证密码一般是邮箱服务商生成的「应用专用密码」，不是网页登录密码。默认通知邮箱用于 Agent 省略收件人时的任务邮件，以及后续回复通知。每个账号用各自的通道，互不影响。',
    to: '/settings',
    linkText: '去系统设置',
  },
  {
    title: '建一把给 Agent 用的密钥',
    badge: '必做',
    badgeClass: 'badge-warn',
    text: '打开「API 密钥 → 新建密钥」，勾选权限。让 Agent 发信 + 收回复，给这四项就够：mail:send、mail:read、tasks:write、tasks:read。',
    hint: '密钥原文会在服务端加密保存，之后可回到「API 密钥」列表再次复制同一把 Key。不要把密钥写进前端代码或公开仓库。',
    to: '/keys',
    linkText: '去建密钥',
  },
  {
    title: '给 Agent 三段提示词',
    badge: '核心',
    badgeClass: 'badge-ok',
    text: '任务开始时，把「任务启动提示词」发给负责当前工作的 AI；另外把「定时任务安装提示词」发给一个专用 Codex 对话，让它在当前对话中创建每分钟轮询任务。任务已经开始后，也可以使用「中途接入提示词」。',
    hint: '启动提示词会立即创建任务会话并发送首封创建消息；定时任务安装提示词只负责建立每分钟轮询；中途接入提示词用于把已经进行中的任务接入 Postroom。',
    to: '/docs',
    linkText: '去接口文档',
  },
]

// ---------------------------------------------------------------- 控制台功能地图
const pages = [
  { name: '概览', path: '/', desc: '今日与累计发信量、成功率、SMTP 配置状态、进行中任务数' },
  { name: '发信工作台', path: '/compose', desc: '手动发一封邮件；勾选「创建任务会话」后，正文会自动带「点开即回复」按钮' },
  { name: '任务会话', path: '/tasks', desc: 'Agent ↔ 收件人的往返线程；详情里能看到所属对话，可查看 / 轮换回复链接、关闭或删除任务' },
  { name: '发送记录', path: '/logs', desc: '每封信的状态、耗时与失败原因（含 SMTP 原始错误）' },
  { name: '接口文档 / Agent 工具', path: '/docs', desc: 'Agent 工具清单 + cURL / Python / Node 示例 + 完整错误码' },
  { name: 'API 密钥', path: '/keys', desc: '给 Agent 用的密钥；可启用、停用、删除' },
  { name: '系统设置', path: '/settings', desc: '本账号的 SMTP、默认通知邮箱、对外地址、测试收件人、修改密码' },
  {
    name: '用户管理',
    path: '/users',
    desc: '仅管理员：新建账号、重置密码、停用、删除（删除会级联清掉该账号的数据）',
    adminOnly: true,
  },
]

// ---------------------------------------------------------------- 对话 + 拉取闭环
const flow = [
  { t: 'Agent 开对话', c: 'POST /api/v1/conversations', d: '带上 Agent 侧的对话标识（external_id），同一个标识重复调用只会复用，不会重复建' },
  { t: 'Agent 发邮件', c: 'POST /api/v1/mail/send', d: '带 "conversation_id"（或 external_id）：自动开一条线程，正文追加「点开即回复」按钮；省略 to 时使用默认通知邮箱' },
  { t: 'Agent 不等', c: '—', d: '任务不会被自动关闭，回复链接默认 30 天有效；Agent 可以继续干别的对话' },
  { t: '收件人回帖', c: '打开邮件里的链接', d: '免登录网页对话，链接本身即凭证；隔几天再回也行' },
  { t: '定时任务拉取', c: 'GET /api/v1/inbox', d: 'Codex 定时任务每 1 分钟取走全部对话的增量回复，按 conversation_id 分发回各自对话' },
  { t: '确认水位', c: 'POST /api/v1/inbox/ack', d: '分发成功后推进水位；下次不带 cursor 就从新水位继续，不会重复投递' },
]

const flowNotes = [
  '「拉取」而不是「等待」：服务端没有长轮询接口。Agent 用 /inbox 增量拉取，因此一个进程能同时服务多个对话，用户过多久回复都不会丢。',
  '游标是全局单调递增的 seq，藏在每条消息的 seq 字段里。不传 cursor 时用服务端水位（按 API 密钥记录），进程重启也不会重复投递。',
  '先分发、后 ack：中途崩了最多重复投递一次，不会漏。水位只增不减，传旧值不会回退。',
  '链接可轮换：POST /tasks/{id}/reply-link 让旧链接立即失效；关闭对话或任务后不能再回帖（仍可只读查看）。',
  '「对外地址」（系统设置）决定邮件里链接的域名。填成本机地址，收件人就点不开了。',
  '在「系统设置」填写默认通知邮箱后，Agent 可以省略 to；任务首次发信成功后，后续 post_task_message 会继续发到该任务的收件人。',
  '同一任务回帖限流 60 条/小时；单条消息长度上限由服务端 MESSAGE_MAX_CHARS 控制。',
]

// ---------------------------------------------------------------- 权限与配额
const scopes = [
  { s: 'mail:send', d: '发信、测试 SMTP、查看内置模板' },
  { s: 'mail:read', d: '查看发信记录、发信统计、控制台概览' },
  { s: 'tasks:write', d: '建 / 改 / 关闭 / 删除任务与对话、发消息、轮换回复链接、ack 拉取水位' },
  { s: 'tasks:read', d: '查看任务与对话、拉取收件箱（/inbox）' },
  { s: 'keys:manage', d: '管理本账号的 API 密钥' },
  { s: 'users:manage', d: '仅管理员：管理账号（普通账号无法申请该权限）' },
]

// ---------------------------------------------------------------- 常见报错
const errors = [
  { c: 'missing_credentials', s: '请求没带任何凭证', f: '给请求加 X-API-Key 头；网页端请先登录' },
  { c: 'invalid_api_key', s: '密钥无效、已停用或已删除', f: '到「API 密钥」页确认状态，必要时重建' },
  { c: 'insufficient_scope', s: '密钥没勾选对应权限', f: '按上表补权限，或新建一把更全的密钥' },
  { c: 'smtp_not_configured', s: '本账号与全局都没配 SMTP', f: '「系统设置 → 邮件通道」填好并测试连接' },
  { c: 'smtp_auth_failed', s: 'SMTP 账号或密码错', f: '换用服务商的应用专用密码；确认已开启 SMTP 服务' },
  { c: 'smtp_connect_failed', s: '连不上 SMTP 服务器', f: '检查主机 / 端口与加密方式是否匹配（465 配 SSL，587 配 STARTTLS）' },
  { c: 'recipient_refused', s: '收件人被对方服务器拒收', f: '核对收件地址；发件域名可能需要配置 SPF / DKIM' },
  { c: 'rate_limit_exceeded', s: '超出每小时发信配额', f: '等下一个时间窗口，或调整服务端配额' },
  { c: 'token_expired', s: '回复链接已过期', f: '让 Agent 重新发一封带新链接的邮件' },
  { c: 'token_revoked', s: '链接已被轮换吊销', f: '用最新那封邮件里的链接' },
  { c: 'task_closed', s: '任务已关闭', f: 'Agent 调 /tasks/{id}/reopen 重新打开' },
  { c: 'conversation_not_found', s: '对话不存在，或不属于当前账号', f: '先用 POST /conversations 幂等 ensure 一次再引用；跨账号的对话一律 404' },
]

// ---------------------------------------------------------------- 验收清单
const checks = [
  '系统设置里点「测试连接」返回成功',
  '发信工作台给自己发一封，发送记录里状态为成功',
  '再发一封并勾选「创建任务会话」，邮件正文里有「点开即回复」按钮',
  '点邮件里的按钮能打开对话页，回一句话后「任务会话」页能看到它',
  '用 Agent 密钥调一次 GET /api/v1/inbox，能在 items 里看到刚才那句回复（说明定时拉取通了）',
  '调 POST /api/v1/inbox/ack 传回 next_cursor，再拉一次 items 为空且 watermark 不变',
  '「概览」页的今日发信量、成功率随之变化',
]

// ---------------------------------------------------------------- Agent 提示词（启动任务 / 定时拉取 / 中途接入分开）
const startupPrompt = computed(() => `你是通过 Postroom 与人协作的 AI Agent。下面是“任务启动时”的规则；只负责当前任务的开局与推进，不要在本次任务里阻塞等待用户回复。

服务地址  ${base.value}
鉴权      每个请求都要带请求头  X-API-Key: ${KEY_PLACEHOLDER}

【第一步：发现工具】不要凭记忆猜字段，先调用：
  GET ${base.value}/api/v1/agent/tools
  返回的是 OpenAI function-calling 格式的 tools；按每项的 method + endpoint + parameters 组装请求。

【任务开始】
  1. 为当前外部对象选择稳定的 external_id（不要使用随机值）。
  2. 幂等创建或复用对话，同一个 external_id 重复调用不会重复创建：
     POST /api/v1/conversations
     {"external_id": "codex:<你的会话 id>", "title": "本次任务名称", "agent_name": "你的名字"}
     → 记下返回的 conversation_id
  3. 对话创建或复用后，立即在这个对话下发送一条“任务已开始”的创建消息；即使当前还没有需要用户确认的事项，也不要等到后面才发第一封邮件：
     POST /api/v1/mail/send
     {"conversation_id": "<上一步的 id>", "thread_title": "本次任务名称", "subject": "任务已开始：<本次任务名称>", "body": "任务已开始执行。当前进度：<简要说明>。后续如有需要确认、补充信息或重要进展，我会继续在这个任务会话中通知；当前无需等待你的回复。", "idempotency_key": "<稳定且唯一的初始化发信幂等键>"}
     → 如果没有在请求中提供 to，服务端会使用网页「系统设置」里的默认通知邮箱
     → 邮件正文会自动追加「点开即回复」按钮，不要自己拼 reply_url
  4. 发完初始化邮件后继续执行主任务，不要长轮询、不要等待用户。用户回复由另一段“定时任务安装提示词”创建的每分钟定时任务处理。
  5. 需要主动补充消息或通知对方时：
     POST /api/v1/tasks/<task_id>/messages
     {"content": "要回复的话"}
     → 默认沿用该任务首次发信的收件人；也可以传 notify_email 设置并更新该任务的通知地址
  6. 任务完成后收尾：
     POST /api/v1/conversations/<conversation_id>/close
     或 POST /api/v1/tasks/<task_id>/close

【重试机制】
  - 网络错误、超时、HTTP 408 / 429 / 500 / 502 / 503 / 504：最多重试 3 次，等待时间依次为 2 秒、5 秒、10 秒；429 优先遵守响应中的 Retry-After。
  - 调用 POST /api/v1/mail/send 重试时，必须复用同一个稳定的 idempotency_key，避免邮件已经发出但响应丢失时重复发信。
  - POST /api/v1/conversations 可安全重试，因为 external_id 是幂等的；不要因为重试而生成新的 external_id。
  - 401 / 403、400 / 404 / 422、smtp_auth_failed、recipient_refused 等凭证、参数或收件人错误不要重试，先修正问题。
  - POST /api/v1/tasks/<task_id>/messages 如果遇到网络超时，先读取任务消息或邮件记录确认是否已经成功，不要盲目重复提交，以免重复写入消息。
  - 连续重试仍失败时，停止本次操作，报告 error.code、error.message 和 request_id，不要无限重试。

【响应契约】
  所有接口都返回 {"ok": bool, "data": ..., "error": {"code", "message"}, "request_id": "..."}
  失败时读 error.code 再决定怎么办：
    401 / 403  凭证或权限问题，不要重试，先修配置
    429        超出配额，等下一个窗口
    502 且 code 为 smtp_connect_failed   可重试
    502 且 code 为 smtp_auth_failed      先修配置，重试无用
  排查不确定的错误时，带上 request_id 找管理员查日志。

【注意】
  回复链接是敏感凭证，不要转发到公开渠道；需要作废时调用 POST /api/v1/tasks/<id>/reply-link 轮换。
  不要把 API Key 或 reply_url 写入公开日志、代码仓库或发给无关人员。`)

const midTaskPrompt = computed(() => `这个任务已经进行到一半。请不要重置、暂停或另开一套并行任务；从现在开始把当前 Codex 对话接入 Postroom，让我可以通过邮件网页回复与你沟通。

服务地址  ${base.value}
鉴权      每个请求都要带请求头  X-API-Key: ${KEY_PLACEHOLDER}

【立即执行一次】
  1. 先调用 GET ${base.value}/api/v1/agent/tools，按返回的工具定义组装请求，不要凭记忆猜字段。
  2. 为当前 Codex 对话选择稳定的 external_id：优先使用当前 Codex task / thread id；如果当前环境不直接显示，就使用“项目标识 + 当前任务的稳定名称”组成，并在后续始终复用同一个值，禁止使用随机值。
  3. 幂等创建或复用 Postroom 对话：
     POST /api/v1/conversations
     {"external_id": "codex:<当前对话的稳定标识>", "title": "<当前任务名称>", "agent_name": "你的名字"}
  4. 不要等到下一次需要通知时才创建任务会话。现在就发送一封“中途接入”消息，让 Postroom 立即建立任务会话并保存当前收件人：
     POST /api/v1/mail/send
     {"conversation_id": "<上一步的 conversation_id>", "thread_title": "<当前任务名称>", "subject": "已接入 Postroom：<当前任务名称>", "body": "任务正在进行中，已从当前进度接入 Postroom。当前进度：<简要说明>。后续重要进展、需要确认的事项和最终结果会继续在这个任务会话中通知。", "idempotency_key": "<稳定且唯一的中途接入发信幂等键>"}
     → 如果没有提供 to，服务端会使用网页「系统设置」里的默认通知邮箱
     → 邮件正文会自动追加「点开即回复」按钮，不要自己拼 reply_url
  5. 记录返回的 task_id 和 conversation_id，并向用户说明已接入成功；如果初始化发送失败，按下面的重试规则处理，不要假装成功。

【接入后规则】
  - 继续执行当前任务，不要阻塞等待用户回复，不要在任务内部创建定时器或长轮询。
  - 后续需要让用户知道进展、请求确认或发送最终结果时，调用：
    POST /api/v1/tasks/<task_id>/messages
    {"content": "要通知用户的内容"}
    默认会发到初始化邮件使用的收件人；只有需要更换收件人时才传 notify_email。
  - 用户的回复由另一个 Codex 对话中的每分钟定时任务通过 GET /api/v1/inbox 拉取，再按 conversation_id 分发回当前对话；不要在这里自己 ack 或重复拉取。

【重试与安全】
  - 网络错误、超时、HTTP 408 / 429 / 500 / 502 / 503 / 504：最多重试 3 次，等待 2 秒、5 秒、10 秒；429 优先遵守 Retry-After。
  - 重试 POST /api/v1/mail/send 时必须复用同一个 idempotency_key；external_id 也必须保持不变。
  - 401 / 403、400 / 404 / 422、smtp_auth_failed、recipient_refused 不要重试，先报告并修正配置。
  - 如果 POST /api/v1/tasks/<task_id>/messages 超时，先检查任务消息或邮件记录再决定是否重试，避免重复通知。
  - 连续失败后报告 error.code、error.message 和 request_id，不要伪造已接入成功。

【响应契约】
  所有接口都返回 {"ok": bool, "data": ..., "error": {"code", "message"}, "request_id": "..."}。
  不要把 API Key 或 reply_url 写入公开日志、代码仓库或发给无关人员。`)

const pollingPrompt = computed(() => `请把 Postroom 的收件箱轮询设置成当前 Codex 对话中的定时任务。

【只执行一次的设置动作】
  请在当前这个 Codex 对话中完成以下设置，不要新建独立对话，也不要另开聊天：
  1. 先把当前对话重命名为：Postroom 消息接收 · 每分钟轮询
  2. 在当前对话中创建一个每 1 分钟执行一次的定时任务，并让每次运行都返回当前这个对话。
  3. 创建定时任务时，只把下面【定时任务执行内容】部分作为定时任务的执行提示词；不要把前面的“设置动作”再次放进定时任务，避免每分钟重复创建任务或重复改名。
  4. 创建完成后，告诉用户定时任务的名称、执行频率和绑定的对话。如果无法创建，必须明确说明原因，不要假装创建成功。

服务地址  ${base.value}
鉴权      每个请求都要带请求头  X-API-Key: ${KEY_PLACEHOLDER}

【定时任务执行内容】
  你是 Postroom 的定时收件箱 Agent。每次被这个定时任务唤醒时，只执行一轮拉取、分发和确认水位；不要创建任务、不要发送首封邮件、不要在任务内部再次创建定时器，也不要长轮询等待。

  1. 先调用 GET ${base.value}/api/v1/agent/tools，必要时刷新工具定义；不要凭记忆猜参数。
  2. 调用 GET /api/v1/inbox。不传 cursor，服务端会按这把 API Key 保存的水位返回增量消息。
  3. 如果 items[] 为空，直接安静结束本轮，不要伪造消息，也不必 ack。
  4. 如果有消息，按 seq 从小到大处理；使用 conversation_id（以及 conversation_external_id）把每条 content / author / task_id 分发回对应的 AI 对话。
  5. 只有当本轮所有连续消息都已经成功分发后，才调用：
     POST /api/v1/inbox/ack
     {"upto_seq": <本次响应的 next_cursor>}
     ack 的水位只增不减，重复提交安全。
  6. 有消息并完成处理后，只汇报必要结果；没有新消息时不要向用户发送无意义的状态消息。

【失败处理】
  - 分发中途失败：不要 ack 到失败消息之后；保留未确认消息，下一分钟再次重试。
  - 401 / 403：停止调用并报告鉴权或权限问题，不要继续重试。
  - 429：读取 Retry-After（如果有）；不要在本轮密集重试，交给下一次定时运行。
  - 5xx 或网络错误：保留水位，结束本轮，交给下一次 1 分钟定时任务重试。
  - 不要为了“清空收件箱”直接 ack 一个没有成功处理的 next_cursor；系统采用至少一次投递，宁可重复，不能漏消息。

【响应契约】
  所有接口都返回 {"ok": bool, "data": ..., "error": {"code", "message"}, "request_id": "..."}。
  排查不确定的错误时，带上 request_id 找管理员查日志。`)

async function copy(text, tag) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = tag
    const labels = {
      startup: '已复制任务启动提示词',
      polling: '已复制定时任务安装提示词',
      midTask: '已复制中途接入提示词',
    }
    toast(labels[tag] || '已复制提示词')
    setTimeout(() => (copied.value = ''), 1600)
  } catch (e) {
    toast('复制失败，请手动选中复制', 'warn')
  }
}
</script>

<template>
  <div class="grid tut-page">
    <!-- 顶部引导 -->
    <div class="card tut-hero">
      <div>
        <div class="tut-hero-title">使用教程</div>
        <div class="tut-hero-sub">
          按顺序走一遍大约 5 分钟：配好发信通道 → 建一把 Agent 密钥 → 把接口交给 Agent →
          用定时任务从 /inbox 拉回复。任务中途也可以随时接入，用户什么时候回都行，Agent 不用等。
        </div>
        <div class="tut-hero-chips">
          <span class="chip">1 · 配 SMTP</span>
          <span class="chip">2 · 建密钥</span>
          <span class="chip">3 · 接入 Agent</span>
          <span class="chip">4 · 每个对话独立发信</span>
          <span class="chip">5 · 每分钟拉取回复</span>
        </div>
      </div>
      <div class="tut-hero-actions">
        <router-link class="btn btn-primary" to="/settings">去配 SMTP</router-link>
        <router-link class="btn" to="/keys">建 API 密钥</router-link>
      </div>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1fr) minmax(310px, 0.82fr)">
      <div>
        <!-- 三步上手 -->
        <div class="card">
          <div class="card-head">
            <div class="card-title">三步上手</div>
            <div class="card-desc">刚拿到账号时按这个顺序做</div>
          </div>
          <div class="card-body">
            <div class="steps">
              <div v-for="(st, i) in steps" :key="st.title" class="step">
                <div class="step-num">{{ i + 1 }}</div>
                <div class="step-body">
                  <div class="step-title">
                    {{ st.title }}
                    <span class="badge" :class="st.badgeClass">{{ st.badge }}</span>
                  </div>
                  <div class="step-text">{{ st.text }}</div>
                  <div class="step-hint">{{ st.hint }}</div>
                  <div style="margin-top: 9px">
                    <router-link class="btn btn-sm" :to="st.to">{{ st.linkText }} →</router-link>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 控制台功能地图 -->
        <div class="card">
          <div class="card-head">
            <div class="card-title">控制台功能地图</div>
            <div class="card-desc">点左侧导航就能到</div>
          </div>
          <table>
            <thead>
              <tr>
                <th style="width: 168px">页面</th>
                <th>用途</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in pages.filter((x) => !x.adminOnly || isAdmin)" :key="p.name">
                <td>
                  <router-link :to="p.path">{{ p.name }}</router-link>
                  <div v-if="p.adminOnly" class="badge badge-mute" style="margin-top: 4px">仅管理员</div>
                </td>
                <td class="small">{{ p.desc }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 对话 + 拉取闭环 -->
        <div class="card">
          <div class="card-head">
            <div class="card-title">回复闭环：定时任务拉取，Agent 不用等</div>
            <div class="card-desc">每个对话独立发信，回复由 cron 从 /inbox 取走后分发回对应对话</div>
          </div>
          <div class="card-body">
            <div class="steps">
              <div v-for="(f, i) in flow" :key="f.t" class="step">
                <div class="step-num">{{ i + 1 }}</div>
                <div class="step-body">
                  <div class="step-title">{{ f.t }}</div>
                  <div class="mono small" style="color: var(--primary-strong)">{{ f.c }}</div>
                  <div class="step-text" style="margin-top: 3px">{{ f.d }}</div>
                </div>
              </div>
            </div>
            <div class="banner banner-warn" style="margin-top: 4px; display: block">
              <div v-for="n in flowNotes" :key="n" class="small" style="margin: 2px 0">· {{ n }}</div>
            </div>
          </div>
        </div>
      </div>

      <div>
        <!-- Agent 提示词 -->
        <div class="card">
          <div class="card-head">
            <div class="card-title">Agent 提示词</div>
            <div class="card-desc">按使用场景分别复制</div>
          </div>
          <div class="card-body">
            <div class="small muted" style="margin-bottom: 8px">
              把 <span class="mono">{{ KEY_PLACEHOLDER }}</span> 换成真实密钥。新任务使用①；定时接收任务使用②；任务已经开始后使用③。
            </div>
            <div class="step-title" style="margin-top: 12px">
              ① 任务启动提示词
              <button class="btn btn-sm btn-primary" style="float: right" @click="copy(startupPrompt, 'startup')">
                {{ copied === 'startup' ? '已复制' : '复制' }}
              </button>
            </div>
            <div class="small muted" style="margin: 5px 0 8px">
              每个新任务 / 新对话开始时使用；会立即创建 Postroom 任务会话并发送首封创建消息，然后继续主任务。
            </div>
            <pre class="code tut-prompt">{{ startupPrompt }}</pre>

            <div class="step-title" style="margin-top: 16px">
              ② 定时任务安装提示词
              <button class="btn btn-sm btn-primary" style="float: right" @click="copy(pollingPrompt, 'polling')">
                {{ copied === 'polling' ? '已复制' : '复制' }}
              </button>
            </div>
            <div class="small muted" style="margin: 5px 0 8px">
              发给专用 Codex 对话一次；它会在当前对话中创建每 1 分钟执行的定时任务，之后只负责拉取、分发和 ack。
            </div>
            <pre class="code tut-prompt">{{ pollingPrompt }}</pre>

            <div class="step-title" style="margin-top: 16px">
              ③ 任务中途接入提示词
              <button class="btn btn-sm btn-primary" style="float: right" @click="copy(midTaskPrompt, 'midTask')">
                {{ copied === 'midTask' ? '已复制' : '复制' }}
              </button>
            </div>
            <div class="small muted" style="margin: 5px 0 8px">
              任务已经执行一段时间、还没有接入 Postroom 时使用；会立即发送中途接入消息并沿用当前任务继续工作。
            </div>
            <pre class="code tut-prompt">{{ midTaskPrompt }}</pre>
          </div>
        </div>

        <!-- 权限与配额 -->
        <div class="card">
          <div class="card-head">
            <div class="card-title">权限（scopes）</div>
            <div class="card-desc">建密钥时按需勾选</div>
          </div>
          <div class="card-body">
            <table>
              <tbody>
                <tr v-for="s in scopes" :key="s.s">
                  <td class="mono small" style="width: 116px">{{ s.s }}</td>
                  <td class="small">{{ s.d }}</td>
                </tr>
              </tbody>
            </table>
            <div class="step-hint" style="margin-top: 10px">
              发信、登录、回帖都有每小时的限额，超出返回 429（<span class="mono">rate_limit_exceeded</span>）——
              具体数值由部署方在服务端配置，需要调整请联系管理员。
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 常见报错 -->
    <div class="card">
      <div class="card-head">
        <div class="card-title">常见报错怎么查</div>
        <div class="card-desc">完整错误码见「接口文档」页</div>
      </div>
      <table>
        <thead>
          <tr>
            <th style="width: 210px">error.code</th>
            <th style="width: 250px">什么情况</th>
            <th>怎么办</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in errors" :key="e.c">
            <td class="mono small">{{ e.c }}</td>
            <td class="small">{{ e.s }}</td>
            <td class="small">{{ e.f }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 验收清单 -->
    <div class="card">
      <div class="card-head">
        <div class="card-title">验收清单</div>
        <div class="card-desc">五条都过，说明整条链路是通的</div>
      </div>
      <div class="card-body">
        <ul class="tut-checks">
          <li v-for="c in checks" :key="c">
            <span class="tut-check-mark">✓</span><span>{{ c }}</span>
          </li>
        </ul>
        <div class="banner banner-info" style="margin-top: 6px">
          <div class="small">
            多账号说明：每个账号的 SMTP 配置、API 密钥、发送记录、任务会话都是<strong>互相隔离</strong>的，
            看不到也改不了别人的数据。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
