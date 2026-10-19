// DecisionPanel —— 拍板组件（A/B/C 选项按钮 + 问题，006 §3.4 / §4.1 #9）
//
// 2026-08-24 doker 事故修复（P-fallback）：
//   FALLBACK 按钮原样发 key「A/B/C」，但项目级 yaml 无 options 时引擎走
//   accept_words 词典判定，「A」不在词典 → 被判驳回打回（按钮却写着
//   「同意」——展示与判定矛盾，见 incident-20260823-doker-decision-stall）。
//   修法：FALLBACK 发语义词（同意/驳回/跳过），引擎词典直接命中；
//   模板自带 options 时仍发模板 key（引擎 options 匹配分支消费）。
import { Loader2 } from 'lucide-react'
import type { StepDef } from '../types'
import { Button } from './ui/Button'

const FALLBACK_OPTIONS: { key: string; send: string; label: string }[] = [
  { key: 'A', send: '同意', label: '同意' },
  { key: 'B', send: '驳回', label: '驳回重做' },
  { key: 'C', send: '跳过', label: '跳过（条件通过）' },
]

const VARIANT: Record<string, 'primary' | 'danger' | 'secondary'> = {
  A: 'primary',
  B: 'danger',
  C: 'secondary',
}

export function DecisionPanel({
  step,
  onDecide,
  deciding = false,
}: {
  step: StepDef
  onDecide: (key: string) => void
  deciding?: boolean
}) {
  const options: { key: string; label: string; send?: string }[] =
    step.options && step.options.length > 0
      ? step.options.map((o) => ({ key: o.key, label: o.label }))
      : FALLBACK_OPTIONS

  return (
    <div className="rounded-lg border border-warning/30 bg-warning/5 p-4">
      <p className="mb-1 text-xs font-medium uppercase tracking-wider text-warning">
        待拍板
      </p>
      <p className="mb-3 text-sm font-medium text-fg-1">
        Q: {step.question || '请确认是否推进'}
      </p>
      <div className="flex flex-wrap gap-2">
        {options.map((o) => (
          <Button
            key={o.key}
            variant={VARIANT[o.key] ?? 'secondary'}
            disabled={deciding}
            // FALLBACK 项发语义词（o.send），模板 options 项仍发模板 key
            onClick={() => onDecide('send' in o && o.send ? o.send : o.key)}
          >
            {o.key}. {o.label}
          </Button>
        ))}
        {deciding && <Loader2 className="h-4 w-4 animate-spin self-center text-fg-3" />}
      </div>
    </div>
  )
}
