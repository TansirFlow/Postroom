<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, state, toast } from './api'
import SiteFooter from './components/SiteFooter.vue'

const route = useRoute()
const router = useRouter()
const pinging = ref(false)
const taskStats = ref({ waiting_reply_tasks: 0, open: 0 })
const menuOpen = ref(false)

const isBare = computed(() => Boolean(route.meta.bare))
const isAdmin = computed(() => Boolean(state.user && state.user.is_admin))

const navGroups = computed(() => {
  const groups = [
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
    {
      label: '账号',
      items: [
        { path: '/settings', icon: '⚙', text: '系统设置' },
        ...(isAdmin.value ? [{ path: '/users', icon: '👥', text: '用户管理' }] : []),
      ],
    },
  ]
  return groups
})

const pageTitle = computed(() => route.meta.title || '控制台')
const displayName = computed(
  () => (state.user && (state.user.display_name || state.user.username)) || '未登录',
)
const roleLabel = computed(() => (isAdmin.value ? '管理员' : '普通用户'))
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
  if (!state.token) return
  try {
    const data = await api.get('/api/v1/tasks?page=1&page_size=1')
    taskStats.value = data.stats || {}
  } catch (e) {
    /* 忽略：无权限或接口不可用 */
  }
}

async function refreshMe() {
  if (!state.token) return
  try {
    await api.me()
  } catch (e) {
    /* 401 已由 api.js 统一处理 */
  }
}

async function logout() {
  menuOpen.value = false
  await api.logout()
  toast('已退出登录')
  router.replace('/login')
}

function toggleMenu() {
  menuOpen.value = !menuOpen.value
}

function closeMenu() {
  menuOpen.value = false
}

let timer
onMounted(async () => {
  await Promise.all([ping(), loadTaskStats(), refreshMe()])
  if (route.query.denied === 'admin') toast('该页面仅管理员可访问', 'warn')
  timer = setInterval(() => {
    ping()
    loadTaskStats()
  }, 45000)
  document.addEventListener('click', closeMenu)
})
onUnmounted(() => {
  clearInterval(timer)
  document.removeEventListener('click', closeMenu)
})

watch(() => route.path, loadTaskStats)
</script>

<template>
  <!-- 登录页 / 免登录回复页：整页独立布局 -->
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
        <div class="user-menu">
          <button class="btn btn-sm user-btn" @click.stop="toggleMenu">
            <span class="avatar">{{ displayName.slice(0, 1).toUpperCase() }}</span>
            <span class="user-name">{{ displayName }}</span>
            <span class="chip chip-role">{{ roleLabel }}</span>
          </button>
          <div v-if="menuOpen" class="dropdown" @click.stop>
            <div class="dropdown-head">
              <div class="user-name">{{ displayName }}</div>
              <div class="small muted mono">{{ state.user?.username }}</div>
            </div>
            <router-link class="dropdown-item" to="/settings" @click="closeMenu">⚙ 系统设置</router-link>
            <router-link v-if="isAdmin" class="dropdown-item" to="/users" @click="closeMenu">👥 用户管理</router-link>
            <button class="dropdown-item danger" @click="logout">↪ 退出登录</button>
          </div>
        </div>
      </header>

      <main class="content">
        <router-view />
        <SiteFooter />
      </main>
    </div>

    <div class="toasts">
      <div v-for="t in state.toasts" :key="t.id" class="toast" :class="t.type">{{ t.message }}</div>
    </div>
  </div>
</template>
