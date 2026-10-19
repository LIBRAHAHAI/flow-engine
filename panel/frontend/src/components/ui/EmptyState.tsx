// EmptyState —— 空状态
import type { ReactNode } from 'react'
import { Inbox } from 'lucide-react'

export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-fg-3">
      <Inbox className="h-10 w-10" />
      <p className="text-sm">{title}</p>
      {hint && <p className="text-xs text-fg-3">{hint}</p>}
    </div>
  )
}
