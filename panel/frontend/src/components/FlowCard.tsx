// FlowCard —— 实例卡片（标题/状态/进度/历史摘要，006 §4.1 #4）
import { Link } from 'react-router-dom'
import { ArrowRight, Play } from 'lucide-react'
import type { FlowSummary } from '../types'
import { StatusBadge } from './StatusBadge'
import { formatTime, historySummary } from '../lib/format'
import { Card } from './ui/Card'
import { api } from '../api/client'
import { toast } from './Toast'

export function FlowCard({ flow, onRefresh }: { flow: FlowSummary; onRefresh?: () => void }) {
  const total = Math.max(1, flow.totalSteps)
  const progress = Math.max(0, Math.min(100, Math.round((flow.currentStepIndex / total) * 100)))
  const recent = flow.recentHistory ?? []

  // mechanism-fix-engine-verify-wait F2：paused 恢复按钮（002 §F2.2）
  const handleResume = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    void (async () => {
      try {
        await api.resumeFlow(flow.id)
        toast.ok(`已恢复流程 ${flow.id}`)
        if (onRefresh) {
          window.setTimeout(() => onRefresh(), 800)
        } else {
          window.setTimeout(() => window.location.reload(), 800)
        }
      } catch (err) {
        toast.err(`恢复失败：${err instanceof Error ? err.message : String(err)}`)
      }
    })()
  }

  return (
    <Card className="flex flex-col p-4 transition-colors hover:border-accent/40">
      {/* 头部：待拍板角标 + 标题 + 状态徽章 */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {flow.hasPendingDecision && (
              <span className="shrink-0 rounded bg-warning/15 px-1.5 py-0.5 text-[10px] font-medium text-warning">
                📌 待拍板
              </span>
            )}
            <h3 className="truncate text-sm font-semibold text-fg-1" title={flow.title}>
              {flow.title}
            </h3>
          </div>
          <p className="mt-1 truncate font-mono-id text-xs text-fg-3" title={flow.id}>
            {flow.id}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {flow.status === 'paused' && (
            <button
              type="button"
              onClick={handleResume}
              className="inline-flex items-center gap-1 rounded bg-warning/15 px-2 py-1 text-xs font-medium text-warning transition-colors hover:bg-warning/25"
              title="恢复 paused 流程"
            >
              <Play className="h-3 w-3" />
              恢复
            </button>
          )}
          <StatusBadge status={flow.status} />
        </div>
      </div>

      {/* 当前步骤 + 进度 */}
      <div className="mb-2 flex items-baseline justify-between gap-2 text-xs">
        <span className="truncate text-fg-2" title={flow.currentStep}>
          @{flow.currentStep}
          {flow.currentStepType ? ` · ${flow.currentStepType}` : ''}
        </span>
        <span className="shrink-0 font-mono-id text-fg-3">
          {flow.currentStepIndex}/{flow.totalSteps}
        </span>
      </div>
      <div className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-bg-3">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            flow.status === 'failed'
              ? 'bg-error'
              : flow.status === 'completed'
                ? 'bg-success'
                : 'bg-accent'
          }`}
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* 最近历史 */}
      {recent.length > 0 && (
        <ul className="mb-3 space-y-1">
          {recent.map((e, i) => (
            <li key={i} className="flex gap-2 text-[11px] leading-tight">
              <span className="shrink-0 font-mono-id text-fg-3">{e.step}</span>
              <span className="truncate text-fg-2" title={historySummary(e)}>
                {historySummary(e)}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* 底部：详情 + 更新时间 */}
      <div className="mt-auto flex items-center justify-between border-t border-line pt-3 text-xs">
        <span className="text-fg-3">更新于 {formatTime(flow.updatedAt)}</span>
        <Link
          to={`/flow/${flow.id}`}
          className="inline-flex items-center gap-1 text-accent transition-colors hover:text-accent/80"
        >
          详情 <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </Card>
  )
}
