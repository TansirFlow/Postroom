<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, toast } from '../api'

const toolsData = ref(null)
const loadError = ref('')
const snippetTab = ref('curl')
const copied = ref('')

const endpoints = [
  { method: 'GET', path: '/api/v1/health', scope: '—', desc: '健康检查（无需鉴权）' },
  { method: 'GET', path: '/api/v1/agent/tools', scope: '—', desc: 'Agent 工具清单（function-calling 格式）' },
  { method: 'GET', path: '/api/v1/agent/manifest', scope: '—', desc: '服务能力摘要与快速上手代码' },
  { method: 'GET', path: '/api/v1/whoami', scope: '任意', desc: '校验当前 Key 与权限' },
  { method: 'GET', path: '/api/v1/overview', scope: 'mail:read', desc: '控制台概览数据' },
  { method: 'POST', path: '/api/v1/conversations', scope: 'tasks:write', desc: '幂等创建/复用对话（Agent 侧一个对话调一次）' },
  { method: 'GET', path: '/api/v1/conversations', scope: 'tasks:read', desc: '对话列表（含未读统计）' },
  { method: 'GET', path: '/api/v1/conversations/{id}', scope: 'tasks:read', desc: '对话详情 + 其下任务线程' },
  { method: 'PATCH', path: '/api/v1/conversations/{id}', scope: 'tasks:write', desc: '改标题 / 状态 / 外部标识' },
  { method: 'POST', path: '/api/v1/conversations/{id}/close', scope: 'tasks:write', desc: '关闭对话（旗下线程一起失效）' },
  { method: 'GET', path: '/api/v1/inbox', scope: 'tasks:read', desc: '★ 增量拉取全部对话的用户回复（定时任务入口，非阻塞）' },
  { method: 'POST', path: '/api/v1/inbox/ack', scope: 'tasks:write', desc: '推进拉取水位（只增不减，重复提交安全）' },
  { method: 'GET', path: '/api/v1/inbox/stats', scope: 'tasks:read', desc: '还有多少回复没取走' },
  { method: 'POST', path: '/api/v1/mail/send', scope: 'mail:send', desc: '发送邮件（支持 HTML / 附件 / 模板 / 幂等）' },
  { method: 'POST', path: '/api/v1/tasks', scope: 'tasks:write', desc: '创建任务线程，拿到 task_id 与免登录回复链接' },
  { method: 'GET', path: '/api/v1/tasks', scope: 'tasks:read', desc: '任务列表（可按 conversation_id 过滤）' },
  { method: 'GET', path: '/api/v1/tasks/{id}', scope: 'tasks:read', desc: '任务详情 + 会话线程 + 回复链接' },
  { method: 'PATCH', path: '/api/v1/tasks/{id}', scope: 'tasks:write', desc: '改标题 / 状态 / 上下文' },
  { method: 'POST', path: '/api/v1/tasks/{id}/messages', scope: 'tasks:write', desc: 'Agent 发消息（可顺带邮件通知）' },
  { method: 'GET', path: '/api/v1/tasks/{id}/messages', scope: 'tasks:read', desc: '单线程读取（after_id 增量；跨对话用 /inbox）' },
  { method: 'POST', path: '/api/v1/tasks/{id}/reply-link', scope: 'tasks:write', desc: '轮换回复链接（旧链接立即失效）' },
  { method: 'POST', path: '/api/v1/tasks/{id}/close', scope: 'tasks:write', desc: '关闭任务' },
  { method: 'DELETE', path: '/api/v1/tasks/{id}', scope: 'tasks:write', desc: '删除任务及会话消息（不可恢复）' },
  { method: 'GET', path: '/api/v1/reply/{token}', scope: '免鉴权', desc: '免登录：打开会话（链接即凭证）' },
  { method: 'GET', path: '/api/v1/reply/{token}/messages', scope: '免鉴权', desc: '免登录：增量拉取新消息' },
  { method: 'POST', path: '/api/v1/reply/{token}', scope: '免鉴权', desc: '免登录：用户回信' },
  { method: 'GET', path: '/api/v1/reply/{token}/link', scope: '免鉴权', desc: '免登录：旧链接自助换新链接' },
  { method: 'GET', path: '/api/v1/mail/logs', scope: 'mail:read', desc: '分页查询发送记录' },
  { method: 'GET', path: '/api/v1/mail/logs/{id}', scope: 'mail:read', desc: '单封邮件详情' },
  { method: 'GET', path: '/api/v1/mail/stats', scope: 'mail:read', desc: '发信统计与限流快照' },
  { method: 'GET', path: '/api/v1/mail/templates', scope: 'mail:send', desc: '内置模板与变量' },
  { method: 'POST', path: '/api/v1/mail/verify-connection', scope: 'mail:send', desc: 'SMTP 连通性 / 登录测试' },
  { method: 'GET', path: '/api/v1/keys', scope: 'keys:manage', desc: '密钥列表' },
  { method: 'POST', path: '/api/v1/keys', scope: 'keys:manage', desc: '创建密钥（明文仅返回一次）' },
  { method: 'PATCH', path: '/api/v1/keys/{id}', scope: 'keys:manage', desc: '启用 / 停用密钥' },
  { method: 'DELETE', path: '/api/v1/keys/{id}', scope: 'keys:manage', desc: '删除密钥' },
  { method: 'POST', path: '/api/v1/auth/login', scope: '—（公开）', desc: '账号密码登录，换会话令牌' },
  { method: 'GET', path: '/api/v1/auth/me', scope: '任意凭证', desc: '当前登录身份与权限' },
  { method: 'POST', path: '/api/v1/auth/password', scope: '登录会话', desc: '修改自己的密码' },
  { method: 'GET', path: '/api/v1/settings', scope: 'mail:read', desc: '读取本账号设置（SMTP 等）' },
  { method: 'PUT', path: '/api/v1/settings', scope: '登录会话', desc: '保存本账号设置' },
  { method: 'GET', path: '/api/v1/users', scope: 'users:manage', desc: '用户列表（仅管理员）' },
  { method: 'POST', path: '/api/v1/users', scope: 'users:manage', desc: '新建账号（仅管理员）' },
]

const base = computed(() => window.location.origin)
// 控制台用会话令牌登录；调用下面这些接口的是 Agent，用的是在「API 密钥」页创建的密钥
const PLACEHOLDER_KEY = 'sk-agent-xxxxxxxx'
const keyPlaceholder = computed(() => PLACEHOLDER_KEY)
const keyReal = computed(() => PLACEHOLDER_KEY)

// 页面展示用掩码密钥（便于截图分享），点「复制」时替换为真实密钥
function buildSnippets(key) {
  return {
    curl: `curl -X POST ${base.value}/api/v1/mail/send \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: ${key}" \\
  -d '{
    "to": ["you@example.com"],
    "subject": "任务完成通知",
    "body": "2026-Q3 报表已生成，环比 +18.4%",
    "idempotency_key": "deploy-report-q3"
  }'`,
    python: `import requests

API = "${base.value}"
KEY = "${key}"

def send_email(to, subject, body=None, html=None, **kw):
    r = requests.post(
        f"{API}/api/v1/mail/send",
        headers={"X-API-Key": KEY},
        json={"to": to, "subject": subject, "body": body, "html": html, **kw},
        timeout=60,
    )
    data = r.json()
    if not data["ok"]:
        raise RuntimeError(data["error"])
    return data["data"]

print(send_email(["you@example.com"], "Hello from Python"))`,
    node: `const API = "${base.value}";
const KEY = "${key}";

async function sendEmail({ to, subject, body }) {
  const res = await fetch(\`\${API}/api/v1/mail/send\`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": KEY },
    body: JSON.stringify({ to, subject, body }),
  });
  const json = await res.json();
  if (!json.ok) throw new Error(json.error.message);
  return json.data;
}

await sendEmail({ to: ["you@example.com"], subject: "Hi", body: "from node" });`,
    agent: `# 1) 让 Agent 自动发现工具
import requests

KEY = "${key}"
manifest = requests.get("${base.value}/api/v1/agent/tools").json()["data"]
tools = manifest["tools"]        # OpenAI function-calling 格式，直接喂给模型
print([t["function"]["name"] for t in tools])
# ['send_email', 'list_email_logs', 'list_email_templates', 'check_mail_connection',
#  'create_task', 'get_task_messages', 'post_task_message', 'close_task']

# 2) 模型决定调用某个工具后，把参数原样转成 HTTP 请求
def dispatch(tool_name, args):
    tool = next(t for t in tools if t["function"]["name"] == tool_name)
    return requests.request(
        tool["method"], tool["endpoint"],
        headers={"X-API-Key": KEY}, json=args, timeout=60,
    ).json()`,
    flow: `# 任务闭环：邮件发出去 → 用户在网页回信 → Agent 取回
import requests

API, KEY = "${base.value}", "${key}"
H = {"X-API-Key": KEY}

# ① 定时任务开局：按 Agent 侧的对话标识幂等拿一个 conversation_id
#    同一个 external_id 重复调用只会复用已有对话，进程重启也不用记住 id
conv = requests.post(f"{API}/api/v1/conversations", headers=H, json={
    "external_id": "codex:2026Q3-report",     # 换成你自己的对话标识
    "title": "季度数据报表生成",
    "agent_name": "DeployBot",
}).json()["data"]
cid = conv["conversation_id"]
print("对话:", cid, "新建" if conv["created"] else "复用")

# ② 在这个对话下开一条线程并发信：正文会自动追加「点开即回复」按钮
sent = requests.post(f"{API}/api/v1/mail/send", headers=H, json={
    "to": ["someone@example.com"],
    "conversation_id": cid,                   # 也可只给 external_id
    "thread_title": "报表是否推送",
    "subject": "报表已生成，是否推送？",
    "body": "2026-Q3 报表已生成，总记录数 1,284,930，环比 +18.4%。要推送到正式库吗？",
}).json()["data"]
print("线程:", sent["task_id"], "回复链接:", sent["reply_url"])

# ③ 干别的去 —— 不用等用户，任务不会被自动关闭，回复链接默认 30 天有效

# ④ 定时任务（cron 每 30 秒）：一次拉走**全部对话**的增量回复，再按对话分发
#    不传 cursor 就用服务端水位；进程重启也不会重复投递
inbox = requests.get(f"{API}/api/v1/inbox", headers=H).json()["data"]
for item in inbox["items"]:
    print(f"[{item['conversation_external_id']}] {item['author']}: {item['content']}")

# ⑤ 分发成功后推进水位（只增不减，重复提交安全）
requests.post(f"{API}/api/v1/inbox/ack", headers=H,
              json={"upto_seq": inbox["next_cursor"]})

# ⑥ 回一句（可顺带邮件通知，同样自动带回复链接）
requests.post(f"{API}/api/v1/tasks/{sent['task_id']}/messages", headers=H, json={
    "content": "收到，已开始推送。",
    "notify_email": ["someone@example.com"],
})

# ⑦ 收尾：关闭对话，旗下线程的回复链接一起失效
requests.post(f"{API}/api/v1/conversations/{cid}/close", headers=H)`,
  }
}

const snippets = computed(() => buildSnippets(keyPlaceholder.value))

async function loadTools() {
  loadError.value = ''
  try {
    toolsData.value = await api.get('/api/v1/agent/tools')
  } catch (e) {
    loadError.value = e.message
  }
}

async function copy(text, tag) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = tag
    toast('已复制（含真实密钥）')
    setTimeout(() => (copied.value = ''), 1600)
  } catch (e) {
    toast('复制失败', 'warn')
  }
}

function copySnippet() {
  return copy(buildSnippets(keyReal.value)[snippetTab.value], snippetTab.value)
}

onMounted(loadTools)
</script>

<template>
  <div class="grid" style="grid-template-columns: minmax(0, 1fr) minmax(300px, 0.75fr)">
    <div>
      <div class="card">
        <div class="card-head">
          <div class="card-title">Agent 工具清单</div>
          <div class="card-desc">GET /api/v1/agent/tools · 无需鉴权</div>
          <div class="spacer" />
          <button class="btn btn-sm" @click="loadTools">重新拉取</button>
        </div>
        <div class="card-body">
          <div v-if="loadError" class="banner banner-err">{{ loadError }}</div>
          <div v-for="tool in toolsData?.tools || []" :key="tool.function.name" class="tool-card">
            <div style="display: flex; align-items: center; gap: 8px">
              <span class="tool-name">{{ tool.function.name }}</span>
              <span class="badge badge-mute">{{ tool.method }} {{ tool.endpoint.replace(base, '') }}</span>
              <span class="badge badge-warn">{{ tool.scope }}</span>
            </div>
            <div class="small" style="margin: 7px 0 9px; color: var(--text-2)">{{ tool.function.description }}</div>
            <div class="small muted" style="margin-bottom: 4px">
              参数：<span class="mono">{{ Object.keys(tool.function.parameters.properties || {}).join(', ') || '无' }}</span>
              <span v-if="tool.function.parameters.required?.length">
                · 必填 <span class="mono">{{ tool.function.parameters.required.join(', ') }}</span>
              </span>
            </div>
            <pre class="code" style="max-height: 210px">{{ JSON.stringify(tool.function.parameters, null, 2) }}</pre>
          </div>
          <div v-if="!toolsData && !loadError" class="empty">加载中…</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div class="card-title">接口列表</div></div>
        <table>
          <thead>
            <tr>
              <th style="width: 76px">方法</th>
              <th>路径</th>
              <th style="width: 108px">权限</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ep in endpoints" :key="ep.method + ep.path">
              <td>
                <span class="badge" :class="ep.method === 'GET' ? 'badge-mute' : ep.method === 'DELETE' ? 'badge-err' : 'badge-ok'">
                  {{ ep.method }}
                </span>
              </td>
              <td class="mono">{{ ep.path }}</td>
              <td class="mono small muted">{{ ep.scope }}</td>
              <td class="small">{{ ep.desc }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div>
      <div class="card">
        <div class="card-head"><div class="card-title">接入信息</div></div>
        <div class="card-body">
          <dl class="kv" style="grid-template-columns: 92px 1fr">
            <dt>Base URL</dt>
            <dd class="mono">{{ base }}</dd>
            <dt>鉴权方式</dt>
            <dd class="mono">X-API-Key: &lt;key&gt;</dd>
            <dt>备选</dt>
            <dd class="mono">Authorization: Bearer &lt;key&gt;</dd>
            <dt>响应格式</dt>
            <dd class="mono">{ ok, data, error, request_id }</dd>
            <dt>Swagger</dt>
            <dd><a :href="base + '/api/docs'" target="_blank">{{ base }}/api/docs</a></dd>
            <dt>OpenAPI</dt>
            <dd><a :href="base + '/openapi.json'" target="_blank">/openapi.json</a></dd>
          </dl>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-title">代码示例</div>
          <div class="spacer" />
          <button class="btn btn-sm" @click="copySnippet">
            {{ copied === snippetTab ? '已复制' : '复制' }}
          </button>
        </div>
        <div class="card-body">
          <div class="tabs">
            <div class="tab" :class="{ active: snippetTab === 'curl' }" @click="snippetTab = 'curl'">cURL</div>
            <div class="tab" :class="{ active: snippetTab === 'python' }" @click="snippetTab = 'python'">Python</div>
            <div class="tab" :class="{ active: snippetTab === 'node' }" @click="snippetTab = 'node'">Node</div>
            <div class="tab" :class="{ active: snippetTab === 'agent' }" @click="snippetTab = 'agent'">接入 Agent</div>
            <div class="tab" :class="{ active: snippetTab === 'flow' }" @click="snippetTab = 'flow'">拉取闭环</div>
          </div>
          <pre class="code">{{ snippets[snippetTab] }}</pre>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div class="card-title">错误码</div></div>
        <div class="card-body">
          <table>
            <tbody>
              <tr><td class="mono">missing_credentials</td><td class="small">请求未带任何凭证（401）</td></tr>
              <tr><td class="mono">invalid_api_key</td><td class="small">Key 无效或已停用（401）</td></tr>
              <tr><td class="mono">insufficient_scope</td><td class="small">权限不足（403）</td></tr>
              <tr><td class="mono">rate_limit_exceeded</td><td class="small">超出每小时配额（429）</td></tr>
              <tr><td class="mono">smtp_auth_failed</td><td class="small">SMTP 账号 / 密码错误，不可重试（502）</td></tr>
              <tr><td class="mono">smtp_connect_failed</td><td class="small">连不上 SMTP，可重试（502）</td></tr>
              <tr><td class="mono">recipient_refused</td><td class="small">收件人被服务器拒绝（502）</td></tr>
              <tr><td class="mono">validation_error</td><td class="small">参数校验失败（422）</td></tr>
              <tr><td class="mono">invalid_signature</td><td class="small">回复链接被篡改，签名校验失败（401）</td></tr>
              <tr><td class="mono">token_expired</td><td class="small">回复链接已过期，需 Agent 重发（401）</td></tr>
              <tr><td class="mono">token_revoked</td><td class="small">回复链接已被轮换吊销（401）</td></tr>
              <tr><td class="mono">task_closed</td><td class="small">任务已关闭，不能再回帖（403）</td></tr>
              <tr><td class="mono">conversation_not_found</td><td class="small">对话不存在，或不属于当前账号（404）</td></tr>
              <tr><td class="mono">reply_rate_limited</td><td class="small">该任务回帖过于频繁（429）</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>
