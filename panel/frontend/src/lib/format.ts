// 展示辅助：历史条目摘要 + 相对时间格式化
import type { HistoryEntry } from '../types'

/** 从历史条目提取一行可读摘要 */
export function historySummary(e: HistoryEntry): string {
  if (e.action === 'decision' && e.decision) return `拍板：${e.decision}`
  if (e.detail) return e.detail
  if (e.log) return e.log
  if (typeof e.passed === 'boolean') return `passed=${e.passed}`
  return e.action
}

/** 历史条目的语义色调（决定时间线圆点颜色） */
export type HistoryTone = 'decision' | 'success' | 'error' | 'info' | 'neutral'

export function historyTone(e: HistoryEntry): HistoryTone {
  if (e.action === 'decision') return 'decision'
  if (e.passed === true) return 'success'
  if (e.passed === false) return 'error'
  if (e.action === 'dispatch') return 'info'
  return 'neutral'
}

/** ISO 时间 → 相对时间（中文） */
export function formatTime(iso: string | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const diff = Date.now() - d.getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return d.toLocaleString('zh-CN', { hour12: false })
}
