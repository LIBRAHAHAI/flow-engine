// DecisionBanner —— 待拍板顶部金色提示条（006 §3.1 L1）
import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import type { FlowSummary } from '../types'

export function DecisionBanner({ pending }: { pending: FlowSummary[] }) {
  if (pending.length === 0) return null
  return (
    <div className="mb-5 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3">
      <div className="flex items-center gap-2 text-sm font-medium text-warning">
        <AlertTriangle className="h-4 w-4" />
        有 {pending.length} 个流程待拍板
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {pending.map((f) => (
          <Link
            key={f.id}
            to={`/flow/${f.id}`}
            className="rounded-md bg-warning/15 px-3 py-1.5 text-xs text-warning transition-colors hover:bg-warning/25"
          >
            📌 {f.title} → 拍板
          </Link>
        ))}
      </div>
    </div>
  )
}
