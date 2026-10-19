/**
 * Dashboard — 总览页（006 §3.7，瘦身）
 *
 * 改动 vs v1：
 *   - 去掉 inline 的「↻ 刷新」按钮（迁到 Sidebar 底部）
 *   - 空状态卡片「去 Settings 创建」改为「+ 新建项目」CTA（弹 CreateFlowModal）
 *   - 保留 DecisionBanner + 筛选条 + 卡片网格（v1 已有）
 *   - key={refreshTick} 让 Sidebar 刷新按钮触发 remount（沿用 v1）
 */
import { useMemo, useState } from 'react'
import { Plus } from 'lucide-react'
import { useFlows } from '../hooks/useFlows'
import { DecisionBanner } from '../components/DecisionBanner'
import { EmptyState } from '../components/ui/EmptyState'
import { FlowCard } from '../components/FlowCard'
import { Spinner } from '../components/ui/Spinner'
import type { FlowStatus, FlowSummary } from '../types'

type Filter = 'all' | 'pending' | 'running' | 'completed' | 'failed'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待拍板' },
  { key: 'running', label: '运行中' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '失败' },
]

// 状态优先级：running > pending > failed > completed > idle
const STATUS_PRIORITY: Record<string, number> = {
  running: 0,
  failed: 1,
  completed: 2,
  idle: 3,
  unknown: 4,
}

function getStatusPriority(s: FlowStatus | string): number {
  return STATUS_PRIORITY[s] ?? STATUS_PRIORITY.unknown
}

function matchFilter(f: FlowSummary, filter: Filter): boolean {
  switch (filter) {
    case 'all': return true
    case 'pending': return f.hasPendingDecision
    case 'running': return f.status === 'running'
    case 'completed': return f.status === 'completed'
    case 'failed': return f.status === 'failed'
  }
}

interface DashboardProps {
  onCreateFlow: () => void
}

export default function Dashboard({ onCreateFlow }: DashboardProps) {
  const { flows, loading, error, refetch } = useFlows()
  const [filter, setFilter] = useState<Filter>('all')

  const pendingFlows = useMemo(
    () => flows.filter((f) => f.hasPendingDecision),
    [flows],
  )

  const visible = useMemo(() => {
    return flows
      .filter((f) => matchFilter(f, filter))
      .sort((a, b) => {
        const pa = getStatusPriority(a.status)
        const pb = getStatusPriority(b.status)
        if (pa !== pb) return pa - pb
        return a.updatedAt < b.updatedAt ? 1 : -1
      })
  }, [flows, filter])

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
      <DecisionBanner pending={pendingFlows} />

      {/* 头部 + 筛选 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-fg-1">项目总览</h1>
          <p className="mt-0.5 text-xs text-fg-3">
            {flows.length > 0 ? `共 ${flows.length} 个项目` : '加载中…'}
            {pendingFlows.length > 0 && (
              <span className="ml-2 text-amber-300">
                · {pendingFlows.length} 个待拍板
              </span>
            )}
          </p>
        </div>

        <div className="flex items-center gap-1 rounded-[8px] border border-line bg-bg-2 p-0.5">
          {FILTERS.map((f) => {
            const count =
              f.key === 'all'
                ? flows.length
                : flows.filter((x) => matchFilter(x, f.key)).length
            const active = filter === f.key
            return (
              <button
                key={f.key}
                type="button"
                onClick={() => setFilter(f.key)}
                className={`rounded-[6px] px-3 py-1.5 text-xs font-medium transition-colors ${
                  active
                    ? 'bg-accent text-white'
                    : 'text-fg-2 hover:bg-bg-3 hover:text-fg-1'
                }`}
              >
                {f.label}
                {count > 0 && (
                  <span
                    className={`ml-1.5 rounded px-1.5 py-0.5 text-[10px] ${
                      active
                        ? 'bg-white/20 text-white'
                        : 'bg-bg-3 text-fg-3'
                    }`}
                  >
                    {count}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* 错误/加载状态 */}
      {error && (
        <div className="rounded-[10px] border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-300">
          加载失败：{error}
        </div>
      )}

      {loading && flows.length === 0 && (
        <div className="flex items-center justify-center py-12">
          <Spinner className="h-8 w-8" />
        </div>
      )}

      {/* 空状态：可触发「+ 新建项目」CTA */}
      {!loading && flows.length === 0 && !error && (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-line bg-bg-2/50 py-12">
          <EmptyState
            title="还没有项目"
            hint="点击下方按钮创建一个新项目。"
          />
          <button
            type="button"
            onClick={onCreateFlow}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent/85"
          >
            <Plus className="h-4 w-4" />
            新建项目
          </button>
        </div>
      )}

      {visible.length > 0 && (
        <div
          data-testid="flow-grid"
          className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3"
        >
          {visible.map((f) => (
            <FlowCard key={f.id} flow={f} onRefresh={refetch} />
          ))}
        </div>
      )}

      {!loading && flows.length > 0 && visible.length === 0 && (
        <EmptyState
          title="此筛选下无项目"
          hint={`筛选「${FILTERS.find((f) => f.key === filter)?.label}」下没有匹配的项目`}
        />
      )}
    </div>
  )
}