/**
 * ManageFlows — 项目管理页（006 §3.6 基础上 2026-08-23 项目化改造）
 *
 * 路由 /manage
 *
 * 结构：
 *   顶部：搜索框（按 title / id 模糊）+ 状态筛选 + 「+ 新建项目」按钮
 *   主体：树形表格 —— 父项目行在前，子项目缩进挂在其下（P2 项目层级）；
 *         子项目行显示 ↳ 前缀 + 父项目名徽标；无父项目的独立项目平铺
 *   每行操作：详情 + 改进（以该行为父项目建子项目）+ 删除（原生 confirm 二次确认）
 *   空状态：搜索 / 筛选无结果提示
 *
 * CreateFlowModal 由父组件传入（App 统一管理 modal 实例，避免重复挂载）
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Search, Trash2, ChevronRight, GitBranch } from 'lucide-react'
import { useFlows } from '../hooks/useFlows'
import { api, ApiError } from '../api/client'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState } from '../components/ui/EmptyState'
import { Spinner } from '../components/ui/Spinner'
import { toast } from '../components/Toast'
import { formatTime } from '../lib/format'
import type { FlowStatus, FlowSummary } from '../types'

type Filter = 'all' | FlowStatus

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '运行中' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '失败' },
  { key: 'idle', label: '空闲' },
]

/** 树形排序：父项目行在前，其子项目紧随其后（缩进渲染）；孤儿子项目按平铺兜底 */
function buildTree(flows: FlowSummary[]): { flow: FlowSummary; depth: number }[] {
  const byId = new Map(flows.map((f) => [f.id, f]))
  const childrenOf = new Map<string, FlowSummary[]>()
  const roots: FlowSummary[] = []
  const orphans: FlowSummary[] = []
  for (const f of flows) {
    if (!f.parent) {
      roots.push(f)
    } else if (byId.has(f.parent)) {
      const list = childrenOf.get(f.parent) ?? []
      list.push(f)
      childrenOf.set(f.parent, list)
    } else {
      orphans.push(f) // 父项目已被删除等异常情况，仍要可见
    }
  }
  const out: { flow: FlowSummary; depth: number }[] = []
  for (const r of roots) {
    out.push({ flow: r, depth: 0 })
    for (const c of childrenOf.get(r.id) ?? []) {
      out.push({ flow: c, depth: 1 })
    }
  }
  for (const o of orphans) out.push({ flow: o, depth: 0 })
  return out
}

interface ManageFlowsProps {
  onCreateFlow: () => void
  /** 行内「改进」入口：以该行为父项目打开 CreateFlowModal 的 improve 模式 */
  onImproveProject: (parentId: string) => void
}

export default function ManageFlows({
  onCreateFlow,
  onImproveProject,
}: ManageFlowsProps) {
  const { flows, loading, error, refetch } = useFlows()
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [deletingId, setDeletingId] = useState<string | null>(null)

  // 过滤后保持树形结构：命中父项目时其子项目跟随展示；命中子项目时其父项目也展示
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matched = flows.filter((f) => {
      if (filter !== 'all' && f.status !== filter) return false
      if (!q) return true
      return (
        f.id.toLowerCase().includes(q) || f.title.toLowerCase().includes(q)
      )
    })
    if (q === '' && filter === 'all') return buildTree(matched)
    // 有筛选时补齐上下文：命中的子项目带上父项目，命中的父项目带上子项目
    const byId = new Map(flows.map((f) => [f.id, f]))
    const keep = new Set(matched.map((f) => f.id))
    for (const f of matched) {
      if (f.parent && byId.has(f.parent)) keep.add(f.parent)
      if (!f.parent) {
        for (const c of flows.filter((x) => x.parent === f.id)) keep.add(c.id)
      }
    }
    return buildTree(flows.filter((f) => keep.has(f.id)))
  }, [flows, query, filter])

  const handleDelete = async (flow: FlowSummary) => {
    const ok = window.confirm(
      `确认删除项目「${flow.title}」(${flow.id})？\n此操作不可撤销。`,
    )
    if (!ok) return
    setDeletingId(flow.id)
    try {
      await api.deleteFlow(flow.id)
      toast.ok(`已删除 ${flow.id}`)
      void refetch()
    } catch (e) {
      toast.err(
        `删除失败：${e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)}`,
      )
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
      <div>
        <h1 className="text-lg font-semibold text-fg-1">管理项目</h1>
        <p className="mt-0.5 text-xs text-fg-3">
          {flows.length > 0 ? `共 ${flows.length} 个项目` : '加载中…'}
          {visible.length !== flows.length && (
            <span className="ml-2 text-accent">· 显示 {visible.length} 个</span>
          )}
        </p>
      </div>

      {/* 顶部三件套：搜索 + 筛选 + 新建 */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-bg-2 p-3">
        <div className="flex flex-1 items-center gap-2 rounded-md border border-line bg-bg-3 px-3 py-1.5">
          <Search className="h-3.5 w-3.5 shrink-0 text-fg-3" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="按标题或 id 模糊搜索…"
            className="flex-1 bg-transparent text-sm text-fg-1 placeholder:text-fg-3 focus:outline-none"
          />
        </div>

        <div className="flex items-center gap-1 rounded-md border border-line bg-bg-3 p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`rounded px-2.5 py-1 text-xs transition-colors ${
                filter === f.key
                  ? 'bg-accent text-white'
                  : 'text-fg-2 hover:bg-bg-2 hover:text-fg-1'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={onCreateFlow}
          className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent/85"
        >
          <Plus className="h-3.5 w-3.5" />
          新建项目
        </button>
      </div>

      {/* 错误 */}
      {error && (
        <div className="rounded-lg border border-error/30 bg-error/10 p-3 text-xs text-error">
          加载失败：{error}
        </div>
      )}

      {/* 加载 */}
      {loading && flows.length === 0 && (
        <div className="flex items-center justify-center py-12">
          <Spinner className="h-8 w-8" />
        </div>
      )}

      {/* 树形表格 */}
      {!loading && visible.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-line bg-bg-2">
          <table className="w-full text-left text-xs">
            <thead className="bg-bg-3 text-fg-3">
              <tr>
                <th className="px-3 py-2 font-normal">项目</th>
                <th className="px-3 py-2 font-normal">状态</th>
                <th className="px-3 py-2 font-normal">当前步骤</th>
                <th className="px-3 py-2 font-normal">执行模板</th>
                <th className="px-3 py-2 font-normal">更新于</th>
                <th className="px-3 py-2 text-right font-normal">操作</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(({ flow: f, depth }) => (
                <tr key={f.id} className="border-t border-line align-middle">
                  <td className="px-3 py-2">
                    <div
                      className="flex items-center gap-1.5"
                      style={depth > 0 ? { paddingLeft: `${depth * 20}px` } : undefined}
                    >
                      {depth > 0 && (
                        <span className="-ml-1 select-none text-fg-3">↳</span>
                      )}
                      <span className="text-fg-1">{f.title}</span>
                      {f.parent && (
                        <span
                          className="inline-flex shrink-0 items-center gap-0.5 rounded border border-line bg-bg-3 px-1 py-px text-[10px] text-fg-3"
                          title={`改进自 ${f.parent}`}
                        >
                          <GitBranch className="h-2.5 w-2.5" />
                          {f.parent}
                        </span>
                      )}
                      <span className="font-mono-id text-fg-3">{f.id}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge status={f.status} />
                  </td>
                  <td className="px-3 py-2 font-mono-id text-fg-2">
                    {f.currentStep}
                  </td>
                  <td className="px-3 py-2 font-mono-id text-fg-2">
                    {f.template ?? '—'}
                  </td>
                  <td className="px-3 py-2 text-fg-3">{formatTime(f.updatedAt)}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-end gap-1">
                      <Link
                        to={`/flow/${f.id}`}
                        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1"
                      >
                        详情 <ChevronRight className="h-3.5 w-3.5" />
                      </Link>
                      <button
                        type="button"
                        onClick={() => onImproveProject(f.id)}
                        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1"
                        title={`以「${f.title}」为父项目创建改进子项目`}
                      >
                        <GitBranch className="h-3 w-3" />
                        改进
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDelete(f)}
                        disabled={deletingId === f.id}
                        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-error transition-colors hover:bg-error/15 disabled:opacity-40"
                        title="删除项目"
                      >
                        {deletingId === f.id ? (
                          <Spinner className="h-3.5 w-3.5" />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5" />
                        )}
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 空状态 */}
      {!loading && flows.length === 0 && !error && (
        <EmptyState
          title="还没有项目"
          hint="点击右上角「+ 新建项目」创建第一个项目。"
        />
      )}

      {!loading && flows.length > 0 && visible.length === 0 && (
        <EmptyState
          title="筛选 / 搜索无结果"
          hint={`筛选「${FILTERS.find((f) => f.key === filter)?.label}」+ 搜索「${query}」下没有匹配项目`}
        />
      )}
    </div>
  )
}
