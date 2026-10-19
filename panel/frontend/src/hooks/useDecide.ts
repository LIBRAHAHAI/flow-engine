// useDecide —— POST /flows/{id}/decide 拍板（乐观更新由调用方处理）
import { useCallback, useState } from 'react'
import { api } from '../api/client'

interface UseDecideResult {
  deciding: boolean
  error: string | null
  decide: (id: string, decision: string) => Promise<boolean>
}

export function useDecide(): UseDecideResult {
  const [deciding, setDeciding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const decide = useCallback(async (id: string, decision: string): Promise<boolean> => {
    setDeciding(true)
    setError(null)
    try {
      await api.decide(id, decision)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    } finally {
      setDeciding(false)
    }
  }, [])

  return { deciding, error, decide }
}
