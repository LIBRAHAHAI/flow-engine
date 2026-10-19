// StepProgress —— 横向步骤进度条（已完成打勾 / 当前高亮 / 后续灰色，006 §3.2）
import { Check } from 'lucide-react'
import type { StepDef } from '../types'

export function StepProgress({
  steps,
  currentStepId,
}: {
  steps: StepDef[]
  currentStepId: string
}) {
  const isFlowDone = currentStepId === 'done'
  const curIdx = steps.findIndex((s) => s.id === currentStepId)

  return (
    <div className="overflow-x-auto pb-1">
      <div className="flex min-w-max items-start gap-1.5">
        {steps.map((s, i) => {
          const done = isFlowDone || i < curIdx
          const current = !isFlowDone && i === curIdx
          return (
            <div
              key={s.id}
              className="flex flex-col items-center gap-1.5"
              title={s.description}
            >
              <div
                className={`flex h-6 w-6 items-center justify-center rounded-full border text-[11px] transition-colors ${
                  done
                    ? 'border-success bg-success/20 text-success'
                    : current
                      ? 'border-accent bg-accent/20 text-accent'
                      : 'border-line text-fg-3'
                }`}
              >
                {done ? (
                  <Check className="h-3.5 w-3.5" />
                ) : current ? (
                  <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
                ) : (
                  i + 1
                )}
              </div>
              <span
                className={`text-[11px] ${
                  current ? 'text-fg-1' : done ? 'text-fg-2' : 'text-fg-3'
                }`}
              >
                {s.id}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
