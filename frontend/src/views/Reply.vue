<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { fmtClock, fmtTime, replyApi, state, timeAgo, toast } from '../api'
import SiteFooter from '../components/SiteFooter.vue'

const route = useRoute()
const token = computed(() => route.params.token)

const session = ref(null)
const messages = ref([])
const loading = ref(true)
const fatal = ref(null) // {code, message}
const sending = ref(false)
const draft = ref('')
const author = ref(localStorage.getItem('agent_reply_author') || '')
const serverTime = ref('')
const threadEl = ref(null)
const lastSeenId = ref(null)

let alive = true
let polling = false

const canReply = computed(() => session.value?.can_reply !== false)
const visibleMessages = computed(() => messages.value)

function scrollToBottom(smooth = true) {
  nextTick(() => {
    const el = threadEl.value
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  })
}

function mergeMessages(incoming) {
  const known = new Set(messages.value.map((m) => m.id))
  let added = 0
  for (const m of incoming) {
    if (!known.has(m.id)) {
      messages.value.push(m)
      added += 1
    }
  }
  if (added) {
    messages.value.sort((a, b) => (a.created_at < b.created_at ? -1 : 1))
    lastSeenId.value = messages.value[messages.value.length - 1].id
  }
  return added
}

async function open() {
  loading.value = true
  fatal.value = null
  try {
    const data = await replyApi.open(token.value)
    session.value = data.session
    messages.value = data.messages || []
    serverTime.value = data.server_time
    lastSeenId.value = messages.value.length ? messages.value[messages.value.length - 1].id : null
    scrollToBottom(false)
    startPolling()
  } catch (e) {
    fatal.value = { code: e.code, message: e.message, detail: e.detail }
  } finally {
    loading.value = false
  }
}

/** 定时增量刷新：服务端已无长轮询，页面自己按固定间隔拉 Agent 的新消息。 */
async function startPolling() {
  if (polling) return
  polling = true
  while (alive && !fatal.value) {
    // 页面不可见时降频，避免后台标签页空转
    let delay = document.hidden ? 15000 : 5000
    try {
      const data = await replyApi.poll(token.value, lastSeenId.value)
      serverTime.value = data.server_time
      if (data.session) session.value = data.session
      if (data.messages?.length) {
        mergeMessages(data.messages)
        scrollToBottom()
      }
    } catch (e) {
      // 链接失效/任务关闭等：停止轮询并提示
      if (['token_revoked', 'token_expired', 'invalid_signature', 'task_not_found'].includes(e.code)) {
        fatal.value = { code: e.code, message: e.message }
        break
      }
      delay = 3000
    }
    if (!alive || fatal.value) break
    await new Promise((r) => setTimeout(r, delay))
  }
  polling = false
}

async function send() {
  const content = draft.value.trim()
  if (!content || sending.value) return
  if (!canReply.value) {
    toast('该任务已关闭，无法回复', 'warn')
    return
  }
  sending.value = true
  localStorage.setItem('agent_reply_author', author.value || '')
  try {
    const data = await replyApi.send(token.value, content, author.value)
    mergeMessages([data.message])
    if (data.session) session.value = data.session
    draft.value = ''
    scrollToBottom()
    toast('已发送')
  } catch (e) {
    toast(e.message, 'error', 6000)
  } finally {
    sending.value = false
  }
}

async function copyLink() {
  try {
    await navigator.clipboard.writeText(window.location.href)
    toast('已复制当前回复链接')
  } catch (e) {
    toast('复制失败，请手动复制地址栏', 'warn')
  }
}

function onKeydown(e) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') send()
}

onMounted(open)
onUnmounted(() => {
  alive = false
})
</script>

<template>
  <div class="reply-page">
    <header class="reply-head">
      <div class="reply-head-inner">
        <div class="reply-brand"><span class="brand-logo">P</span></div>
        <div style="min-width: 0">
          <div class="reply-title">{{ session?.title || '与 Agent 的对话' }}</div>
          <div class="reply-sub">
            <span v-if="session">
              {{ session.agent_name }} ·
              <span class="badge" :class="session.status === 'open' ? 'badge-ok' : 'badge-mute'">
                {{ session.status === 'open' ? '进行中' : '已结束' }}
              </span>
              <span class="muted" style="margin-left: 6px">共 {{ session.message_count }} 条消息</span>
            </span>
            <span v-else class="muted">正在校验链接…</span>
          </div>
        </div>
        <div class="spacer" />
        <div class="reply-actions">
          <span v-if="session" class="small muted d-none-sm">
            链接有效期至 {{ session.link_expires_at?.slice(0, 10) }}
          </span>
          <button v-if="session" class="btn btn-sm" @click="copyLink">复制链接</button>
        </div>
      </div>
    </header>

    <div v-if="fatal" class="reply-body">
      <div class="reply-card">
        <div class="reply-fatal">
          <div class="reply-fatal-icon">🔗</div>
          <h2>链接无法使用</h2>
          <p>{{ fatal.message }}</p>
          <p class="small muted">
            错误码：<span class="mono">{{ fatal.code }}</span>
            <template v-if="fatal.code === 'token_revoked'"> · 该链接已被轮换，请使用最新邮件里的链接</template>
            <template v-if="fatal.code === 'token_expired'"> · 有效期已过，请让 Agent 重新发送</template>
          </p>
        </div>
      </div>
    </div>

    <div v-else class="reply-body">
      <div ref="threadEl" class="reply-thread">
        <div v-if="loading" class="empty">加载中…</div>

        <template v-for="m in visibleMessages" :key="m.id">
          <div v-if="m.role === 'system'" class="thread-system">
            <span>{{ m.content }} · {{ fmtTime(m.created_at) }}</span>
          </div>
          <div v-else class="thread-row" :class="m.role === 'user' ? 'me' : 'agent'">
            <div class="thread-avatar" :class="m.role === 'user' ? 'user' : 'agent'">
              {{ m.role === 'user' ? '我' : 'AI' }}
            </div>
            <div class="thread-main">
              <div class="thread-meta">
                <b>{{ m.role === 'user' ? (m.author || '我') : (m.author || session?.agent_name || 'Agent') }}</b>
                <span class="muted">{{ fmtClock(m.created_at) }}</span>
                <span v-if="m.source === 'email'" class="badge badge-mute">来自邮件</span>
              </div>
              <div class="bubble" :class="m.role === 'user' ? 'bubble-me' : 'bubble-agent'">{{ m.content }}</div>
            </div>
          </div>
        </template>

        <div v-if="!sending" class="thread-auto-refresh">
          页面每几秒自动刷新一次；Agent 收到你的回复后，新消息会自动出现在这里
        </div>
      </div>

      <div class="reply-composer">
        <div class="composer-row">
          <input v-model="author" type="text" placeholder="你的称呼（可选）" class="composer-name" />
          <span class="small muted">有人回复时这里会自动刷新 · 服务器时间 {{ fmtClock(serverTime) }}</span>
        </div>
        <textarea
          v-model="draft"
          rows="3"
          :placeholder="canReply ? '回复 Agent…（Ctrl / ⌘ + Enter 发送）' : '该任务已结束，无法回复'"
          :disabled="!canReply"
          @keydown="onKeydown"
        />
        <div class="composer-foot">
          <span class="small muted">{{ draft.length }} / 4000</span>
          <div class="spacer" />
          <button class="btn btn-primary" :disabled="!canReply || sending || !draft.trim()" @click="send">
            {{ sending ? '发送中…' : '发送回复' }}
          </button>
        </div>
        <div v-if="!canReply" class="banner banner-warn" style="margin: 10px 0 0">
          该任务已结束，链接仅供查看历史对话。
        </div>
      </div>
    </div>

    <SiteFooter />

    <div class="toasts">
      <div v-for="t in state.toasts" :key="t.id" class="toast" :class="t.type">{{ t.message }}</div>
    </div>
  </div>
</template>
