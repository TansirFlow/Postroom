<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, loadSiteInfo, state } from '../api'
import SiteFooter from '../components/SiteFooter.vue'

const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')
const hint = ref('')

onMounted(async () => {
  await loadSiteInfo()
  if (route.query.expired) hint.value = '登录状态已失效，请重新登录'
})

async function submit() {
  error.value = ''
  if (!username.value.trim() || !password.value) {
    error.value = '请填写用户名与密码'
    return
  }
  loading.value = true
  try {
    await api.login(username.value.trim(), password.value)
    const target = typeof route.query.redirect === 'string' && route.query.redirect
      ? route.query.redirect
      : '/'
    router.replace(target)
  } catch (e) {
    error.value = e.status === 429 ? e.message : e.message || '登录失败'
    password.value = ''
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <span class="brand-logo">P</span>
        <div>
          <div class="login-title">{{ state.site.app || 'Postroom' }}</div>
          <div class="login-sub">面向 AI Agent 的工具型 API 控制台</div>
        </div>
      </div>

      <div v-if="hint" class="banner banner-warn small">{{ hint }}</div>
      <div v-if="error" class="banner banner-warn small">{{ error }}</div>

      <form class="login-form" @submit.prevent="submit">
        <div class="field">
          <label class="field-label">用户名</label>
          <input
            v-model="username"
            type="text"
            autocomplete="username"
            placeholder="admin"
            autofocus
          />
        </div>
        <div class="field">
          <label class="field-label">密码</label>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            placeholder="••••••••"
          />
        </div>
        <button class="btn btn-primary login-submit" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登录' }}
        </button>
      </form>

      <div class="login-foot small muted">
        账号由管理员在「用户管理」中创建，本系统不提供自助注册。<br />
        首次部署的账号与密码见服务器 <code>backend/data/keys.txt</code>。
      </div>
    </div>
    <SiteFooter />
  </div>
</template>
