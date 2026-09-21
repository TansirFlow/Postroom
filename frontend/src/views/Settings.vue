<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, toast } from '../api'

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const testResult = ref(null)
const globalSmtp = ref({})
const effective = ref({})
const ownConfigured = ref(false)

const form = reactive({
  host: '',
  port: 465,
  use_ssl: true,
  starttls: false,
  user: '',
  password: '',
  from_email: '',
  from_name: '',
})

const advanced = reactive({
  public_base_url: '',
  test_recipients: '',
})
const effectiveBaseUrl = ref('')
const globalBaseUrl = ref('')

const pwd = reactive({ old_password: '', new_password: '', confirm: '' })
const changingPwd = ref(false)

const ownMode = computed(() => Boolean(form.host.trim()))

async function load() {
  loading.value = true
  try {
    const d = await api.get('/api/v1/settings')
    const smtp = d.smtp || {}
    form.host = smtp.host || ''
    form.port = smtp.port || 465
    form.use_ssl = smtp.use_ssl === undefined ? true : Boolean(smtp.use_ssl)
    form.starttls = smtp.starttls === undefined ? false : Boolean(smtp.starttls)
    form.user = smtp.user || ''
    form.password = ''
    form.from_email = smtp.from_email || ''
    form.from_name = smtp.from_name || ''

    advanced.public_base_url = d.public_base_url || ''
    advanced.test_recipients = d.test_recipients || ''
    effectiveBaseUrl.value = d.effective_public_base_url || ''
    globalBaseUrl.value = d.global_public_base_url || ''
    globalSmtp.value = d.global_smtp || {}
    effective.value = d.effective_smtp || {}
    ownConfigured.value = Boolean(d.smtp_configured)
    pwd.old_password = ''
    pwd.new_password = ''
    pwd.confirm = ''
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

function smtpPayload() {
  const body = {
    host: form.host.trim(),
    port: Number(form.port) || 465,
    use_ssl: form.use_ssl,
    starttls: form.starttls,
    user: form.user.trim(),
    from_email: form.from_email.trim(),
    from_name: form.from_name.trim(),
  }
  if (form.password) body.password = form.password
  return body
}

async function save() {
  saving.value = true
  testResult.value = null
  try {
    const d = await api.put('/api/v1/settings', {
      smtp: smtpPayload(),
      public_base_url: advanced.public_base_url.trim(),
      test_recipients: advanced.test_recipients.trim(),
    })
    effective.value = d.effective_smtp || {}
    effectiveBaseUrl.value = d.effective_public_base_url || ''
    ownConfigured.value = Boolean((d.smtp || {}).host)
    form.password = ''
    toast('设置已保存')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  testing.value = true
  testResult.value = null
  try {
    const d = await api.post('/api/v1/settings/smtp-test', { smtp: smtpPayload() })
    testResult.value = d
    toast(d.ok ? 'SMTP 连接与登录成功' : 'SMTP 连接失败，详见结果', d.ok ? 'success' : 'error')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    testing.value = false
  }
}

async function clearOwnSmtp() {
  if (!window.confirm('清空后本账号将改用服务器的全局 SMTP 配置，确认继续？')) return
  try {
    await api.put('/api/v1/settings', { smtp: { host: '' } })
    toast('已切回服务器全局 SMTP')
    await load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function changePassword() {
  if (!pwd.old_password || !pwd.new_password) {
    toast('请填写当前密码与新密码', 'warn')
    return
  }
  if (pwd.new_password !== pwd.confirm) {
    toast('两次输入的新密码不一致', 'warn')
    return
  }
  changingPwd.value = true
  try {
    await api.post('/api/v1/auth/password', {
      old_password: pwd.old_password,
      new_password: pwd.new_password,
    })
    toast('密码已修改，其它设备的登录已失效')
    pwd.old_password = ''
    pwd.new_password = ''
    pwd.confirm = ''
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    changingPwd.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="card">
    <div class="card-head">
      <div class="card-title">邮件通道（SMTP）</div>
      <div class="card-desc">
        每个账号可以配置自己的发信通道，与其它账号互不影响
      </div>
      <div class="spacer" />
      <button class="btn btn-sm" :disabled="loading" @click="load">刷新</button>
    </div>

    <div class="card-body">
      <div class="banner" :class="ownConfigured ? 'banner-ok' : 'banner-info'">
        <template v-if="ownConfigured">
          当前使用<strong>本账号自己的 SMTP</strong>：{{ form.host }}:{{ form.port }}
        </template>
        <template v-else>
          当前使用<strong>服务器全局 SMTP</strong>
          <span class="mono small">
            （{{ globalSmtp.host || '未配置' }}:{{ globalSmtp.port || '-' }}）
          </span>
          ；在下面填写主机即可启用本账号专属通道。
        </template>
      </div>

      <div class="form-grid">
        <div class="field">
          <label class="field-label">SMTP 主机</label>
          <input v-model="form.host" type="text" placeholder="smtp.example.com" />
          <div class="field-hint">留空 = 使用服务器全局 SMTP 配置</div>
        </div>
        <div class="field">
          <label class="field-label">端口</label>
          <input v-model.number="form.port" type="number" min="1" max="65535" placeholder="465" />
        </div>
        <div class="field">
          <label class="field-label">认证账号</label>
          <input v-model="form.user" type="text" placeholder="you@example.com" />
        </div>
        <div class="field">
          <label class="field-label">认证密码 / 授权码</label>
          <input
            v-model="form.password"
            type="password"
            :placeholder="ownConfigured ? '已保存，留空表示不修改' : '应用专用密码'"
          />
        </div>
        <div class="field">
          <label class="field-label">发件地址</label>
          <input v-model="form.from_email" type="text" placeholder="留空则与认证账号相同" />
        </div>
        <div class="field">
          <label class="field-label">发件人显示名</label>
          <input v-model="form.from_name" type="text" placeholder="Postroom" />
        </div>
      </div>

      <div class="row" style="align-items: center; gap: 18px; margin-top: 4px">
        <label class="check"><input v-model="form.use_ssl" type="checkbox" /> 使用 SSL（465）</label>
        <label class="check"><input v-model="form.starttls" type="checkbox" /> 使用 STARTTLS（587）</label>
        <span class="small muted">一般二选一：465 用 SSL，587 用 STARTTLS</span>
      </div>

      <div class="row" style="margin-top: 14px">
        <button class="btn btn-primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存设置' }}
        </button>
        <button class="btn" :disabled="testing" @click="testConnection">
          {{ testing ? '测试中…' : '测试连接' }}
        </button>
        <button v-if="ownConfigured" class="btn btn-danger" @click="clearOwnSmtp">
          切回全局配置
        </button>
      </div>

      <div v-if="testResult" class="test-result">
        <div class="row" style="align-items: center">
          <span class="badge" :class="testResult.ok ? 'badge-ok' : 'badge-err'">
            {{ testResult.ok ? '连接成功' : '连接失败' }}
          </span>
          <span class="small muted">
            {{ testResult.host }}:{{ testResult.port }}
            <template v-if="testResult.latency_ms !== undefined"> · {{ testResult.latency_ms }}ms</template>
            <template v-if="testResult.source"> · 来源 {{ testResult.source === 'user' ? '本账号' : '服务器全局' }}</template>
          </span>
        </div>
        <pre v-if="testResult.error" class="code">{{ testResult.error.code }}: {{ testResult.error.message }}</pre>
        <pre v-else-if="testResult.banner" class="code">{{ testResult.banner }}</pre>
      </div>

      <div class="small muted" style="margin-top: 12px">
        当前生效配置来源：
        <strong>{{ effective.source === 'user' ? '本账号' : '服务器全局 .env' }}</strong>
        ，发件地址 <span class="mono">{{ effective.from_addr || '-' }}</span>
        ，密码 <span class="mono">{{ effective.password_set ? '已设置' : '未设置' }}</span>
        （密码只存服务端数据库，接口从不回传明文）
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-head">
      <div class="card-title">回复链接与测试</div>
      <div class="card-desc">影响本账号发出的「点开即回信」链接地址</div>
    </div>
    <div class="card-body">
      <div class="field">
        <label class="field-label">对外地址（回复链接的根地址）</label>
        <input v-model="advanced.public_base_url" type="text" placeholder="https://your-domain.com" />
        <div class="field-hint">
          留空则回落服务器全局配置（当前全局：
          <span class="mono">{{ globalBaseUrl || '（未配置，取请求 host）' }}</span>）；
          当前生效值 <span class="mono">{{ effectiveBaseUrl }}</span>
        </div>
      </div>
      <div class="field">
        <label class="field-label">测试收件人（逗号分隔）</label>
        <input v-model="advanced.test_recipients" type="text" placeholder="you@example.com, another@example.com" />
        <div class="field-hint">仅用于控制台的「一键测试发信」，不会自动发送</div>
      </div>
      <button class="btn btn-primary" :disabled="saving" @click="save">保存设置</button>
    </div>
  </div>

  <div class="card">
    <div class="card-head">
      <div class="card-title">修改密码</div>
      <div class="card-desc">修改后，其它设备上的登录状态会立即失效</div>
    </div>
    <div class="card-body">
      <div class="form-grid">
        <div class="field">
          <label class="field-label">当前密码</label>
          <input v-model="pwd.old_password" type="password" autocomplete="current-password" />
        </div>
        <div class="field">
          <label class="field-label">新密码</label>
          <input v-model="pwd.new_password" type="password" autocomplete="new-password" placeholder="至少 8 位" />
        </div>
        <div class="field">
          <label class="field-label">确认新密码</label>
          <input v-model="pwd.confirm" type="password" autocomplete="new-password" />
        </div>
      </div>
      <div class="small muted" style="margin-bottom: 10px">
        密码至少 8 位，且需包含字母、数字、符号中的至少两类。
      </div>
      <button class="btn btn-primary" :disabled="changingPwd" @click="changePassword">
        {{ changingPwd ? '提交中…' : '修改密码' }}
      </button>
    </div>
  </div>
</template>
