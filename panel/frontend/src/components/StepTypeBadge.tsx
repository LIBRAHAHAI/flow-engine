/**
 * StepTypeBadge — 步骤类型徽章（dispatch / decision / wait / verify / done / discuss）
 */
import type { StepType } from '../types'

interface StepTypeBadgeProps {
  type: StepType
}

const TYPE_STYLES: Record<StepType, { label: string; className: string }> = {
  decision: { label: '决策', className: 'bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/30' },
  dispatch: { label: '执行', className: 'bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-400/30' },
  wait: { label: '等待', className: 'bg-slate-500/15 text-slate-300 ring-1 ring-slate-400/30' },
  verify: { label: '校验', className: 'bg-violet-500/15 text-violet-300 ring-1 ring-violet-400/30' },
  done: { label: '完成', className: 'bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-400/30' },
  // 2026-08-22 返工（P1b）：立项访谈步骤
  discuss: { label: '访谈', className: 'bg-sky-500/15 text-sky-300 ring-1 ring-sky-400/30' },
  '': { label: '未知', className: 'bg-slate-500/15 text-slate-300 ring-1 ring-slate-400/30' },
}

export function StepTypeBadge({ type }: StepTypeBadgeProps) {
  const style = TYPE_STYLES[type] ?? TYPE_STYLES['']
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ring-1 ${style.className}`}>
      {style.label}
    </span>
  )
}

export default StepTypeBadge