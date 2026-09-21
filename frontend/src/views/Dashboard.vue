<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, fmtBytes, fmtTime, state, toast } from '../api'

const loading = ref(true)
const overview = ref(null)
const recent = ref([])
const smtpResult = ref(null)
const smtpTesting = ref(false)
const error = ref('')

const stats = computed(() => overview.value?.mail_stats || {})
const smtp = computed(() => overview.value?.smtp || {})
const testRecipients = computed(() => overview.value?.test_recipients || [])
const taskStats = computed(() => overview.value?.task_stats || {})
const waitingTasks = ref([])

async function load() {
  loading.value = true
  error.value = ''
  try {
    overview.value = await api.get('/api/v1/overview')
    const [logs, tasks] = await Promise.all([
      api.get('/api/v1/mail/logs?page=1&page_size=6'),
      api.get('/api/v1/tasks?status=open&page_size=5').catch(() => ({ items: [] })),
    ])
    recent.value = logs.items || []
    waitingTasks.value = (tasks.items || []).filter((t) => t.unread_for_agent > 0)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function testSmtp() {
  smtpTesting.value = true
  smtpResult.value = null
  try {
    smtpResult.value = await api.post('/api/v1/mail/verify-connection')
    if (smtpResult.value.ok) toast(`SMTP 连接正常（${smtpResult.value.latency_ms}ms）`)
    else toast(smtpResult.value.error?.message || 'SMTP 连接失败', 'error')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    smtpTesting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="error" class="banner banner-err">{{ error }}</div>

    <div class="grid grid-4">
      <div class="stat">
        <div class="stat-label">今日发信</div>
        <div class="stat-value">{{ stats.today ?? '-' }}</div>
        <div class="stat-foot">累计 {{ stats.total ?? 0 }} 封</div>
      </div>
      <div class="stat">
        <div class="stat-label">成功 / 失败</div>
        <div class="stat-value">{{ stats.sent ?? 0 }} <span class="muted" style="font-size:17px">/ {{ stats.failed ?? 0 }}</span></div>
        <div class="stat-foot">失败率 {{ stats.total ? ((stats.failed / stats.total) * 100).toFixed(1) : '0.0' }}%</div>
      </div>
      <div class="stat">
        <div class="stat-label">平均发送耗时</div>
        <div class="stat-value">{{ stats.avg_latency_ms ?? 0 }}<span class="muted" style="font-size:15px"> ms</span></div>
        <div class="stat-foot">含 SMTP 建连 + 登录</div>
      </div>
      <div class="stat">
        <div class="stat-label">API 密钥</div>
        <div class="stat-value">{{ overview?.keys?.count ?? '-' }}</div>
        <div class="stat-foot">限流 {{ overview?.keys?.rate_limit_per_hour ?? '-' }} 封 / 小时</div>
      </div>
      <div class="stat">
        <div class="stat-label">进行中任务</div>
        <div class="stat-value">{{ taskStats.open ?? '-' }}</div>
        <div class="stat-foot">累计 {{ taskStats.total ?? 0 }} 个会话</div>
      </div>
      <div class="stat" :style="taskStats.waiting_reply_tasks ? 'border-color:#c5d6ff' : ''">
        <div class="stat-label">待你 / Agent 回复</div>
        <div class="stat-value" :style="taskStats.waiting_reply_tasks ? 'color:var(--primary-strong)' : ''">
          {{ taskStats.waiting_reply_tasks ?? '-' }}
        </div>
        <div class="stat-foot">未读用户消息 {{ taskStats.unread_user_messages ?? 0 }} 条</div>
      </div>
    </div>

    <div v-if="waitingTasks.length" class="banner banner-warn" style="margin-top: 16px">
      <span>
        有 {{ waitingTasks.length }} 个任务收到用户回复待处理：
        <b>{{ waitingTasks.map((t) => t.title || t.id).join('、') }}</b>
      </span>
      <router-link class="btn btn-sm" to="/tasks">去查看</router-link>
    </div>

    <div class="grid grid-2" style="margin-top: 16px">
      <div class="card">
        <div class="card-head">
          <div class="card-title">SMTP 发信通道</div>
          <div class="spacer" />
          <button class="btn btn-sm" :disabled="smtpTesting" @click="testSmtp">
            {{ smtpTesting ? '测试中…' : '测试连通性' }}
          </button>
        </div>
        <div class="card-body">
          <dl class="kv">
            <dt>服务器</dt>
            <dd class="mono">{{ smtp.host }}:{{ smtp.port }} <span class="muted">({{ smtp.ssl ? 'SSL' : 'STARTTLS' }})</span></dd>
            <dt>认证账号</dt>
            <dd class="mono">{{ smtp.user || '未配置' }}</dd>
            <dt>发件人</dt>
            <dd>{{ smtp.from_name }} &lt;{{ smtp.from_email }}&gt;</dd>
            <dt>密码</dt>
            <dd>
              <span class="badge" :class="smtp.password_set ? 'badge-ok' : 'badge-err'">
                {{ smtp.password_set ? '已设置' : '未设置' }}
              </span>
            </dd>
            <dt>测试收件人</dt>
            <dd>
              <span v-for="r in testRecipients" :key="r" class="chip" style="margin-right: 6px">{{ r }}</span>
            </dd>
          </dl>

          <div v-if="smtpResult" style="margin-top: 14px">
            <div class="badge" :class="smtpResult.ok ? 'badge-ok' : 'badge-err'">
              {{ smtpResult.ok ? '连接正常' : '连接失败' }}
            </div>
            <pre class="code" style="margin-top: 8px">{{ JSON.stringify(smtpResult, null, 2) }}</pre>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-title">Agent 接入</div>
          <div class="card-desc">30 秒让你的 Agent 学会发邮件</div>
        </div>
        <div class="card-body">
          <div class="field">
            <label class="field-label">1. 拉取工具清单（无需鉴权）</label>
            <pre class="code">GET {{ '/api/v1/agent/tools' }}</pre>
          </div>
          <div class="field">
            <label class="field-label">2. 调用发信接口</label>
            <pre class="code">POST /api/v1/mail/send
X-API-Key: sk-agent-***

{ "to": ["you@example.com"],
  "subject": "来自 Agent 的问候",
  "body": "Hello from the tool API." }</pre>
          </div>
          <div class="row" style="margin-top: 4px">
            <router-link class="btn btn-primary" to="/compose">去工作台试一封</router-link>
            <router-link class="btn" to="/docs">查看完整文档</router-link>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="card-title">最近发送</div>
        <div class="spacer" />
        <button class="btn btn-sm" @click="load">刷新</button>
        <router-link class="btn btn-sm" to="/logs">全部记录</router-link>
      </div>
      <table v-if="recent.length">
        <thead>
          <tr>
            <th>时间</th>
            <th>收件人</th>
            <th>主题</th>
            <th>状态</th>
            <th>耗时</th>
            <th>大小</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in recent" :key="row.id">
            <td class="mono muted">{{ fmtTime(row.created_at) }}</td>
            <td class="mono">{{ row.to_addrs?.join(', ') }}</td>
            <td>{{ row.subject }}</td>
            <td>
              <span class="badge" :class="row.status === 'sent' ? 'badge-ok' : 'badge-err'">{{ row.status }}</span>
            </td>
            <td class="mono muted">{{ row.latency_ms }}ms</td>
            <td class="mono muted">{{ fmtBytes(row.size_bytes) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">{{ loading ? '加载中…' : '还没有发信记录' }}</div>
    </div>
  </div>
</template>
