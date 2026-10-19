// useFlows —— GET /flows 列表，10s 轮询 + 切后台暂停 + 错误自动重试一次（006 §6.4）
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FlowSummary } from '../types'
import { api, ApiError } from '../api/client'
import { getRefreshMs } from '../api/config'

interface UseFlowsResult {
  flows: FlowSummary[]
  loading: boolean
  error: string | null
  refetch: () => void
}

export function useFlows(): UseFlowsResult {
  const [flows, setFlows] = useState<FlowSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  const load = useCallback(async () => {
    try {
      const data = await api.listFlows()
      if (!mountedRef.current) return
      setFlows(data)
      setError(null)
    } catch (e) {
      if (!mountedRef.current) return
      // 5xx / 网络错误自动重试一次（间隔 3s）；4xx 不重试
      if (e instanceof ApiError && e.status >= 500) {
        setTimeout(async () => {
          try {
            const data = await api.listFlows()
            if (mountedRef.current) {
              setFlows(data)
              setError(null)
            }
          } catch {
            /* 重试仍失败，保留错误 */
          }
        }, 3000)
      } else if (e instanceof ApiError && e.status === 0) {
        setTimeout(async () => {
          try {
            const data = await api.listFlows()
            if (mountedRef.current) {
              setFlows(data)
              setError(null)
            }
          } catch {
            /* ignore */
          }
        }, 3000)
      }
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    void load()

    let timer: ReturnType<typeof setTimeout> | null = null
    const schedule = () => {
      timer = setTimeout(() => {
        // 标签页隐藏时暂停轮询（006 §6.4）
        if (document.visibilityState === 'hidden') {
          schedule()
          return
        }
        void load().finally(schedule)
      }, getRefreshMs())
    }
    schedule()

    return () => {
      mountedRef.current = false
      if (timer) clearTimeout(timer)
    }
  }, [load])

  return { flows, loading, error, refetch: load }
}
