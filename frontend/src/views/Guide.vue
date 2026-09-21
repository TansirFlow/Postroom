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
    text: '打开「系统设置 → 邮件通道」，填 SMTP 主机 / 端口 / 认证账号 / 认证密码，按服务商选 SSL（465）或 STARTTLS（587）。先点「测试连接」，看到成功提示后再「保存设置」。',
    hint: '认证密码一般是邮箱服务商生成的「应用专用密码」，不是网页登录密码。没配好之前，所有发信都会直接返回 smtp_not_configured。每个账号用各自的通道，互不影响。',
    to: '/settings',
    linkText: '去系统设置',
  },
  {
    title: '建一把给 Agent 用的密钥',
    badge: '必做',
    badgeClass: 'badge-warn',
    text: '打开「API 密钥 → 新建密钥」，勾选权限。让 Agent 发信 + 收回复，给这四项就够：mail:send、mail:read、tasks:write、tasks:read。',
    hint: '明文密钥只在创建那一次显示，之后只剩前缀；没存下来只能删掉重建。不要把密钥写进前端代码或公开仓库。',
    to: '/keys',
    linkText: '去建密钥',
  },
  {
    title: '把接口交给 Agent',
    badge: '核心',
    badgeClass: 'badge-ok',
    text: '复制右侧「接入提示词」，连同密钥一起发给你的 Agent / LLM。它会先 GET /api/v1/agent/tools 拿到工具清单，再按清单里的 method + endpoint + parameters 组装请求，不需要你手写接口。',
    hint: '工具清单是 OpenAI function-calling 格式，可以直接当作模型的 tools 参数使用。回复是「拉取式」的：Agent 用 GET /api/v1/inbox 增量取，不用等、不用长轮询。完整接口列表见「接口文档」。',
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
  { name: '系统设置', path: '/settings', desc: '本账号的 SMTP、对外地址（决定回复链接域名）、测试收件人、修改密码' },
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
  { t: 'Agent 发邮件', c: 'POST /api/v1/mail/send', d: '带 "conversation_id"（或 external_id）：自动开一条线程，正文追加「点开即回复」按钮' },
  { t: 'Agent 不等', c: '—', d: '任务不会被自动关闭，回复链接默认 30 天有效；Agent 可以继续干别的对话' },
  { t: '收件人回帖', c: '打开邮件里的链接', d: '免登录网页对话，链接本身即凭证；隔几天再回也行' },
  { t: '定时任务拉取', c: 'GET /api/v1/inbox', d: 'cron 每 30 秒一次取走全部对话的增量回复，按 conversation_id 分发回各自对话' },
  { t: '确认水位', c: 'POST /api/v1/inbox/ack', d: '分发成功后推进水位；下次不带 cursor 就从新水位继续，不会重复投递' },
]

const flowNotes = [
  '「拉取」而不是「等待」：服务端没有长轮询接口。Agent 用 /inbox 增量拉取，因此一个进程能同时服务多个对话，用户过多久回复都不会丢。',
  '游标是全局单调递增的 seq，藏在每条消息的 seq 字段里。不传 cursor 时用服务端水位（按 API 密钥记录），进程重启也不会重复投递。',
  '先分发、后 ack：中途崩了最多重复投递一次，不会漏。水位只增不减，传旧值不会回退。',
  '链接可轮换：POST /tasks/{id}/reply-link 让旧链接立即失效；关闭对话或任务后不能再回帖（仍可只读查看）。',
  '「对外地址」（系统设置）决定邮件里链接的域名。填成本机地址，收件人就点不开了。',
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

// ---------------------------------------------------------------- 接入提示词
const prompt = computed(() => `你是通过 Postroom 发邮件的助手。请遵守下面的约定。

服务地址  ${base.value}
鉴权      每个请求都要带请求头  X-API-Key: ${KEY_PLACEHOLDER}

【第一步】先拿到工具清单，不要凭记忆猜字段：
  GET ${base.value}/api/v1/agent/tools
  返回的是 OpenAI function-calling 格式的 tools，按其中每项的 method + endpoint + parameters 组装请求。

【发一封邮件】
  POST /api/v1/mail/send
  {"to": ["someone@example.com"], "subject": "标题", "body": "正文"}

【需要对方回复时（对话 + 定时拉取）】
  A. 每进一个新对话，先幂等拿一个会话 id（同一个 external_id 只会复用，不会重复建）：
     POST /api/v1/conversations
     {"external_id": "codex:<你的会话 id>", "title": "本次任务名称", "agent_name": "你的名字"}
     → 记下返回的 conversation_id

  B. 发信：带上会话 id（也可以只给 external_id，服务端会自动 ensure）
     POST /api/v1/mail/send
     {"conversation_id": "<上一步的 id>", "thread_title": "标题", "to": [...], "subject": "...", "body": "..."}
     → 邮件正文会自动追加「点开即回复」按钮，不用自己拼链接

  C. 发完就走，不要等。任务不会被自动关闭，回复链接默认 30 天有效。

  D. 由定时任务（cron，建议每 30 秒）统一取回复，一次拿到全部对话的增量：
     GET /api/v1/inbox            ← 不传 cursor 就用服务端水位，进程重启也不会重复投递
     → 返回 items[]，每条带 conversation_id / external_id / task_id / content / seq
     → 按 conversation_id 把消息送回对应的对话即可

  E. 分发成功后确认水位（只增不减，重复提交安全）：
     POST /api/v1/inbox/ack
     {"upto_seq": <上一步返回的 next_cursor>}

  F. 需要主动加一句 / 通知对方：
     POST /api/v1/tasks/<task_id>/messages
     {"content": "要回复的话", "notify_email": ["someone@example.com"]}

  G. 收尾：关闭对话或单条任务，回复链接立即失效
     POST /api/v1/conversations/<conversation_id>/close
     POST /api/v1/tasks/<task_id>/close

【响应契约】
  所有接口都返回 {"ok": bool, "data": ..., "error": {"code", "message"}, "request_id": "..."}
  失败时读 error.code 再决定怎么办：
    401 / 403  凭证或权限问题，不要重试，先修配置
    429        超出配额，等下一个窗口
    502 且 code 为 smtp_connect_failed   可重试
    502 且 code 为 smtp_auth_failed      先修配置，重试无用
  排查不确定的错误时，带上 request_id 找管理员查日志。

【注意】
  回复链接是敏感凭证，不要转发到公开渠道；需要作废时调 /tasks/<id>/reply-link 轮换。`)

async function copy(text, tag) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = tag
    toast(tag === 'prompt' ? '已复制接入提示词' : '已复制')
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
          用定时任务从 /inbox 拉回复。用户什么时候回都行，Agent 不用等。
        </div>
        <div class="tut-hero-chips">
          <span class="chip">1 · 配 SMTP</span>
          <span class="chip">2 · 建密钥</span>
          <span class="chip">3 · 接入 Agent</span>
          <span class="chip">4 · 每个对话独立发信</span>
          <span class="chip">5 · cron 拉取回复</span>
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
        <!-- 接入提示词 -->
        <div class="card">
          <div class="card-head">
            <div class="card-title">接入提示词</div>
            <div class="card-desc">复制给 Agent 即可</div>
            <div class="spacer" />
            <button class="btn btn-sm btn-primary" @click="copy(prompt, 'prompt')">
              {{ copied === 'prompt' ? '已复制' : '复制' }}
            </button>
          </div>
          <div class="card-body">
            <div class="small muted" style="margin-bottom: 8px">
              把 <span class="mono">{{ KEY_PLACEHOLDER }}</span> 换成你刚建的真实密钥，再发给 Agent / LLM。
            </div>
            <pre class="code tut-prompt">{{ prompt }}</pre>
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
