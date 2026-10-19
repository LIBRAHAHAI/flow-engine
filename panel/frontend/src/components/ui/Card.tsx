// Card —— 卡片容器（统一圆角 10px / 边框 / 底色，006 §5.3）
import type { ReactNode } from 'react'

export function Card({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={`rounded-[10px] border border-line bg-bg-2 ${className}`}>
      {children}
    </div>
  )
}
