// HistoryList —— 完整历史（可滚动，006 §2.4 底部）
import type { HistoryEntry } from '../types'
import { historySummary } from '../lib/format'

export function HistoryList({ history }: { history: HistoryEntry[] }) {
  if (history.length === 0) {
    return <p className="py-4 text-xs text-fg-3">暂无历史</p>
  }
  return (
    <div className="max-h-96 overflow-y-auto rounded-md border border-line bg-bg-3">
      <table className="w-full text-left text-xs">
        <thead className="sticky top-0 bg-bg-3">
          <tr className="text-fg-3">
            <th className="px-3 py-2 font-normal">#</th>
            <th className="px-3 py-2 font-normal">step</th>
            <th className="px-3 py-2 font-normal">action</th>
            <th className="px-3 py-2 font-normal">摘要</th>
          </tr>
        </thead>
        <tbody>
          {history.map((e, i) => (
            <tr key={i} className="border-t border-line">
              <td className="px-3 py-1.5 text-fg-3">{i + 1}</td>
              <td className="px-3 py-1.5 font-mono-id text-fg-1">{e.step}</td>
              <td className="px-3 py-1.5 text-fg-2">{e.action}</td>
              <td className="px-3 py-1.5 text-fg-2" title={historySummary(e)}>
                {historySummary(e)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
