<script setup>
import { onMounted, ref, watch } from 'vue'
import { api, fmtBytes, fmtTime, toast } from '../api'

const rows = ref([])
const total = ref(0)
const pages = ref(1)
const page = ref(1)
const pageSize = ref(20)
const statusFilter = ref('')
const keyword = ref('')
const loading = ref(false)
const detail = ref(null)

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams({ page: page.value, page_size: pageSize.value })
    if (statusFilter.value) params.set('status', statusFilter.value)
    if (keyword.value.trim()) params.set('q', keyword.value.trim())
    const data = await api.get(`/api/v1/mail/logs?${params.toString()}`)
    rows.value = data.items || []
    total.value = data.total
    pages.value = data.pages
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function openDetail(row) {
  try {
    detail.value = await api.get(`/api/v1/mail/logs/${row.id}`)
  } catch (e) {
    toast(e.message, 'error')
  }
}

let searchTimer
watch(keyword, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 320)
})
watch([statusFilter, pageSize], () => {
  page.value = 1
  load()
})
watch(page, load)

onMounted(load)
</script>

<template>
  <div class="card">
    <div class="card-head">
      <div class="card-title">发送记录</div>
      <div class="card-desc">共 {{ total }} 条</div>
      <div class="spacer" />
      <select v-model="statusFilter" style="width: 130px">
        <option value="">全部状态</option>
        <option value="sent">sent</option>
        <option value="failed">failed</option>
      </select>
      <input v-model="keyword" type="text" placeholder="搜索主题 / 收件人" style="width: 200px" />
      <button class="btn btn-sm" :disabled="loading" @click="load">{{ loading ? '加载中…' : '刷新' }}</button>
    </div>

    <table v-if="rows.length">
      <thead>
        <tr>
          <th>时间</th>
          <th>收件人</th>
          <th>主题</th>
          <th>状态</th>
          <th>耗时</th>
          <th>大小</th>
          <th>调用方</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id">
          <td class="mono muted" style="white-space: nowrap">{{ fmtTime(row.created_at) }}</td>
          <td class="mono">{{ row.to_addrs?.join(', ') }}</td>
          <td>{{ row.subject }}</td>
          <td>
            <span class="badge" :class="row.status === 'sent' ? 'badge-ok' : 'badge-err'">{{ row.status }}</span>
            <div v-if="row.error_code" class="small muted mono">{{ row.error_code }}</div>
          </td>
          <td class="mono muted">{{ row.latency_ms }}ms</td>
          <td class="mono muted">{{ fmtBytes(row.size_bytes) }}</td>
          <td class="mono muted">{{ row.api_key_name }}</td>
          <td><button class="btn btn-sm" @click="openDetail(row)">详情</button></td>
        </tr>
      </tbody>
    </table>
    <div v-else class="empty">{{ loading ? '加载中…' : '没有匹配的记录' }}</div>

    <div class="pager">
      <span>每页</span>
      <select v-model.number="pageSize" style="width: 72px">
        <option :value="10">10</option>
        <option :value="20">20</option>
        <option :value="50">50</option>
      </select>
      <button class="btn btn-sm" :disabled="page <= 1" @click="page--">上一页</button>
      <span>{{ page }} / {{ pages }}</span>
      <button class="btn btn-sm" :disabled="page >= pages" @click="page++">下一页</button>
    </div>
  </div>

  <div v-if="detail" class="modal-mask" @click.self="detail = null">
    <div class="modal" style="max-width: 720px">
      <div class="modal-head">
        邮件详情
        <span class="badge" :class="detail.status === 'sent' ? 'badge-ok' : 'badge-err'" style="margin-left: 8px">{{ detail.status }}</span>
      </div>
      <div class="modal-body">
        <dl class="kv">
          <dt>记录 ID</dt>
          <dd class="mono">{{ detail.id }}</dd>
          <dt>时间</dt>
          <dd>{{ fmtTime(detail.created_at) }}</dd>
          <dt>主题</dt>
          <dd>{{ detail.subject }}</dd>
          <dt>收件人</dt>
          <dd class="mono">{{ detail.to_addrs?.join(', ') }}</dd>
          <dt v-if="detail.cc_addrs?.length">抄送</dt>
          <dd v-if="detail.cc_addrs?.length" class="mono">{{ detail.cc_addrs.join(', ') }}</dd>
          <dt>Message-ID</dt>
          <dd class="mono">{{ detail.message_id || '-' }}</dd>
          <dt v-if="detail.error">错误</dt>
          <dd v-if="detail.error" style="color: var(--danger)">{{ detail.error }}</dd>
        </dl>
        <div v-if="detail.body_preview" style="margin-top: 14px">
          <div class="field-label">正文预览</div>
          <pre class="code">{{ detail.body_preview }}</pre>
        </div>
        <div style="margin-top: 14px">
          <div class="field-label">原始记录</div>
          <pre class="code">{{ JSON.stringify(detail, null, 2) }}</pre>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="detail = null">关闭</button>
      </div>
    </div>
  </div>
</template>
