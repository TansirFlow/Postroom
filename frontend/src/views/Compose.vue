<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, fmtBytes, toast } from '../api'

const form = reactive({
  mode: 'text', // text | html | template
  to: '',
  cc: '',
  bcc: '',
  subject: '',
  body: '',
  html: '',
  template: '',
  replyTo: '',
  idempotency: '',
  taskId: '',
  attachReplyLink: true,
})

const tasks = ref([])
const templates = ref([])
// 当前部署地址（多用户/多域名场景下不要写死端口）
const apiOrigin = window.location.origin
const templateVars = ref({})
const attachments = ref([])
const testRecipients = ref([])
const sending = ref(false)
const result = ref(null)
const error = ref('')
const elapsed = ref(0)

const selectedTemplate = computed(() => templates.value.find((t) => t.name === form.template) || null)

function parseList(value) {
  return value
    .split(/[,;\s\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function toggleRecipient(addr) {
  const list = parseList(form.to)
  const i = list.indexOf(addr)
  if (i > -1) list.splice(i, 1)
  else list.push(addr)
  form.to = list.join(', ')
}

function isRecipientOn(addr) {
  return parseList(form.to).includes(addr)
}

function buildPayload() {
  const payload = { to: parseList(form.to) }
  if (form.subject.trim()) payload.subject = form.subject.trim()
  if (form.cc.trim()) payload.cc = parseList(form.cc)
  if (form.bcc.trim()) payload.bcc = parseList(form.bcc)
  if (form.replyTo.trim()) payload.reply_to = form.replyTo.trim()
  if (form.idempotency.trim()) payload.idempotency_key = form.idempotency.trim()
  if (form.taskId) {
    payload.task_id = form.taskId
    payload.attach_reply_link = form.attachReplyLink
  }

  if (form.mode === 'template') {
    payload.template = form.template
    payload.variables = { ...templateVars.value }
    if (!payload.subject) payload.subject = selectedTemplate.value?.subject || '(模板主题)'
  } else if (form.mode === 'html') {
    payload.html = form.html
    if (form.body.trim()) payload.body = form.body
  } else {
    payload.body = form.body
  }

  if (attachments.value.length) {
    payload.attachments = attachments.value.map((a) => ({
      filename: a.filename,
      content_base64: a.content_base64,
      mime_type: a.mime_type,
    }))
  }
  return payload
}

const payloadPreview = computed(() => {
  try {
    const p = buildPayload()
    const clone = JSON.parse(JSON.stringify(p))
    if (clone.attachments) {
      clone.attachments = clone.attachments.map((a) => ({ ...a, content_base64: `<base64 ${a.content_base64.length} chars>` }))
    }
    return JSON.stringify(clone, null, 2)
  } catch (e) {
    return '{}'
  }
})

async function onFiles(event) {
  const files = Array.from(event.target.files || [])
  for (const file of files) {
    if (file.size > 5 * 1024 * 1024) {
      toast(`${file.name} 超过 5MB，已跳过`, 'warn')
      continue
    }
    const content_base64 = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
      reader.onerror = reject
      reader.readAsDataURL(file)
    })
    attachments.value.push({ filename: file.name, content_base64, mime_type: file.type, size: file.size })
  }
  event.target.value = ''
}

function removeAttachment(i) {
  attachments.value.splice(i, 1)
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    toast('已复制')
  } catch (e) {
    toast('复制失败', 'warn')
  }
}

async function send() {
  error.value = ''
  result.value = null
  if (!parseList(form.to).length) {
    error.value = '请至少填写一个收件人'
    return
  }
  if (form.mode === 'template' && !form.template) {
    error.value = '请选择邮件模板'
    return
  }
  if (form.mode === 'text' && !form.body.trim()) {
    error.value = '正文不能为空'
    return
  }
  if (form.mode === 'html' && !form.html.trim()) {
    error.value = 'HTML 正文不能为空'
    return
  }

  sending.value = true
  const t0 = performance.now()
  try {
    const payload = buildPayload()
    const timer = setInterval(() => (elapsed.value = Math.round(performance.now() - t0)), 100)
    try {
      result.value = await api.post('/api/v1/mail/send', payload)
    } finally {
      clearInterval(timer)
    }
    toast(`发送成功 · ${result.value.latency_ms}ms`)
  } catch (e) {
    error.value = e.message
    if (e.detail) result.value = { status: 'failed', ...e.detail }
    toast(e.message, 'error', 7000)
  } finally {
    sending.value = false
  }
}

async function loadMeta() {
  try {
    const [tpl, rec, taskList] = await Promise.all([
      api.get('/api/v1/mail/templates'),
      api.get('/api/v1/mail/test-recipients'),
      api.get('/api/v1/tasks?status=open&page_size=50').catch(() => ({ items: [] })),
    ])
    templates.value = tpl
    testRecipients.value = rec.recipients || []
    tasks.value = taskList.items || []
    form.template = templates.value[0]?.name || ''
    applyTemplate(form.template)

    // 支持 /compose?task=task_xxx 从任务页跳转过来
    const preset = new URLSearchParams(window.location.search).get('task')
    if (preset && tasks.value.some((t) => t.id === preset)) form.taskId = preset
  } catch (e) {
    /* 未配置 key 时静默 */
  }
}

async function reloadTasks() {
  try {
    const data = await api.get('/api/v1/tasks?status=open&page_size=50')
    tasks.value = data.items || []
  } catch (e) {
    toast(e.message, 'error')
  }
}

function applyTemplate(name) {
  const tpl = templates.value.find((t) => t.name === name)
  const vars = {}
  for (const v of tpl?.variables || []) vars[v] = ''
  if (name === 'welcome') Object.assign(vars, { name: 'Alice', product: 'Postroom' })
  if (name === 'alert') Object.assign(vars, { level: 'ERROR', title: '数据同步任务异常退出', target: 'sync-worker', time: new Date().toLocaleString('zh-CN'), message: '进程 exit code 1' })
  if (name === 'report') Object.assign(vars, { title: '季度进度简报', period: new Date().toISOString().slice(0, 10), summary: '本季度完成率提升 12%', detail: '2026-Q3 | 42/45 通过 | 3 项待确认' })
  templateVars.value = vars
}

onMounted(loadMeta)
</script>

<template>
  <div class="grid" style="grid-template-columns: minmax(0, 1.35fr) minmax(320px, 1fr)">
    <div>
      <div class="card">
        <div class="card-head">
          <div class="card-title">发信工作台</div>
          <div class="card-desc">与 Agent 调用同一套接口（POST /api/v1/mail/send）</div>
        </div>
        <div class="card-body">
          <div v-if="error" class="banner banner-err" style="margin-bottom: 14px">{{ error }}</div>

          <div class="field">
            <label class="field-label">收件人 *</label>
            <input v-model="form.to" type="text" placeholder="多个地址用逗号分隔" />
            <div class="chips" style="margin-top: 7px">
              <span class="small muted" style="align-self: center">快捷：</span>
              <span
                v-for="r in testRecipients"
                :key="r"
                class="chip"
                :class="{ on: isRecipientOn(r) }"
                @click="toggleRecipient(r)"
              >
                {{ r }}
              </span>
            </div>
          </div>

          <div class="row">
            <div class="field">
              <label class="field-label">抄送 CC</label>
              <input v-model="form.cc" type="text" placeholder="可选" />
            </div>
            <div class="field">
              <label class="field-label">密送 BCC</label>
              <input v-model="form.bcc" type="text" placeholder="可选" />
            </div>
          </div>

          <div class="field">
            <label class="field-label">主题 <span v-if="form.mode !== 'template'">*</span></label>
            <input v-model="form.subject" type="text" :placeholder="form.mode === 'template' ? '留空则使用模板主题' : '邮件主题'" />
          </div>

          <div class="field">
            <label class="field-label">关联任务（可选 · 附带免登录回复链接）</label>
            <div style="display: flex; gap: 8px">
              <select v-model="form.taskId">
                <option value="">不关联任务</option>
                <option v-for="t in tasks" :key="t.id" :value="t.id">
                  {{ t.title || t.id }} · {{ t.agent_name || 'Agent' }}
                </option>
              </select>
              <button class="btn btn-sm" @click="reloadTasks">刷新</button>
            </div>
            <div v-if="form.taskId" class="field-hint">
              <label class="switch">
                <input v-model="form.attachReplyLink" type="checkbox" />
                在正文追加「点开即回复」链接，并把本封内容记入该任务会话
              </label>
            </div>
            <div v-else class="field-hint">
              在「任务会话」页新建任务，或让 Agent 调 <span class="mono">POST /api/v1/tasks</span>；
              关联后邮件里会自带回复入口，用户点开就能跟你对话。
            </div>
          </div>

          <div class="tabs">
            <div class="tab" :class="{ active: form.mode === 'text' }" @click="form.mode = 'text'">纯文本</div>
            <div class="tab" :class="{ active: form.mode === 'html' }" @click="form.mode = 'html'">HTML</div>
            <div class="tab" :class="{ active: form.mode === 'template' }" @click="form.mode = 'template'">模板</div>
          </div>

          <template v-if="form.mode === 'template'">
            <div class="field">
              <label class="field-label">模板</label>
              <select v-model="form.template" @change="applyTemplate(form.template)">
                <option v-for="t in templates" :key="t.name" :value="t.name">{{ t.label }} — {{ t.description }}</option>
              </select>
              <div class="field-hint">模板主题：<span class="mono">{{ selectedTemplate?.subject }}</span></div>
            </div>
            <div class="row">
              <div v-for="(val, key) in templateVars" :key="key" class="field">
                <label class="field-label">{{ key }}</label>
                <input v-model="templateVars[key]" type="text" :placeholder="'{{' + key + '}}'" />
              </div>
            </div>
            <div class="field">
              <label class="field-label">附加纯文本正文（可选）</label>
              <textarea v-model="form.body" rows="2" placeholder="留空则使用模板正文" />
            </div>
          </template>

          <template v-else>
            <div class="field">
              <label class="field-label">{{ form.mode === 'html' ? 'HTML 正文 *' : '正文 *' }}</label>
              <textarea v-model="form[mode === 'html' ? 'html' : 'body']" rows="9" :placeholder="form.mode === 'html' ? '<h2>Hello</h2>' : '写下你想发送的内容…'" />
            </div>
            <div v-if="form.mode === 'html'" class="field">
              <label class="field-label">附带的纯文本版本（可选，兼容不支持 HTML 的客户端）</label>
              <textarea v-model="form.body" rows="3" />
            </div>
          </template>

          <div class="field">
            <label class="field-label">附件</label>
            <label class="dropzone" style="display: block">
              点击选择文件（单个 ≤ 5MB，总计 ≤ 10MB）
              <input type="file" multiple style="display: none" @change="onFiles" />
            </label>
            <div v-if="attachments.length" class="chips" style="margin-top: 8px">
              <span v-for="(a, i) in attachments" :key="a.filename" class="chip on">
                {{ a.filename }} · {{ fmtBytes(a.size) }}
                <b style="cursor: pointer" @click="removeAttachment(i)">×</b>
              </span>
            </div>
          </div>

          <div class="row">
            <div class="field">
              <label class="field-label">回复地址 Reply-To</label>
              <input v-model="form.replyTo" type="text" placeholder="可选" />
            </div>
            <div class="field">
              <label class="field-label">幂等键 Idempotency-Key</label>
              <div style="display: flex; gap: 8px">
                <input v-model="form.idempotency" type="text" placeholder="重试时不会重复发送" />
                <button class="btn btn-sm" @click="form.idempotency = 'ui-' + Date.now().toString(36)">生成</button>
              </div>
            </div>
          </div>

          <div class="row" style="margin-top: 4px">
            <button class="btn btn-primary" :disabled="sending" @click="send">
              {{ sending ? `发送中… ${elapsed}ms` : '发送邮件' }}
            </button>
            <button class="btn" :disabled="sending" @click="result = null">清空结果</button>
          </div>
        </div>
      </div>

      <div v-if="result" class="card">
        <div class="card-head">
          <div class="card-title">发送结果</div>
          <div class="spacer" />
          <span class="badge" :class="result.status === 'sent' ? 'badge-ok' : 'badge-err'">{{ result.status }}</span>
        </div>
        <div class="card-body">
          <dl v-if="result.status === 'sent'" class="kv">
            <dt>记录 ID</dt>
            <dd class="mono">{{ result.id }}</dd>
            <dt>Message-ID</dt>
            <dd class="mono">{{ result.message_id }}</dd>
            <dt>收件人</dt>
            <dd class="mono">{{ result.to?.join(', ') }}</dd>
            <dt>耗时 / 大小</dt>
            <dd class="mono">{{ result.latency_ms }} ms · {{ fmtBytes(result.size_bytes) }} <span v-if="result.attempts > 1">· 重试 {{ result.attempts }} 次</span></dd>
            <dt>剩余配额</dt>
            <dd class="mono">{{ result.rate_limit?.remaining }} / {{ result.rate_limit?.limit }}</dd>
          </dl>
          <div v-if="result.status === 'sent' && result.reply_url" class="link-box" style="margin-top: 14px">
            <div class="field-label" style="margin-bottom: 6px">本封邮件内的回复链接</div>
            <div class="link-row">
              <input :value="result.reply_url" readonly class="mono" />
              <button class="btn btn-sm" @click="copyText(result.reply_url)">复制</button>
              <a class="btn btn-sm" :href="result.reply_url" target="_blank">打开</a>
            </div>
            <div class="field-hint">用户点开即可回信；Agent 用 <span class="mono">GET /api/v1/tasks/{{ result.task_id }}/messages</span> 取回回复。</div>
          </div>
          <pre class="code" style="margin-top: 14px">{{ JSON.stringify(result, null, 2) }}</pre>
        </div>
      </div>
    </div>

    <div>
      <div class="card">
        <div class="card-head">
          <div class="card-title">请求预览</div>
          <div class="card-desc">Agent 实际发出的 body</div>
        </div>
        <div class="card-body">
          <pre class="code">{{ payloadPreview }}</pre>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div class="card-title">cURL 复现</div></div>
        <div class="card-body">
          <pre class="code">curl -X POST {{ apiOrigin }}/api/v1/mail/send \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -d '{{ payloadPreview.replace(/\s+/g, ' ').slice(0, 220) }}'</pre>
          <div class="field-hint" style="margin-top: 8px">
            提示：把 <code>{{ apiOrigin }}</code> 换成你的部署地址即可给 Agent 使用；
            <code>$AGENT_API_KEY</code> 取自「API 密钥」页。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
