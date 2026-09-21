<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtClock, fmtTime, timeAgo, toast } from '../api'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const pages = ref(1)
const page = ref(1)
const statusFilter = ref('')
const keyword = ref('')
const stats = ref({})

const detail = ref(null)
const detailLoading = ref(false)
const messages = ref([])
const replyLink = ref('')
const linkExpires = ref('')
const busy = ref(false)
const notifyEmail = ref('')
const draft = ref('')

const selectedId = computed(() => route.params.id || null)

const scopesHint = 'tasks:write'

async function loadList() {
  loading.value = true
  try {
    const params = new URLSearchParams({ page: page.value, page_size: 20 })
    if (statusFilter.value) params.set('status', statusFilter.value)
    if (keyword.value.trim()) params.set('q', keyword.value.trim())
    const data = await api.get(`/api/v1/tasks?${params}`)
    rows.value = data.items || []
    total.value = data.total
    pages.value = data.pages
    stats.value = data.stats || {}
    if (!selectedId.value && rows.value.length) select(rows.value[0].id)
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function loadDetail(id) {
  detailLoading.value = true
  try {
    const data = await api.get(`/api/v1/tasks/${id}?include_link=true`)
    detail.value = data.task
    messages.value = data.messages || []
    replyLink.value = data.reply_url || ''
    linkExpires.value = data.reply_expires_at || ''
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    detailLoading.value = false
  }
}

function select(id) {
  router.replace({ name: 'task-detail', params: { id } })
}

async function refresh() {
  await loadList()
  if (selectedId.value) await loadDetail(selectedId.value)
}

async function rotateLink() {
  if (!confirm('轮换后，此前发出的所有回复链接将立即失效，确定继续？')) return
  busy.value = true
  try {
    const data = await api.post(`/api/v1/tasks/${selectedId.value}/reply-link`)
    replyLink.value = data.reply_url
    linkExpires.value = data.reply_expires_at
    toast('已生成新链接，旧链接失效')
    loadList()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}

async function setStatus(status) {
  busy.value = true
  try {
    const path = status === 'closed' ? 'close' : 'reopen'
    await api.post(`/api/v1/tasks/${selectedId.value}/${path}`)
    toast(status === 'closed' ? '任务已关闭' : '任务已重新打开')
    await refresh()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}

async function removeTask() {
  const t = detail.value
  if (!t) return
  if (!confirm(`确定删除任务「${t.title}」及其全部会话消息？\n此操作不可恢复，回复链接会同时失效。`)) return
  busy.value = true
  try {
    await api.del(`/api/v1/tasks/${selectedId.value}`)
    toast('任务已删除')
    selectedId.value = ''
    detail.value = null
    await loadList()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}

async function sendAsAgent() {
  const content = draft.value.trim()
  if (!content) return
  busy.value = true
  try {
    const body = { content }
    if (notifyEmail.value.trim()) {
      body.notify_email = notifyEmail.value.split(/[,;\s]+/).filter(Boolean)
    }
    const data = await api.post(`/api/v1/tasks/${selectedId.value}/messages`, body)
    toast(data.email ? `已发送，并邮件通知 ${data.email.to.length} 个地址` : '已发送')
    draft.value = ''
    await refresh()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}

async function copy(text, label = '已复制') {
  try {
    await navigator.clipboard.writeText(text)
    toast(label)
  } catch (e) {
    toast('复制失败', 'warn')
  }
}

let searchTimer
watch(keyword, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    loadList()
  }, 320)
})
watch(statusFilter, () => {
  page.value = 1
  loadList()
})
watch(page, loadList)
watch(
  () => route.params.id,
  (id) => {
    if (id && id !== detail.value?.id) loadDetail(id)
  },
  { immediate: true }, // 直接访问 / 刷新 /tasks/<id> 时也要展开详情
)

onMounted(loadList)
</script>

<template>
  <div class="tasks-layout">
    <!-- 左：任务列表 -->
    <div class="card tasks-list">
      <div class="card-head">
        <div class="card-title">任务</div>
        <div class="spacer" />
        <button class="btn btn-sm" :disabled="loading" @click="refresh">{{ loading ? '…' : '刷新' }}</button>
      </div>
      <div class="tasks-filters">
        <input v-model="keyword" type="text" placeholder="搜索标题 / 任务 ID / Agent" />
        <select v-model="statusFilter" style="width: 96px">
          <option value="">全部</option>
          <option value="open">进行中</option>
          <option value="closed">已结束</option>
        </select>
      </div>
      <div class="tasks-stats">
        <span class="chip">进行中 {{ stats.open ?? 0 }}</span>
        <span class="chip" :class="{ on: stats.waiting_reply_tasks > 0 }">
          待回复 {{ stats.waiting_reply_tasks ?? 0 }}
        </span>
        <span class="chip">消息 {{ stats.messages ?? 0 }}</span>
      </div>

      <div class="tasks-rows">
        <div
          v-for="row in rows"
          :key="row.id"
          class="task-row"
          :class="{ active: row.id === selectedId }"
          @click="select(row.id)"
        >
          <div class="task-row-top">
            <span class="task-row-title">{{ row.title || '未命名任务' }}</span>
            <span v-if="row.unread_for_agent > 0" class="nav-badge">{{ row.unread_for_agent }}</span>
          </div>
          <div class="task-row-sub">
            <span class="mono">{{ row.id.slice(5, 13) }}</span> ·
            {{ row.agent_name || 'Agent' }} ·
            <span class="badge" :class="row.status === 'open' ? 'badge-ok' : 'badge-mute'">{{ row.status === 'open' ? '进行中' : '已结束' }}</span>
          </div>
          <div class="task-row-sub muted">
            最后消息 {{ timeAgo(row.last_message_at || row.created_at) }} · {{ row.message_count }} 条
          </div>
        </div>
        <div v-if="!rows.length" class="empty">{{ loading ? '加载中…' : '还没有任务' }}</div>
      </div>

      <div class="pager" style="border-top: 1px solid var(--border)">
        <button class="btn btn-sm" :disabled="page <= 1" @click="page--">上一页</button>
        <span>{{ page }} / {{ pages }} · 共 {{ total }}</span>
        <button class="btn btn-sm" :disabled="page >= pages" @click="page++">下一页</button>
      </div>
    </div>

    <!-- 右：详情 -->
    <div v-if="detail" class="card task-detail">
      <div class="card-head">
        <div class="card-title">{{ detail.title || '未命名任务' }}</div>
        <span class="badge" :class="detail.status === 'open' ? 'badge-ok' : 'badge-mute'">
          {{ detail.status === 'open' ? '进行中' : '已结束' }}
        </span>
        <div class="spacer" />
        <button class="btn btn-sm" :disabled="busy" @click="setStatus(detail.status === 'open' ? 'closed' : 'open')">
          {{ detail.status === 'open' ? '关闭任务' : '重新打开' }}
        </button>
        <button class="btn btn-sm btn-danger" :disabled="busy" @click="removeTask">删除</button>
      </div>

      <div class="card-body">
        <dl class="kv" style="grid-template-columns: 104px 1fr">
          <dt>任务 ID</dt>
          <dd class="mono">
            {{ detail.id }}
            <button class="btn btn-sm" style="margin-left: 6px" @click="copy(detail.id, '任务 ID 已复制')">复制</button>
          </dd>
          <dt>Agent</dt>
          <dd>{{ detail.agent_name || '-' }}</dd>
          <dt>创建 / 最后消息</dt>
          <dd class="muted small">{{ fmtTime(detail.created_at) }} → {{ fmtTime(detail.last_message_at) }}</dd>
          <dt>上下文 meta</dt>
          <dd class="mono small">{{ JSON.stringify(detail.meta || {}) }}</dd>
        </dl>

        <div v-if="replyLink" class="link-box">
          <div class="field-label" style="margin-bottom: 6px">免登录回复链接（可直接发给用户）</div>
          <div class="link-row">
            <input :value="replyLink" readonly class="mono" />
            <button class="btn btn-sm" @click="copy(replyLink, '回复链接已复制')">复制</button>
            <button class="btn btn-sm" :disabled="busy" @click="rotateLink">轮换</button>
            <a class="btn btn-sm" :href="replyLink" target="_blank">打开</a>
          </div>
          <div class="field-hint">
            链接内含签名令牌，谁拿到谁就能回信（仅限本任务）。有效期至 {{ linkExpires?.slice(0, 10) }}；轮换后旧链接立即失效。
          </div>
        </div>
      </div>

      <div class="card-body" style="border-top: 1px solid var(--border); padding-top: 14px">
        <div class="field-label">会话线程 · {{ messages.length }} 条</div>
        <div v-if="detailLoading" class="empty">加载中…</div>
        <div v-else class="mini-thread">
          <div v-for="m in messages" :key="m.id" class="mini-msg" :class="m.role">
            <div class="mini-meta">
              <b>{{ m.role === 'user' ? (m.author || '用户') : m.role === 'system' ? '系统' : (m.author || 'Agent') }}</b>
              <span class="muted">{{ fmtClock(m.created_at) }}</span>
              <span v-if="m.source === 'email'" class="badge badge-mute">邮件</span>
              <span v-else-if="m.source === 'web'" class="badge badge-ok">网页</span>
            </div>
            <div class="mini-content">{{ m.content }}</div>
          </div>
        </div>
      </div>

      <div class="card-body" style="border-top: 1px solid var(--border)">
        <div class="field-label">以 Agent 身份发言（需要 {{ scopesHint }} 权限）</div>
        <textarea v-model="draft" rows="3" placeholder="追加一条 Agent 消息…" />
        <div class="row" style="margin-top: 10px">
          <input v-model="notifyEmail" type="text" placeholder="同时邮件通知（可选，多个用逗号分隔）" />
          <button class="btn btn-primary" :disabled="busy || !draft.trim()" @click="sendAsAgent">发送</button>
        </div>
      </div>
    </div>

    <div v-else class="card task-detail">
      <div class="empty">从左侧选择一个任务，或让 Agent 调 <span class="mono">POST /api/v1/tasks</span> 新建</div>
    </div>
  </div>
</template>
