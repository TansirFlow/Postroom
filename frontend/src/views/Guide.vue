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
    hint: '工具清单是 OpenAI function-calling 格式，可以直接当作模型的 tools 参数使用。完整接口列表见「接口文档」。',
    to: '/docs',
    linkText: '去接口文档',
  },
]

// ---------------------------------------------------------------- 控制台功能地图
const pages = [
  { name: '概览', path: '/', desc: '今日与累计发信量、成功率、SMTP 配置状态、进行中任务数' },
  { name: '发信工作台', path: '/compose', desc: '手动发一封邮件；勾选「创建任务会话」后，正文会自动带「点开即回复」按钮' },
  { name: '任务会话', path: '/tasks', desc: 'Agent ↔ 收件人的往返对话；可查看 / 轮换回复链接、关闭或删除任务' },
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

// ---------------------------------------------------------------- 任务闭环
const flow = [
  { t: 'Agent 建任务', c: 'POST /api/v1/tasks', d: '拿到 task_id 与 reply_url' },
  { t: 'Agent 发邮件', c: 'POST /api/v1/mail/send', d: '带上 "task_id"，正文自动追加「点开即回复」按钮' },
  { t: '收件人回帖', c: '打开邮件里的链接', d: '免登录网页对话，链接本身即凭证' },
  { t: 'Agent 取回', c: 'GET /api/v1/tasks/{id}/messages', d: 'wait_seconds 长轮询，最长阻塞 60 秒，不用空转' },
]

const flowNotes = [
  '链接可轮换：POST /tasks/{id}/reply-link 让旧链接立即失效；任务关闭后不能再回帖（仍可只读查看）。',
  '「对外地址」（系统设置）决定邮件里链接的域名。填成本机地址，收件人就点不开了。',
  '同一任务回帖限流 60 条/小时；单条消息长度上限由服务端 MESSAGE_MAX_CHARS 控制。',
]

// ---------------------------------------------------------------- 权限与配额
const scopes = [
  { s: 'mail:send', d: '发信、测试 SMTP、查看内置模板' },
  { s: 'mail:read', d: '查看发信记录、发信统计、控制台概览' },
  { s: 'tasks:write', d: '建 / 改 / 关闭 / 删除任务、发消息、轮换回复链接' },
  { s: 'tasks:read', d: '查看任务列表与会话内容' },
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
]

// ---------------------------------------------------------------- 验收清单
const checks = [
  '系统设置里点「测试连接」返回成功',
  '发信工作台给自己发一封，发送记录里状态为成功',
  '再发一封并勾选「创建任务会话」，邮件正文里有「点开即回复」按钮',
  '点邮件里的按钮能打开对话页，回一句话后「任务会话」页能看到它',
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

【需要对方回复时（任务闭环）】
  1. POST /api/v1/tasks
     {"title": "本次任务名称", "agent_name": "你的名字"}
     → 记下返回的 task_id
  2. POST /api/v1/mail/send
     带上 {"task_id": "<上一步的 id>", ...}
     → 邮件正文会自动追加「点开即回复」按钮，不用自己拼链接
  3. GET  /api/v1/tasks/<id>/messages?role=user&wait_seconds=55
     → 长轮询等用户回复，最长阻塞 60 秒；没有新消息就再来一次
  4. POST /api/v1/tasks/<id>/messages
     {"content": "要回复的话", "notify_email": ["someone@example.com"]}
     → notify_email 可选：回帖的同时给对方发一封邮件通知
  5. POST /api/v1/tasks/<id>/close
     → 收尾；关闭后回复链接立即失效

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
          按顺序走一遍大约 5 分钟：配好发信通道 → 建一把 Agent 密钥 → 把接口交给 Agent。
        </div>
        <div class="tut-hero-chips">
          <span class="chip">1 · 配 SMTP</span>
          <span class="chip">2 · 建密钥</span>
          <span class="chip">3 · 接入 Agent</span>
          <span class="chip">4 · 跑通任务闭环</span>
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

        <!-- 任务闭环 -->
        <div class="card">
          <div class="card-head">
            <div class="card-title">任务闭环：让 Agent 能「等用户回话」</div>
            <div class="card-desc">本项目的主要用法，普通「发完即走」的邮件也支持</div>
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
