// useHistory —— GET /flows/{id}/history 完整历史
import { useCallback, useEffect, useRef, useState } from 'react'
import type { HistoryEntry } from '../types'
import { api } from '../api/client'

interface UseHistoryResult {
  history: HistoryEntry[]
  loading: boolean
  error: string | null
  refetch: () => void
}

export function useHistory(id: string): UseHistoryResult {
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  const load = useCallback(async () => {
    try {
      const data = await api.getHistory(id)
      if (mountedRef.current) {
        setHistory(data)
        setError(null)
      }
    } catch (e) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [id])

  useEffect(() => {
    mountedRef.current = true
    void load()
    return () => {
      mountedRef.current = false
    }
  }, [load])

  return { history, loading, error, refetch: load }
}
