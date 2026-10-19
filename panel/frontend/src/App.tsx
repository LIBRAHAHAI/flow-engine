/**
 * App — 路由壳 + 侧边栏布局（v2，006 §1 / §2 / §5.1；2026-08-23 项目化改造）
 *
 * 路由：
 *   /            → Dashboard（项目总览）
 *   /flow/:id    → FlowDetail（详情 + 拍板 + grill-me）
 *   /grill/:id   → FlowDetail（grill-me deep link，路由同 FlowDetail）
 *   /manage      → ManageFlows（全部项目 + 树形上下级 + 搜索/筛选/删除）
 *   /templates   → FlowTemplates（流程模板库 + 绑定到项目，P3 新增）
 *   /logs        → Logs（按项目筛选日志查看器）
 *   /settings    → Settings（引擎连接 + 关于，**瘦身**，去掉「创建项目」）
 *   *            → 回到 Dashboard
 *
 * 布局：左 240px 固定 Sidebar + 右主内容区。
 * 「+ 新建项目」按钮的 CreateFlowModal 实例由 App 顶层管理一处，
 * Sidebar / Dashboard / ManageFlows 都能触发；
 * 「改进项目」（P2 项目层级）走同一 modal 的 improve 模式：
 *   - Sidebar「改进项目…」→ 打开 modal 并自选父项目
 *   - ManageFlows 行内「改进」→ 以该行为父项目预选
 *
 * 刷新机制：Sidebar 底部 ↻ 按钮触发 refreshTick++，让 Dashboard 通过 key={refreshTick}
 * 强制 remount 来重新拉数据（沿用 v1 模式，比 location.reload() 轻）。
 */
import { useCallback, useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import KanbanBoard from './pages/KanbanBoard'
import { Sidebar } from './components/Sidebar'
import { ToastMount } from './components/Toast'
import { CreateFlowModal } from './components/CreateFlowModal'
import Dashboard from './pages/Dashboard'
import FlowDetail from './pages/FlowDetail'
import Settings from './pages/Settings'
import ManageFlows from './pages/ManageFlows'
import FlowTemplates from './pages/FlowTemplates'
import Logs from './pages/Logs'
import type { FlowSummary } from './types'

export default function App() {
  const [refreshTick, setRefreshTick] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  /** improve 模式预选的父项目 id；null = 新建模式 */
  const [improveParent, setImproveParent] = useState<string | undefined>(undefined)

  const handleRefresh = useCallback(() => {
    setRefreshing(true)
    setRefreshTick((t) => t + 1)
    window.setTimeout(() => setRefreshing(false), 600)
  }, [])

  const openCreate = useCallback(() => {
    setImproveParent(undefined)
    setCreateOpen(true)
  }, [])

  const openImprove = useCallback((parentId?: string) => {
    // 无参调用（Sidebar 入口）= 打开 modal 让用户自选父项目
    setImproveParent(parentId ?? '')
    setCreateOpen(true)
  }, [])

  const closeCreate = useCallback(() => setCreateOpen(false), [])
  const handleCreated = useCallback((_flow: FlowSummary) => {
    // 触发 Dashboard / ManageFlows 重新拉取
    setRefreshTick((t) => t + 1)
  }, [])

  return (
    <div className="flex min-h-screen bg-bg-1">
      <Sidebar
        onRefresh={handleRefresh}
        refreshing={refreshing}
        onCreateFlow={openCreate}
        onImproveProject={() => openImprove()}
      />
      <main className="flex-1 overflow-y-auto">
        <Routes>
          <Route
            path="/"
            element={<Dashboard key={refreshTick} onCreateFlow={openCreate} />}
          />
          <Route path="/flow/:id" element={<FlowDetail />} />
          <Route path="/grill/:id" element={<FlowDetail />} />
          <Route
            path="/manage"
            element={
              <ManageFlows
                key={`manage-${refreshTick}`}
                onCreateFlow={openCreate}
                onImproveProject={(parentId) => openImprove(parentId)}
              />
            }
          />
          <Route path="/templates" element={<FlowTemplates />} />
          <Route path="/kanban" element={<KanbanBoard />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <ToastMount />
      <CreateFlowModal
        key={`create-${improveParent ?? 'new'}`}
        open={createOpen}
        onClose={closeCreate}
        onCreated={handleCreated}
        initialParent={
          improveParent === undefined ? undefined : improveParent || ''
        }
      />
    </div>
  )
}
