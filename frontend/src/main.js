import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { setApiKey } from './api'
import './styles.css'

// 便捷入口：访问 /?key=sk-agent-xxx 可直接把密钥写入本地存储并跳回干净地址
const params = new URLSearchParams(window.location.search)
const urlKey = params.get('key') || params.get('api_key')
if (urlKey) {
  setApiKey(urlKey)
  params.delete('key')
  params.delete('api_key')
  const qs = params.toString()
  window.history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''))
}

createApp(App).use(router).mount('#app')
