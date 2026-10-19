/**
 * Sidebar — 240px 固定侧边栏（006 §2 基础上 2026-08-23 项目化改造）
 *
 * 结构：顶部 Logo + 标题 + 折叠按钮（v3 再做）→ 主 CTA「+ 新建项目」
 *       → 次 CTA「改进项目」（选已有项目建子项目，P2 项目层级）
 *       → 5 区导航（仪表盘 / 管理项目 / 流程模板 / 日志 / 设置）
 *       → 底部引擎状态 + 刷新按钮
 *
 * Props 由父组件传入：
 *   onRefresh      —— 触发 Dashboard remount（key={refreshTick}）
 *   refreshing     —— 当前是否在反馈中（按钮动画）
 *   onCreateFlow   —— 「+ 新建项目」点击回调（由父组件管理 CreateFlowModal 实例）
 *   onImproveProject —— 「改进项目」点击回调（打开 modal 的 improve 模式）
 *
 * 内部状态：
 *   engineState  —— 15s 轮询 listFlows 得到的连通状态
 */
import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  ListChecks,
  Columns3,
  ScrollText,
  Settings as SettingsIcon,
  GitBranch,
  Plus,
  RefreshCw,
} from 'lucide-react'
import { api, ApiError } from '../api/client'

type EngineState = 'checking' | 'ok' | 'err'

interface SidebarProps {
  onRefresh: () => void
  refreshing: boolean
  onCreateFlow: () => void
  onImproveProject: () => void
}

async function probe(): Promise<EngineState> {
  try {
    await api.listFlows()
    return 'ok'
  } catch (e) {
    // 业务 4xx 也算"引擎在响应"
    if (e instanceof ApiError && e.status !== 0) return 'ok'
    return 'err'
  }
}

interface NavItem {
  to: string
  icon: typeof LayoutDashboard
  label: string
  end?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', icon: LayoutDashboard, label: '仪表盘', end: true },
  { to: '/manage', icon: ListChecks, label: '管理项目' },
  // 2026-08-24 六项优化 #2：管理项目下新增看板入口
  { to: '/kanban', icon: Columns3, label: '任务看板' },
  { to: '/templates', icon: GitBranch, label: '流程模板' },
  { to: '/logs', icon: ScrollText, label: '日志' },
  { to: '/settings', icon: SettingsIcon, label: '设置' },
]

export function Sidebar({
  onRefresh,
  refreshing,
  onCreateFlow,
  onImproveProject,
}: SidebarProps) {
  const [state_, setState] = useState<EngineState>('checking')

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      const s = await probe()
      if (!cancelled) setState(s)
    }
    void check()
    const timer = window.setInterval(check, 15_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const dotCls =
    state_ === 'ok'
      ? 'bg-success animate-pulse'
      : state_ === 'err'
        ? 'bg-error'
        : 'bg-fg-3'
  const labelCls =
    state_ === 'ok' ? 'text-success' : state_ === 'err' ? 'text-error' : 'text-fg-3'
  const label = state_ === 'ok' ? '引擎运行中' : state_ === 'err' ? '引擎离线' : '检测中…'

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-bg-2">
      {/* 顶部 Logo */}
      <div className="flex h-14 items-center gap-2 border-b border-line px-4">
        <span className="grid h-7 w-7 place-items-center rounded-md bg-accent text-sm font-bold text-white">
          FE
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-fg-1">Flow Engine</p>
          <p className="truncate text-[10px] text-fg-3">v2 控制台</p>
        </div>
      </div>

      {/* 主 CTA：+ 新建项目 */}
      <div className="flex flex-col gap-1.5 px-3 pt-3">
        <button
          type="button"
          onClick={onCreateFlow}
          className="flex w-full items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-accent/85"
        >
          <Plus className="h-4 w-4" />
          新建项目
        </button>
        {/* 次 CTA：改进项目（P2 项目层级：选已有项目建子项目） */}
        <button
          type="button"
          onClick={onImproveProject}
          className="flex w-full items-center justify-center gap-1.5 rounded-md border border-line bg-bg-2 px-3 py-1.5 text-xs text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1"
        >
          <GitBranch className="h-3.5 w-3.5" />
          改进项目…
        </button>
      </div>

      {/* 导航区 */}
      <nav className="mt-3 flex flex-col gap-0.5 px-2">
        {NAV_ITEMS.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex h-9 items-center gap-2.5 rounded-md px-3 text-sm transition-colors ${
                isActive
                  ? 'bg-bg-3 text-fg-1'
                  : 'text-fg-2 hover:bg-bg-3 hover:text-fg-1'
              }`
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* 底部：引擎状态 + 刷新 */}
      <div className="mt-auto border-t border-line p-3">
        <div className="flex items-center justify-between gap-2">
          <span className={`flex items-center gap-1.5 text-xs ${labelCls}`}>
            <span className={`h-2 w-2 rounded-full ${dotCls}`} />
            {label}
          </span>
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="rounded-md p-1.5 text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:opacity-40"
            title="刷新 Dashboard"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>
    </aside>
  )
}

export default Sidebar