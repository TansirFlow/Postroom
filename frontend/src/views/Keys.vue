<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, fmtTime, toast } from '../api'

const keys = ref([])
const scopes = ref([])
const loading = ref(false)
const creating = ref(false)
const showCreate = ref(false)
const createdKey = ref(null)
const form = reactive({ name: '', scopes: ['mail:send', 'mail:read'], note: '' })
const revealed = ref({})

async function load() {
  loading.value = true
  try {
    const data = await api.get('/api/v1/keys')
    keys.value = data.items || []
    scopes.value = data.available_scopes || []
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

function toggleScope(scope) {
  const i = form.scopes.indexOf(scope)
  if (i > -1) form.scopes.splice(i, 1)
  else form.scopes.push(scope)
}

async function create() {
  if (!form.name.trim()) {
    toast('请填写密钥名称', 'warn')
    return
  }
  if (!form.scopes.length) {
    toast('至少选择一个权限', 'warn')
    return
  }
  creating.value = true
  try {
    createdKey.value = await api.post('/api/v1/keys', {
      name: form.name.trim(),
      scopes: form.scopes,
      note: form.note,
    })
    showCreate.value = false
    form.name = ''
    form.note = ''
    await load()
    toast('密钥已创建')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    creating.value = false
  }
}

async function toggleKey(row) {
  try {
    await api.patch(`/api/v1/keys/${row.id}?enabled=${!row.enabled}`)
    toast(row.enabled ? '已停用' : '已启用')
    load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function removeKey(row) {
  if (!window.confirm(`确认删除密钥「${row.name}」？删除后使用该密钥的 Agent 将立即失效。`)) return
  try {
    await api.del(`/api/v1/keys/${row.id}`)
    toast('已删除')
    load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    toast('已复制到剪贴板')
  } catch (e) {
    toast('复制失败，请手动选择文本', 'warn')
  }
}

async function copyAndClose(text) {
  await copy(text)
  createdKey.value = null
}

onMounted(load)
</script>

<template>
  <div class="card">
    <div class="card-head">
      <div class="card-title">API 密钥</div>
      <div class="card-desc">Agent 使用 X-API-Key 头访问接口</div>
      <div class="spacer" />
      <button class="btn btn-sm" :disabled="loading" @click="load">刷新</button>
      <button class="btn btn-sm btn-primary" @click="showCreate = true">新建密钥</button>
    </div>

    <table v-if="keys.length">
      <thead>
        <tr>
          <th>名称</th>
          <th>前缀</th>
          <th>权限</th>
          <th>状态</th>
          <th>调用次数</th>
          <th>最近使用</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in keys" :key="row.id">
          <td>
            {{ row.name }}
            <div v-if="row.note" class="small muted">{{ row.note }}</div>
          </td>
          <td class="mono muted">{{ row.prefix }}…</td>
          <td>
            <span v-for="s in row.scopes" :key="s" class="chip" style="margin: 1px 3px 1px 0">{{ s }}</span>
          </td>
          <td>
            <span class="badge" :class="row.enabled ? 'badge-ok' : 'badge-mute'">
              {{ row.enabled ? '启用' : '停用' }}
            </span>
          </td>
          <td class="mono">{{ row.call_count }}</td>
          <td class="mono muted">{{ row.last_used_at ? fmtTime(row.last_used_at) : '-' }}</td>
          <td style="white-space: nowrap">
            <button class="btn btn-sm" @click="toggleKey(row)">{{ row.enabled ? '停用' : '启用' }}</button>
            <button class="btn btn-sm btn-danger" style="margin-left: 6px" @click="removeKey(row)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else class="empty">{{ loading ? '加载中…' : '暂无密钥' }}</div>

    <div class="card-body" style="border-top: 1px solid var(--border); background: var(--panel-2)">
      <div class="small muted">
        权限说明：
        <span v-for="s in scopes" :key="s.scope" class="chip" style="margin: 2px 4px 2px 0">
          <b>{{ s.scope }}</b> · {{ s.label }}
        </span>
      </div>
    </div>
  </div>

  <!-- 新建密钥 -->
  <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
    <div class="modal">
      <div class="modal-head">新建 API 密钥</div>
      <div class="modal-body">
        <div class="field">
          <label class="field-label">名称 *</label>
          <input v-model="form.name" type="text" placeholder="如 report-agent" />
        </div>
        <div class="field">
          <label class="field-label">权限 *</label>
          <div class="chips">
            <span
              v-for="s in scopes"
              :key="s.scope"
              class="chip"
              :class="{ on: form.scopes.includes(s.scope) }"
              @click="toggleScope(s.scope)"
            >
              {{ s.scope }}
            </span>
          </div>
        </div>
        <div class="field">
          <label class="field-label">备注</label>
          <input v-model="form.note" type="text" placeholder="可选" />
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="showCreate = false">取消</button>
        <button class="btn btn-primary" :disabled="creating" @click="create">{{ creating ? '创建中…' : '创建' }}</button>
      </div>
    </div>
  </div>

  <!-- 新建后展示一次明文 -->
  <div v-if="createdKey" class="modal-mask" @click.self="createdKey = null">
    <div class="modal">
      <div class="modal-head">密钥已创建 · 请立即保存</div>
      <div class="modal-body">
        <div class="banner banner-warn">明文密钥只显示这一次，关闭后无法再次查看。</div>
        <div class="field">
          <label class="field-label">{{ createdKey.name }}</label>
          <pre class="code">{{ createdKey.api_key }}</pre>
        </div>
        <div class="row">
          <button class="btn btn-primary" @click="copy(createdKey.api_key)">复制密钥</button>
          <button class="btn" @click="copy(createdKey.api_key)">复制并关闭</button>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="createdKey = null">我已保存</button>
      </div>
    </div>
  </div>
</template>
