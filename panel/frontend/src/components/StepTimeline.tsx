// StepTimeline —— 垂直步骤时间线（history 倒序渲染，006 §3.2）
import type { HistoryEntry } from '../types'
import { historySummary, historyTone, type HistoryTone } from '../lib/format'

const DOT: Record<HistoryTone, string> = {
  decision: 'bg-warning',
  success: 'bg-success',
  error: 'bg-error',
  info: 'bg-accent',
  neutral: 'bg-fg-3',
}

export function StepTimeline({
  history,
  limit = 6,
}: {
  history: HistoryEntry[]
  limit?: number
}) {
  const entries = history.slice(-limit).reverse() // 最近的在最上面
  if (entries.length === 0) {
    return <p className="py-4 text-xs text-fg-3">暂无历史</p>
  }
  return (
    <div>
      {entries.map((e, i) => (
        <div key={i} className="relative flex gap-3 pb-4 last:pb-0">
          {i < entries.length - 1 && (
            <span className="absolute left-[4.5px] top-4 h-full w-px bg-line" />
          )}
          <span
            className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${DOT[historyTone(e)]}`}
          />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono-id text-xs text-fg-1">{e.step}</span>
              <span className="text-xs text-fg-3">{e.action}</span>
            </div>
            <p className="mt-0.5 truncate text-xs text-fg-2" title={historySummary(e)}>
              {historySummary(e)}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}
