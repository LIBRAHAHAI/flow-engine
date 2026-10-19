/**
 * Logs — 日志查看页（006 §3.5）
 *
 * 路由 /logs
 *
 * 结构：
 *   顶部 FlowPicker（下拉选流程，复用 useFlows）
 *   主体 倒序日志列表（每条：时间 + level + 来源 + 消息）
 *   自动刷新：5s 一次（标签页隐藏暂停，沿用 useFlows 模式）
 *
 * 空状态：未选流程 → 「请选择流程」；选了但无 log → 「暂无日志」
 */
import { useEffect, useRef, useState } from 'react'
import { ScrollText } from 'lucide-react'
import { useFlows } from '../hooks/useFlows'
import { api, ApiError } from '../api/client'
import { Spinner } from '../components/ui/Spinner'
import { EmptyState } from '../components/ui/EmptyState'
import type { LogEntry, LogLevel } from '../types'

const REFRESH_MS = 5_000

const LEVEL_CLS: Record<LogLevel, string> = {
  info: 'bg-accent/15 text-accent ring-1 ring-accent/30',
  warn: 'bg-warning/15 text-warning ring-1 ring-warning/30',
  error: 'bg-error/15 text-error ring-1 ring-error/30',
  debug: 'bg-fg-3/15 text-fg-2 ring-1 ring-fg-3/30',
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

export default function Logs() {
  const { flows } = useFlows()
  const [selectedId, setSelectedId] = useState<string>('')
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  // 选中流程变化 → 立即拉一次
  useEffect(() => {
    mountedRef.current = true
    if (!selectedId) {
      setLogs([])
      setError(null)
      return
    }
    let timer: ReturnType<typeof setTimeout> | null = null
    const load = async () => {
      setLoading(true)
      try {
        const data = await api.listLogs(selectedId)
        if (!mountedRef.current) return
        setLogs(data)
        setError(null)
      } catch (e) {
        if (!mountedRef.current) return
        setError(
          e instanceof ApiError
            ? e.message
            : e instanceof Error
              ? e.message
              : String(e),
        )
      } finally {
        if (mountedRef.current) setLoading(false)
      }
    }
    void load()

    const schedule = () => {
      timer = setTimeout(() => {
        if (document.visibilityState === 'hidden') {
          schedule()
          return
        }
        void load().finally(schedule)
      }, REFRESH_MS)
    }
    schedule()

    return () => {
      mountedRef.current = false
      if (timer) clearTimeout(timer)
    }
  }, [selectedId])

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 p-6">
      <div>
        <h1 className="text-lg font-semibold text-fg-1">日志查看</h1>
        <p className="mt-0.5 text-xs text-fg-3">
          选择一个流程查看其运行日志。每 5 秒自动刷新。
        </p>
      </div>

      {/* FlowPicker */}
      <div className="flex items-center gap-3 rounded-lg border border-line bg-bg-2 p-3">
        <ScrollText className="h-4 w-4 text-accent" />
        <label className="text-xs text-fg-2">选择流程</label>
        <select
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          className="flex-1 rounded-md border border-line bg-bg-3 px-3 py-1.5 text-sm text-fg-1 focus:border-accent focus:outline-none"
        >
          <option value="">-- 请选择流程 --</option>
          {flows.map((f) => (
            <option key={f.id} value={f.id}>
              {f.title} · {f.id}
            </option>
          ))}
        </select>
        {loading && <Spinner className="h-4 w-4" />}
      </div>

      {/* 错误 */}
      {error && (
        <div className="rounded-lg border border-error/30 bg-error/10 p-3 text-xs text-error">
          加载日志失败：{error}
        </div>
      )}

      {/* 空状态 */}
      {!selectedId && (
        <EmptyState
          title="请选择一个流程"
          hint="从上方下拉框选择流程后，将在此显示该流程的日志。"
        />
      )}

      {selectedId && !loading && logs.length === 0 && !error && (
        <EmptyState title="该流程暂无日志" hint="日志由引擎在执行过程中写入。" />
      )}

      {/* 日志列表（倒序：最新在上） */}
      {logs.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-line bg-bg-2">
          <table className="w-full text-left text-xs">
            <thead className="bg-bg-3 text-fg-3">
              <tr>
                <th className="w-44 px-3 py-2 font-normal">时间</th>
                <th className="w-16 px-3 py-2 font-normal">level</th>
                <th className="w-32 px-3 py-2 font-normal">来源</th>
                <th className="px-3 py-2 font-normal">消息</th>
              </tr>
            </thead>
            <tbody>
              {[...logs]
                .sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1))
                .map((log, i) => (
                  <tr
                    key={`${log.timestamp}-${i}`}
                    className="border-t border-line align-top"
                  >
                    <td className="px-3 py-1.5 font-mono-id text-fg-3">
                      {formatTime(log.timestamp)}
                    </td>
                    <td className="px-3 py-1.5">
                      <span
                        className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${
                          LEVEL_CLS[log.level] ?? LEVEL_CLS.info
                        }`}
                      >
                        {log.level}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 font-mono-id text-fg-2">
                      {log.source ?? '-'}
                    </td>
                    <td className="whitespace-pre-wrap break-words px-3 py-1.5 text-fg-1">
                      {log.message}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}