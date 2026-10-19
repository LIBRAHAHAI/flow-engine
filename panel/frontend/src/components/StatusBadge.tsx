// StatusBadge —— 状态徽章（running/done/failed/idle，未知状态兜底）
import type { FlowStatus } from '../types'

const MAP: Record<string, { label: string; cls: string; dot: string }> = {
  running: {
    label: '运行中',
    cls: 'bg-accent/15 text-accent ring-1 ring-accent/30',
    dot: 'bg-accent animate-pulse',
  },
  completed: {
    label: '已完成',
    cls: 'bg-success/15 text-success ring-1 ring-success/30',
    dot: 'bg-success',
  },
  failed: {
    label: '失败',
    cls: 'bg-error/15 text-error ring-1 ring-error/30',
    dot: 'bg-error',
  },
  idle: {
    label: '空闲',
    cls: 'bg-fg-3/15 text-fg-2 ring-1 ring-fg-3/30',
    dot: 'bg-fg-3',
  },
  // mechanism-fix-engine-verify-wait F2 新增（002 §F2.2）
  paused: {
    label: '已暂停',
    cls: 'bg-warning/15 text-warning ring-1 ring-warning/30',
    dot: 'bg-warning',
  },
}

export function StatusBadge({ status }: { status: FlowStatus }) {
  const cfg = MAP[status] ?? {
    label: status,
    cls: 'bg-fg-3/15 text-fg-2 ring-1 ring-fg-3/30',
    dot: 'bg-fg-3',
  }
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${cfg.cls}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </span>
  )
}
