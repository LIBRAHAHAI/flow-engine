/**
 * Toast — 通知（轻量级自管 state 队列）
 *
 * 设计要点：
 * - 全局唯一 ToastManager，通过 createPortal 渲染到 #root 外
 * - 自动消失 3s（错误 5s）
 * - 一次最多显示 3 条，超出排入队列
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

export type ToastKind = 'ok' | 'warn' | 'err' | 'info'

interface ToastItem {
  id: string
  kind: ToastKind
  message: string
}

type Listener = (items: ToastItem[]) => void

let queue: ToastItem[] = []
const listeners = new Set<Listener>()
let counter = 0

function emit() {
  for (const fn of listeners) fn(queue)
}

function push(kind: ToastKind, message: string) {
  const id = `toast-${++counter}`
  const item: ToastItem = { id, kind, message }
  queue = [...queue, item].slice(-3)
  emit()
  const ttl = kind === 'err' ? 5000 : 3000
  window.setTimeout(() => {
    queue = queue.filter((t) => t.id !== id)
    emit()
  }, ttl)
}

export const toast = {
  ok: (msg: string) => push('ok', msg),
  warn: (msg: string) => push('warn', msg),
  err: (msg: string) => push('err', msg),
  info: (msg: string) => push('info', msg),
}

const KIND_STYLES: Record<ToastKind, { icon: string; className: string }> = {
  ok: { icon: '✓', className: 'border-emerald-500/30 bg-emerald-500/15 text-emerald-200' },
  warn: { icon: '!', className: 'border-amber-500/30 bg-amber-500/15 text-amber-200' },
  err: { icon: '✕', className: 'border-rose-500/30 bg-rose-500/15 text-rose-200' },
  info: { icon: 'i', className: 'border-slate-500/30 bg-slate-500/15 text-slate-200' },
}

function ToastList({ items }: { items: ToastItem[] }) {
  return (
    <div className="pointer-events-none fixed bottom-6 right-6 z-50 flex flex-col gap-2">
      {items.map((t) => {
        const s = KIND_STYLES[t.kind]
        return (
          <div
            key={t.id}
            className={`flex items-start gap-2 rounded-[8px] border px-4 py-3 text-sm shadow-lg backdrop-blur-sm ${s.className}`}
            role="status"
          >
            <span className="font-mono font-semibold">{s.icon}</span>
            <span className="leading-snug">{t.message}</span>
          </div>
        )
      })}
    </div>
  )
}

export function ToastMount() {
  const [items, setItems] = useState<ToastItem[]>(queue)
  useEffect(() => {
    const fn: Listener = (next) => setItems(next)
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  }, [])
  if (items.length === 0) return null
  return createPortal(<ToastList items={items} />, document.body)
}

export default ToastMount