import { createRouter, createWebHistory } from 'vue-router'

import Dashboard from './views/Dashboard.vue'
import Compose from './views/Compose.vue'
import Logs from './views/Logs.vue'
import Keys from './views/Keys.vue'
import ApiDocs from './views/ApiDocs.vue'
import Tasks from './views/Tasks.vue'
import Reply from './views/Reply.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Dashboard, meta: { title: '概览' } },
    { path: '/compose', name: 'compose', component: Compose, meta: { title: '发信工作台' } },
    { path: '/tasks', name: 'tasks', component: Tasks, meta: { title: '任务会话' } },
    { path: '/tasks/:id', name: 'task-detail', component: Tasks, meta: { title: '任务会话' } },
    { path: '/logs', name: 'logs', component: Logs, meta: { title: '发送记录' } },
    { path: '/keys', name: 'keys', component: Keys, meta: { title: 'API 密钥' } },
    { path: '/docs', name: 'api-docs', component: ApiDocs, meta: { title: '接口文档' } },
    // 免登录回复页：独立整页布局，不需要 API Key
    { path: '/reply/:token', name: 'reply', component: Reply, meta: { bare: true, title: '对话' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
