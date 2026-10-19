// 运行时配置（API 地址 / 轮询间隔）—— Settings 页可改，持久化到 localStorage

const KEY_API_BASE = 'flow-engine:api-base'
const KEY_REFRESH_MS = 'flow-engine:refresh-ms'

export const DEFAULT_API_BASE = 'http://localhost:8650'
export const DEFAULT_REFRESH_MS = 10_000

export function getApiBase(): string {
  try {
    const v = localStorage.getItem(KEY_API_BASE)
    if (v) return v.replace(/\/+$/, '')
  } catch {
    /* localStorage 不可用时忽略 */
  }
  return DEFAULT_API_BASE
}

export function setApiBase(url: string): void {
  try {
    localStorage.setItem(KEY_API_BASE, url.replace(/\/+$/, ''))
  } catch {
    /* ignore */
  }
}

export function getRefreshMs(): number {
  try {
    const v = Number(localStorage.getItem(KEY_REFRESH_MS))
    if (Number.isFinite(v) && v >= 3000) return v
  } catch {
    /* ignore */
  }
  return DEFAULT_REFRESH_MS
}

export function setRefreshMs(ms: number): void {
  try {
    localStorage.setItem(KEY_REFRESH_MS, String(ms))
  } catch {
    /* ignore */
  }
}
