/**
 * KanbanBoard — 任务看板页（2026-08-24 六项优化 #4/#5）
 *
 * 路由 /kanban（Sidebar「管理项目」下新增按钮进入）
 *
 * 数据源：GET /api/kanban/tasks —— Hermes kanban 库只读快照，
 *   后端已推导 derivedState（前端不自行判断，只做渲染映射）：
 *     ready            待认领（正常排队）
 *     unassigned-agent 执行者未唤醒/不存在（如 assignee=flow-engine），没人会认领
 *     running          执行中（心跳新鲜）
 *     stuck            疑似卡住（running 但心跳超 15 分钟）
 *     blocked          连续失败 ≥2 次
 *     done             完成
 *
 * 四列布局：待认领/未唤醒 → 执行中 → 受阻 → 近期完成。
 * 每卡：状态灯 + 执行者徽标 + 项目·步骤 + 相对时间 + 「催一下」按钮。
 *
 * 催动边界（user 2026-08-24 拍板）：POST /api/kanban/tasks/{id}/nudge =
 *   往执行者待办桥队列推一条提醒，机器送信、人工触发；不代投拍板、不改任务状态。
 *   服务端 10 分钟冷却（409），assignee 无效时兜底催 hermes（响应 effectiveTarget 说明）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlarmClock,
  BellRing,
  CircleDot,
  Hand,
  Octagon,
  RefreshCw,
  Search,
  UserX,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import { api, ApiError } from '../api/client'
import { toast } from '../components/Toast'
import { Spinner } from '../components/ui/Spinner'
import type { KanbanTask } from '../types'

// ── 状态渲染映射（与后端 derivedState 注释一一对应） ────────

interface StateCfg {
  label: string
  cls: string
  icon: LucideIcon
  pulse?: boolean
}

const STATE_CFG: Record<string, StateCfg> = {
  ready: {
    label: '待认领',
    cls: 'bg-accent/15 text-accent ring-1 ring-accent/30',
    icon: Hand,
  },
  'unassigned-agent': {
    label: '执行者未唤醒',
    cls: 'bg-error/15 text-error ring-1 ring-error/30',
    icon: UserX,
  },
  running: {
    label: '执行中',
    cls: 'bg-success/15 text-success ring-1 ring-success/30',
    icon: Zap,
    pulse: true,
  },
  stuck: {
    label: '疑似卡住',
    cls: 'bg-warning/15 text-warning ring-1 ring-warning/30',
    icon: AlarmClock,
  },
  blocked: {
    label: '受阻',
    cls: 'bg-error/15 text-error ring-1 ring-error/30',
    icon: Octagon,
  },
  done: {
    label: '完成',
    cls: 'bg-fg-3/15 text-fg-2 ring-1 ring-fg-3/30',
    icon: CircleDot,
  },
  archived: {
    label: '已归档',
    cls: 'bg-fg-3/15 text-fg-2 ring-1 ring-fg-3/30',
    icon: CircleDot,
  },
}

function stateCfg(state: string): StateCfg {
  return (
    STATE_CFG[state] ?? {
      label: state,
      cls: 'bg-fg-3/15 text-fg-2 ring-1 ring-fg-3/30',
      icon: CircleDot,
    }
  )
}

/** 列定义：derivedState → 四列归属（顺序即列序）。
 *  done/archived = 终态，归「近期完成」并默认隐藏（活卡不被历史卡淹没） */
const DONE_STATES = ['done', 'archived']
const COLUMNS: { key: string; title: string; hint: string; match: (t: KanbanTask) => boolean }[] = [
  {
    key: 'waiting',
    title: '待认领 / 未唤醒',
    hint: '排队中或派给了没人认领的执行者',
    match: (t) =>
      !DONE_STATES.includes(t.derivedState) &&
      !['running', 'stuck', 'blocked'].includes(t.derivedState),
  },
  {
    key: 'active',
    title: '执行中',
    hint: '含心跳停更超 15 分钟的疑似卡住',
    match: (t) => t.derivedState === 'running' || t.derivedState === 'stuck',
  },
  {
    key: 'blocked',
    title: '受阻',
    hint: '连续失败 ≥2 次，需要人看失败原因',
    match: (t) => t.derivedState === 'blocked',
  },
  {
    key: 'done',
    title: '近期完成',
    hint: '最近完成的任务（最多显示 12 张）',
    match: (t) => DONE_STATES.includes(t.derivedState),
  },
]

const DONE_SHOW_LIMIT = 12

/** epoch 秒 → 相对时间（中文短句） */
function ago(ts?: number | null): string {
  if (!ts) return ''
  const diff = Date.now() / 1000 - ts
  if (diff < 0) return '刚刚'
  if (diff < 60) return `${Math.floor(diff)} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

/** 卡片「最近活动」时间：不同状态取不同锚点 */
function activityTs(t: KanbanTask): number | null {
  if (t.derivedState === 'done') return t.completedAt ?? null
  if (t.derivedState === 'running' || t.derivedState === 'stuck')
    return t.lastHeartbeatAt ?? t.startedAt ?? null
  return t.createdAt ?? null
}

export default function KanbanBoard() {
  const [tasks, setTasks] = useState<KanbanTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [hideDone, setHideDone] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [nudgingId, setNudgingId] = useState<string | null>(null)

  const load = useCallback(async (silent: boolean) => {
    if (!silent) setLoading(true)
    else setRefreshing(true)
    try {
      const res = await api.listKanbanTasks()
      setTasks(res.tasks)
      setError('')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void load(false)
    // 15s 静默自刷新：心跳/认领状态变化自动上屏
    const timer = window.setInterval(() => void load(true), 15_000)
    return () => window.clearInterval(timer)
  }, [load])

  /** 过滤：搜索词匹配 id/项目/步骤/执行者；hideDone 时收起已完成列数据由列内截断处理 */
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return tasks.filter((t) => {
      if (!q) return true
      return (
        t.id.toLowerCase().includes(q) ||
        (t.flow ?? '').toLowerCase().includes(q) ||
        (t.stepTitle ?? '').toLowerCase().includes(q) ||
        (t.assignee ?? '').toLowerCase().includes(q)
      )
    })
  }, [tasks, query])

  const handleNudge = async (t: KanbanTask) => {
    setNudgingId(t.id)
    try {
      const res = await api.nudgeKanbanTask(t.id)
      toast.ok(
        res.effectiveTarget !== res.target
          ? `已催 ${res.target}（${res.effectiveTarget}）`
          : `已催 ${res.target}`,
      )
    } catch (e) {
      toast.err(
        `催动失败：${e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)}`,
      )
    } finally {
      setNudgingId(null)
    }
  }

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 p-6">
      {/* 头部：标题 + 过滤 + 刷新 */}
      <div className="flex flex-wrap items-center gap-2">
        <div>
          <h1 className="text-lg font-semibold text-fg-1">任务看板</h1>
          <p className="mt-0.5 text-xs text-fg-3">
            共 {tasks.length} 张卡 · 15 秒自动刷新 ·「催一下」= 给执行者待办队列送提醒
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <div className="flex items-center gap-2 rounded-md border border-line bg-bg-2 px-3 py-1.5">
            <Search className="h-3.5 w-3.5 shrink-0 text-fg-3" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="按项目 / 步骤 / 执行者过滤…"
              className="w-56 bg-transparent text-sm text-fg-1 placeholder:text-fg-3 focus:outline-none"
            />
          </div>
          <button
            type="button"
            onClick={() => setHideDone((v) => !v)}
            className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
              hideDone
                ? 'border-accent/40 bg-accent/10 text-accent'
                : 'border-line bg-bg-2 text-fg-2 hover:bg-bg-3 hover:text-fg-1'
            }`}
          >
            {hideDone ? '已隐藏完成' : '显示完成'}
          </button>
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={refreshing}
            className="rounded-md border border-line bg-bg-2 p-2 text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:opacity-40"
            title="立即刷新"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* 错误 */}
      {error && (
        <div className="rounded-lg border border-error/30 bg-error/10 p-3 text-xs text-error">
          加载失败：{error}
        </div>
      )}

      {/* 加载 */}
      {loading && tasks.length === 0 && (
        <div className="flex items-center justify-center py-12">
          <Spinner className="h-8 w-8" />
        </div>
      )}

      {/* 四列看板 */}
      {!loading && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {COLUMNS.map((col) => {
            let colTasks = visible.filter(col.match)
            if (col.key === 'done') {
              colTasks = hideDone ? [] : colTasks.slice(0, DONE_SHOW_LIMIT)
            }
            return (
              <section key={col.key} className="flex min-w-0 flex-col rounded-lg border border-line bg-bg-1">
                <header className="border-b border-line px-3 py-2">
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-medium text-fg-1">{col.title}</h2>
                    <span className="rounded-full bg-bg-3 px-2 py-px text-[10px] text-fg-3">
                      {colTasks.length}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[10px] leading-tight text-fg-3">{col.hint}</p>
                </header>
                <div className="flex max-h-[calc(100vh-260px)] min-h-24 flex-col gap-2 overflow-y-auto p-2">
                  {colTasks.map((t) => {
                    const cfg = stateCfg(t.derivedState)
                    const Icon = cfg.icon
                    const act = ago(activityTs(t))
                    return (
                      <article
                        key={t.id}
                        className={`rounded-md border border-line bg-bg-2 p-2.5 ${
                          t.derivedState === 'stuck' ? 'animate-pulse' : ''
                        }`}
                      >
                        <div className="flex items-center justify-between gap-1.5">
                          <span
                            className={`inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${cfg.cls}`}
                          >
                            <Icon className={`h-3 w-3 ${cfg.pulse ? 'animate-pulse' : ''}`} />
                            {cfg.label}
                          </span>
                          <span className="truncate rounded bg-bg-3 px-1.5 py-0.5 font-mono-id text-[10px] text-fg-2" title={`执行者 ${t.assignee}`}>
                            @{t.assignee || '—'}
                          </span>
                        </div>

                        <p className="mt-1.5 truncate text-xs font-medium text-fg-1" title={t.stepTitle}>
                          {t.flow ? (
                            <Link to={`/flow/${t.flow}`} className="hover:text-accent hover:underline">
                              {t.flow}
                            </Link>
                          ) : (
                            <span className="text-fg-2">非引擎卡</span>
                          )}
                          <span className="mx-1 text-fg-3">·</span>
                          {t.stepTitle}
                        </p>

                        {(t.failureCount ?? 0) > 0 && (
                          <p className="mt-1 truncate text-[10px] text-error" title={t.lastFailureError ?? ''}>
                            连续失败 {t.failureCount} 次{t.lastFailureError ? `：${t.lastFailureError}` : ''}
                          </p>
                        )}
                        {t.derivedState === 'ready' && t.readyStale && (
                          <p className="mt-1 text-[10px] text-warning">躺了很久没人领，考虑催一下</p>
                        )}
                        {t.derivedState === 'stuck' && (
                          <p className="mt-1 text-[10px] text-warning">心跳停更，执行者可能掉线</p>
                        )}

                        <div className="mt-2 flex items-center justify-between gap-1.5">
                          <span className="truncate text-[10px] text-fg-3" title={t.id}>
                            {act || t.id}
                          </span>
                          {t.derivedState !== 'done' && (
                            <button
                              type="button"
                              onClick={() => void handleNudge(t)}
                              disabled={nudgingId === t.id}
                              className="inline-flex shrink-0 items-center gap-1 rounded border border-line px-1.5 py-0.5 text-[10px] text-fg-2 transition-colors hover:border-accent/50 hover:bg-accent/10 hover:text-accent disabled:opacity-40"
                              title="往执行者待办桥队列推一条提醒（人工催动，不改任务状态）"
                            >
                              {nudgingId === t.id ? (
                                <Spinner className="h-2.5 w-2.5" />
                              ) : (
                                <BellRing className="h-2.5 w-2.5" />
                              )}
                              催一下
                            </button>
                          )}
                        </div>
                      </article>
                    )
                  })}
                  {colTasks.length === 0 && (
                    <p className="px-2 py-6 text-center text-[11px] text-fg-3">空</p>
                  )}
                </div>
              </section>
            )
          })}
        </div>
      )}
    </div>
  )
}
