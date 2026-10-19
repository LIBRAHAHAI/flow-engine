// Toast —— 轻量通知（拍板成功/失败、刷新提示），context 驱动，3.5s 自动消失
import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from 'react'
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react'

type ToastType = 'success' | 'error' | 'warning' | 'info'

interface ToastItem {
  id: number
  type: ToastType
  message: string
}

interface ToastContextValue {
  toast: (message: string, type?: ToastType) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast 必须在 ToastProvider 内使用')
  return ctx
}

const ICONS: Record<ToastType, { icon: ReactNode; cls: string }> = {
  success: { icon: <CheckCircle2 className="h-4 w-4" />, cls: 'text-success' },
  error: { icon: <XCircle className="h-4 w-4" />, cls: 'text-error' },
  warning: { icon: <AlertTriangle className="h-4 w-4" />, cls: 'text-warning' },
  info: { icon: <Info className="h-4 w-4" />, cls: 'text-accent' },
}

function ToastView({ item }: { item: ToastItem }) {
  const { icon, cls } = ICONS[item.type]
  return (
    <div className="flex items-center gap-2 rounded-md border border-line bg-bg-3 px-4 py-2.5 text-sm text-fg-1 shadow-lg">
      <span className={cls}>{icon}</span>
      <span>{item.message}</span>
    </div>
  )
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const toast = useCallback((message: string, type: ToastType = 'info') => {
    const id = Date.now() + Math.random()
    setItems((prev) => [...prev, { id, type, message }])
    setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id))
    }, 3500)
  }, [])

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {items.map((item) => (
          <ToastView key={item.id} item={item} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}
