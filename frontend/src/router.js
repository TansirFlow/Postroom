import { createRouter, createWebHistory } from 'vue-router'

import Dashboard from './views/Dashboard.vue'
import Compose from './views/Compose.vue'
import Logs from './views/Logs.vue'
import Keys from './views/Keys.vue'
import ApiDocs from './views/ApiDocs.vue'
import Tasks from './views/Tasks.vue'
import Reply from './views/Reply.vue'
import Login from './views/Login.vue'
import Settings from './views/Settings.vue'
import Users from './views/Users.vue'

import { authEvents, clearSession, isAdmin, isLoggedIn, state } from './api'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    // 登录页：独立整页布局，未登录可访问
    { path: '/login', name: 'login', component: Login, meta: { bare: true, public: true, title: '登录' } },
    { path: '/', name: 'dashboard', component: Dashboard, meta: { title: '概览' } },
    { path: '/compose', name: 'compose', component: Compose, meta: { title: '发信工作台' } },
    { path: '/tasks', name: 'tasks', component: Tasks, meta: { title: '任务会话' } },
    { path: '/tasks/:id', name: 'task-detail', component: Tasks, meta: { title: '任务会话' } },
    { path: '/logs', name: 'logs', component: Logs, meta: { title: '发送记录' } },
    { path: '/keys', name: 'keys', component: Keys, meta: { title: 'API 密钥' } },
    { path: '/settings', name: 'settings', component: Settings, meta: { title: '系统设置' } },
    { path: '/users', name: 'users', component: Users, meta: { title: '用户管理', adminOnly: true } },
    { path: '/docs', name: 'api-docs', component: ApiDocs, meta: { title: '接口文档' } },
    // 免登录回复页：独立整页布局，链接本身即凭证
    { path: '/reply/:token', name: 'reply', component: Reply, meta: { bare: true, public: true, title: '对话' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  // 公开页（登录页、回复页）直接放行
  if (to.meta.public) {
    // 已登录还去登录页 → 直接回控制台
    if (to.name === 'login' && isLoggedIn()) return { path: '/' }
    return true
  }
  if (!isLoggedIn()) {
    return { path: '/login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }
  // 只有在「已知当前用户不是管理员」时才拦；身份尚未拉取时交给接口判权限
  if (to.meta.adminOnly && state.user && !isAdmin()) {
    return { path: '/', query: { denied: 'admin' } }
  }
  return true
})

// 后端返回 401（令牌过期 / 被吊销 / 账号停用）时自动回登录页
authEvents.onUnauthorized = () => {
  clearSession()
  const current = router.currentRoute.value
  if (current.meta?.public) return
  router.replace({ path: '/login', query: { redirect: current.fullPath, expired: '1' } })
}
