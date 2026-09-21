<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, fmtTime, state, toast } from '../api'

const users = ref([])
const policy = ref({ min_length: 8, rule: '' })
const loading = ref(false)
const creating = ref(false)
const showCreate = ref(false)
const created = ref(null) // 新建后展示一次性密码
const resetInfo = ref(null) // 重置密码结果

const form = reactive({ username: '', display_name: '', role: 'user', password: '', note: '' })

async function load() {
  loading.value = true
  try {
    const data = await api.get('/api/v1/users')
    users.value = data.items || []
    policy.value = data.password_policy || policy.value
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!form.username.trim()) {
    toast('请填写用户名', 'warn')
    return
  }
  creating.value = true
  try {
    const data = await api.post('/api/v1/users', {
      username: form.username.trim(),
      display_name: form.display_name.trim(),
      role: form.role,
      password: form.password || null,
      note: form.note,
    })
    created.value = data
    showCreate.value = false
    form.username = ''
    form.display_name = ''
    form.role = 'user'
    form.password = ''
    form.note = ''
    await load()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    creating.value = false
  }
}

async function resetPassword(row) {
  if (!window.confirm(`重置「${row.username}」的密码？该账号所有已登录设备会立即失效。`)) return
  try {
    resetInfo.value = await api.post(`/api/v1/users/${row.id}/password`, {})
    await load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function toggle(row) {
  try {
    await api.patch(`/api/v1/users/${row.id}`, { enabled: !row.enabled })
    toast(row.enabled ? '已停用' : '已启用')
    await load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function toggleRole(row) {
  const next = row.role === 'admin' ? 'user' : 'admin'
  if (!window.confirm(`把「${row.username}」的角色改为「${next === 'admin' ? '管理员' : '普通用户'}」？`)) return
  try {
    await api.patch(`/api/v1/users/${row.id}`, { role: next })
    toast('角色已更新')
    await load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function remove(row) {
  if (
    !window.confirm(
      `确认删除账号「${row.username}」？\n\n该账号的 API 密钥、发信记录、任务与会话消息会一并删除，不可恢复。`,
    )
  )
    return
  try {
    await api.del(`/api/v1/users/${row.id}`)
    toast('账号已删除')
    await load()
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

onMounted(load)
</script>

<template>
  <div class="card">
    <div class="card-head">
      <div class="card-title">用户管理</div>
      <div class="card-desc">
        账号只能由管理员创建；每个账号的密钥、发信记录、任务与 SMTP 设置互相隔离
      </div>
      <div class="spacer" />
      <button class="btn btn-sm" :disabled="loading" @click="load">刷新</button>
      <button class="btn btn-sm btn-primary" @click="showCreate = true">新建账号</button>
    </div>

    <table v-if="users.length">
      <thead>
        <tr>
          <th>用户名</th>
          <th>显示名</th>
          <th>角色</th>
          <th>状态</th>
          <th>最近登录</th>
          <th>创建时间</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in users" :key="row.id">
          <td>
            <span class="mono">{{ row.username }}</span>
            <span v-if="row.is_self" class="chip" style="margin-left: 6px">当前登录</span>
            <div v-if="row.note" class="small muted">{{ row.note }}</div>
          </td>
          <td>{{ row.display_name || '-' }}</td>
          <td>
            <span class="badge" :class="row.role === 'admin' ? 'badge-ok' : 'badge-mute'">
              {{ row.role === 'admin' ? '管理员' : '普通用户' }}
            </span>
          </td>
          <td>
            <span class="badge" :class="row.enabled ? 'badge-ok' : 'badge-mute'">
              {{ row.enabled ? '启用' : '停用' }}
            </span>
            <span v-if="row.must_change_password" class="badge badge-warn" style="margin-left: 4px">待改密</span>
          </td>
          <td class="mono muted">{{ row.last_login_at ? fmtTime(row.last_login_at) : '-' }}</td>
          <td class="mono muted">{{ fmtTime(row.created_at) }}</td>
          <td style="white-space: nowrap">
            <button class="btn btn-sm" @click="resetPassword(row)">重置密码</button>
            <button class="btn btn-sm" style="margin-left: 6px" @click="toggleRole(row)">
              {{ row.role === 'admin' ? '降为用户' : '升为管理员' }}
            </button>
            <button
              class="btn btn-sm"
              style="margin-left: 6px"
              :disabled="row.is_self"
              :title="row.is_self ? '不能停用当前登录的账号' : ''"
              @click="toggle(row)"
            >
              {{ row.enabled ? '停用' : '启用' }}
            </button>
            <button
              class="btn btn-sm btn-danger"
              style="margin-left: 6px"
              :disabled="row.is_self"
              :title="row.is_self ? '不能删除当前登录的账号' : ''"
              @click="remove(row)"
            >
              删除
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else class="empty">{{ loading ? '加载中…' : '暂无账号' }}</div>
  </div>

  <!-- 新建账号 -->
  <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
    <div class="modal">
      <div class="modal-head">新建账号</div>
      <div class="modal-body">
        <div class="field">
          <label class="field-label">用户名 *</label>
          <input v-model="form.username" type="text" placeholder="字母/数字/._@-，2-40 位" />
        </div>
        <div class="field">
          <label class="field-label">显示名</label>
          <input v-model="form.display_name" type="text" placeholder="留空则与用户名相同" />
        </div>
        <div class="field">
          <label class="field-label">角色</label>
          <select v-model="form.role">
            <option value="user">普通用户（只能用自己账号的数据）</option>
            <option value="admin">管理员（额外可管理账号）</option>
          </select>
        </div>
        <div class="field">
          <label class="field-label">初始密码</label>
          <input v-model="form.password" type="text" :placeholder="`留空则自动生成（${policy.rule}）`" />
          <div class="field-hint">明文密码只在创建后显示一次，请及时转交本人</div>
        </div>
        <div class="field">
          <label class="field-label">备注</label>
          <input v-model="form.note" type="text" placeholder="可选" />
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="showCreate = false">取消</button>
        <button class="btn btn-primary" :disabled="creating" @click="create">
          {{ creating ? '创建中…' : '创建' }}
        </button>
      </div>
    </div>
  </div>

  <!-- 新建后展示一次密码 -->
  <div v-if="created" class="modal-mask" @click.self="created = null">
    <div class="modal">
      <div class="modal-head">账号已创建 · 请立即保存密码</div>
      <div class="modal-body">
        <div class="banner banner-warn">明文密码只显示这一次，关闭后无法再次查看（可随时「重置密码」）。</div>
        <div class="field">
          <label class="field-label">用户名</label>
          <pre class="code">{{ created.user.username }}</pre>
        </div>
        <div class="field">
          <label class="field-label">初始密码</label>
          <pre class="code">{{ created.initial_password }}</pre>
        </div>
        <div class="small muted">
          {{ created.user.is_admin ? '该账号为管理员' : '该账号为普通用户' }}，
          首次登录后可在「系统设置」修改密码并配置自己的 SMTP。
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="copy(created.initial_password)">复制密码</button>
        <button class="btn btn-primary" @click="created = null">我已保存</button>
      </div>
    </div>
  </div>

  <!-- 重置密码结果 -->
  <div v-if="resetInfo" class="modal-mask" @click.self="resetInfo = null">
    <div class="modal">
      <div class="modal-head">密码已重置 · 请立即保存</div>
      <div class="modal-body">
        <div class="banner banner-warn">
          「{{ resetInfo.username }}」所有已登录设备已失效；新密码只显示这一次。
        </div>
        <div class="field">
          <label class="field-label">新密码</label>
          <pre class="code">{{ resetInfo.new_password }}</pre>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="copy(resetInfo.new_password)">复制密码</button>
        <button class="btn btn-primary" @click="resetInfo = null">我已保存</button>
      </div>
    </div>
  </div>
</template>
