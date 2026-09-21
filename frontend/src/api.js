/**
 * API 客户端：统一处理 {ok, data, error, request_id} 包裹、会话令牌注入、错误抛出。
 *
 * 鉴权模型（多用户）：
 * - 人在网页 → 账号密码登录拿到会话令牌，存 localStorage，随请求以
 *   `Authorization: Bearer <token>` 发送；
 * - Agent/脚本 → 用 API Key（`X-API-Key`），不走这里。
 * 令牌失效（401）时自动清空本地状态并跳回登录页。
 */
import { reactive } from 'vue'

const TOKEN_STORAGE = 'postroom_token'
const USER_STORAGE = 'postroom_user'

function readUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_STORAGE) || 'null')
  } catch (e) {
    return null
  }
}

export const state = reactive({
  token: localStorage.getItem(TOKEN_STORAGE) || '',
  user: readUser(),
  toasts: [],
  health: { ok: null, latency: null, checkedAt: null },
  // 站点展示信息（应用名 / 备案号），由 /api/v1/site 填充
  site: { app: '', version: '', icp: '', icpUrl: '' },
})

/** 401 时的回调，由 router 注册（避免循环依赖） */
export const authEvents = { onUnauthorized: null }

export function setSession(token, user) {
  state.token = (token || '').trim()
  state.user = user || null
  if (state.token) localStorage.setItem(TOKEN_STORAGE, state.token)
  else localStorage.removeItem(TOKEN_STORAGE)
  if (state.user) localStorage.setItem(USER_STORAGE, JSON.stringify(state.user))
  else localStorage.removeItem(USER_STORAGE)
}

export function clearSession() {
  setSession('', null)
}

export const isLoggedIn = () => Boolean(state.token)
export const isAdmin = () => Boolean(state.user && state.user.is_admin)

export function toast(message, type = 'success', timeout = 4200) {
  const id = Math.random().toString(36).slice(2)
  state.toasts.push({ id, message, type })
  setTimeout(() => {
    const i = state.toasts.findIndex((t) => t.id === id)
    if (i > -1) state.toasts.splice(i, 1)
  }, timeout)
}

export class ApiError extends Error {
  constructor(message, code, status, detail) {
    super(message)
    this.code = code
    this.status = status
    this.detail = detail
  }
}

async function request(path, { method = 'GET', body, raw = false, auth = true } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth && state.token) headers['Authorization'] = `Bearer ${state.token}`

  const started = performance.now()
  let res
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (e) {
    throw new ApiError('无法连接到 API 服务，请确认后端已启动', 'network_error', 0)
  }
  const latency = Math.round(performance.now() - started)

  let json = null
  try {
    json = await res.json()
  } catch (e) {
    json = null
  }

  if (res.status === 401 && auth && state.token) {
    // 令牌过期 / 被吊销 / 账号被停用 → 清掉本地登录态并回登录页
    clearSession()
    if (authEvents.onUnauthorized) authEvents.onUnauthorized()
  }

  if (json && json.ok === false) {
    const err = json.error || {}
    throw new ApiError(err.message || `请求失败 (${res.status})`, err.code || 'error', res.status, err)
  }
  if (!res.ok) {
    throw new ApiError(`请求失败 (${res.status})`, `http_${res.status}`, res.status)
  }
  if (raw) return { data: json?.data, latency, requestId: json?.request_id }
  return json?.data
}

export const api = {
  get: (p) => request(p),
  post: (p, body) => request(p, { method: 'POST', body }),
  put: (p, body) => request(p, { method: 'PUT', body }),
  patch: (p, body) => request(p, { method: 'PATCH', body }),
  del: (p) => request(p, { method: 'DELETE' }),
  raw: (p, opts) => request(p, opts),

  /** 账号密码登录；成功后写入本地会话 */
  async login(username, password) {
    const data = await request('/api/v1/auth/login', {
      method: 'POST',
      auth: false,
      body: { username, password },
    })
    setSession(data?.token, data?.user)
    return data
  },

  /** 校验当前会话是否仍有效，并刷新用户信息 */
  async me() {
    const data = await request('/api/v1/auth/me')
    if (data?.user) {
      state.user = data.user
      localStorage.setItem(USER_STORAGE, JSON.stringify(data.user))
    }
    return data
  },

  async logout() {
    try {
      await request('/api/v1/auth/logout', { method: 'POST', auth: false })
    } catch (e) {
      /* 令牌无状态，登出失败也不影响本地清理 */
    }
    clearSession()
  },

  /** 健康检查，供顶栏状态灯使用 */
  async ping() {
    const { data, latency } = await request('/api/v1/health', { raw: true, auth: false })
    state.health = {
      ok: data?.status === 'ok',
      latency,
      smtp: data?.smtp_configured,
      version: data?.version,
      checkedAt: new Date().toLocaleTimeString('zh-CN'),
    }
    return data
  },
}

/** 站点信息（应用名 / ICP 备案号）。公开接口，未登录也能拿到。 */
export async function loadSiteInfo() {
  try {
    const data = await request('/api/v1/site', { auth: false })
    state.site = {
      app: data?.app || '',
      version: data?.version || '',
      icp: data?.icp_license || '',
      icpUrl: data?.icp_license_url || 'https://beian.miit.gov.cn/',
    }
  } catch (e) {
    /* 页脚属于锦上添花，拿不到就静默不显示 */
  }
  return state.site
}

export function fmtTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

export function fmtBytes(n) {
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

/** 免登录回复页专用：链接本身即凭证，不携带任何登录态。 */
export const replyApi = {
  open: (token) => request(`/api/v1/reply/${encodeURIComponent(token)}`, { auth: false }),
  poll: (token, afterId, waitSeconds = 25) => {
    const p = new URLSearchParams()
    if (afterId) p.set('after_id', afterId)
    if (waitSeconds) p.set('wait_seconds', waitSeconds)
    return request(`/api/v1/reply/${encodeURIComponent(token)}/messages?${p}`, { auth: false })
  },
  send: (token, content, author) =>
    request(`/api/v1/reply/${encodeURIComponent(token)}`, {
      method: 'POST',
      auth: false,
      body: { content, author: author || undefined },
    }),
}

/** 相对时间：几秒前 / 几分钟前 */
export function timeAgo(iso) {
  if (!iso) return '-'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return iso
  const diff = Math.floor((Date.now() - t) / 1000)
  if (diff < 10) return '刚刚'
  if (diff < 60) return `${diff} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`
  return fmtTime(iso)
}

export function fmtClock(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
}
