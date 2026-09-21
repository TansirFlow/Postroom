<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, setApiKey, state, toast } from './api'

const route = useRoute()
const keyInput = ref(state.apiKey)
const showKeyModal = ref(false)
const pinging = ref(false)
const taskStats = ref({ waiting_reply_tasks: 0, open: 0 })

const isBare = computed(() => Boolean(route.meta.bare))

const navGroups = [
  {
    label: '能力模块',
    items: [
      { path: '/', icon: '◈', text: '概览' },
      { path: '/compose', icon: '✉', text: '发信工作台' },
      { path: '/tasks', icon: '💬', text: '任务会话', badge: () => taskStats.value.waiting_reply_tasks },
      { path: '/logs', icon: '☰', text: '发送记录' },
    ],
  },
  {
    label: '开发者',
    items: [
      { path: '/docs', icon: '{ }', text: '接口文档 / Agent 工具' },
      { path: '/keys', icon: '🔑', text: 'API 密钥' },
    ],
  },
]

const pageTitle = computed(() => route.meta.title || '控制台')
const keyMasked = computed(() =>
  state.apiKey ? `${state.apiKey.slice(0, 10)}…${state.apiKey.slice(-4)}` : '未配置',
)
const healthText = computed(() => {
  if (state.health.ok === null) return '检测中…'
  return state.health.ok ? `在线 · ${state.health.latency}ms` : '离线'
})

function isActive(path) {
  if (path === '/') return route.path === '/'
  return route.path === path || route.path.startsWith(path + '/')
}

async function ping() {
  pinging.value = true
  try {
    await api.ping()
  } catch (e) {
    state.health = { ok: false, latency: null, checkedAt: new Date().toLocaleTimeString('zh-CN') }
  } finally {
    pinging.value = false
  }
}

async function loadTaskStats() {
  if (!state.apiKey) return
  try {
    const data = await api.get('/api/v1/tasks?page=1&page_size=1')
    taskStats.value = data.stats || {}
  } catch (e) {
    /* 忽略：未配置 key 或无权限 */
  }
}

function saveKey() {
  setApiKey(keyInput.value)
  showKeyModal.value = false
  toast(state.apiKey ? 'API Key 已保存' : '已清除 API Key')
  ping()
  loadTaskStats()
}

function openKeyModal() {
  keyInput.value = state.apiKey
  showKeyModal.value = true
}

let timer
onMounted(() => {
  ping()
  loadTaskStats()
  timer = setInterval(() => {
    ping()
    loadTaskStats()
  }, 45000)
})
onUnmounted(() => clearInterval(timer))

watch(() => route.path, loadTaskStats)
</script>

<template>
  <!-- 免登录回复页：整页独立布局 -->
  <router-view v-if="isBare" />

  <div v-else class="layout">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-title"><span class="brand-logo">P</span> Postroom</div>
        <div class="brand-sub">
          <span class="dot" :class="state.health.ok === false ? 'err' : state.health.ok ? '' : 'off'" />
          {{ healthText }}
        </div>
      </div>

      <nav class="nav">
        <template v-for="group in navGroups" :key="group.label">
          <div class="nav-label">{{ group.label }}</div>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            class="nav-item"
            :class="{ active: isActive(item.path) }"
          >
            <span class="ico">{{ item.icon }}</span>{{ item.text }}
            <span v-if="item.badge && item.badge() > 0" class="nav-badge">{{ item.badge() }}</span>
          </router-link>
        </template>
      </nav>

      <div class="sidebar-foot">
        SMTP · 邮件通道<br />
        <router-link to="/docs">控制台文档</router-link> · <a href="/api/docs" target="_blank">Swagger</a>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="page-title">{{ pageTitle }}</div>
        <div class="spacer" />
        <button class="btn btn-sm" :disabled="pinging" :title="state.health.checkedAt || ''" @click="ping">
          {{ pinging ? '检测中…' : '刷新状态' }}
        </button>
        <button class="btn btn-sm" @click="openKeyModal">
          <span class="dot" :class="state.apiKey ? '' : 'off'" />
          <span class="mono">{{ keyMasked }}</span>
        </button>
      </header>

      <main class="content">
        <div v-if="!state.apiKey" class="banner banner-warn">
          <span>尚未配置 API Key，页面数据无法加载。</span>
          <button class="btn btn-sm" @click="openKeyModal">立即配置</button>
          <span class="small">首次启动的密钥会打印在后端控制台，并保存在 backend/data/keys.txt</span>
        </div>
        <router-view />
      </main>
    </div>

    <div class="toasts">
      <div v-for="t in state.toasts" :key="t.id" class="toast" :class="t.type">{{ t.message }}</div>
    </div>

    <div v-if="showKeyModal" class="modal-mask" @click.self="showKeyModal = false">
      <div class="modal">
        <div class="modal-head">API Key 配置</div>
        <div class="modal-body">
          <div class="field">
            <label class="field-label">API Key</label>
            <input v-model="keyInput" type="password" placeholder="sk-agent-xxxxxxxx" @keyup.enter="saveKey" />
            <div class="field-hint">
              仅保存在浏览器 localStorage，随每个请求以 <code>X-API-Key</code> 头发送。可在「API 密钥」页新建或吊销。
            </div>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showKeyModal = false">取消</button>
          <button class="btn btn-primary" @click="saveKey">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
