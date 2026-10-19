// useFlow —— GET /flows/{id} 详情，按需拉取
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FlowDetail } from '../types'
import { api } from '../api/client'

interface UseFlowResult {
  flow: FlowDetail | null
  loading: boolean
  error: string | null
  refetch: () => void
}

export function useFlow(id: string): UseFlowResult {
  const [flow, setFlow] = useState<FlowDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.getFlow(id)
      if (mountedRef.current) {
        setFlow(data)
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

  return { flow, loading, error, refetch: load }
}
