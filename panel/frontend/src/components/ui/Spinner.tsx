// Spinner —— 加载中
import { Loader2 } from 'lucide-react'

export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return <Loader2 className={`animate-spin text-fg-3 ${className}`} aria-label="加载中" />
}
