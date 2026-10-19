/**
 * FlowTemplates — 流程模板库页（2026-08-23 项目化改造 P3 新增）
 *
 * 路由 /templates
 *
 * 作用：项目立项后，选择「用哪套流程执行该项目」——任务分派、监察、
 * 验收均按所选模板推进。本页列出全部可用模板（flows/template-*.yaml，
 * meta 段驱动），并支持把模板绑定到指定项目。
 *
 * 结构：
 *   顶部：标题 + 说明
 *   主体：模板卡片网格（title/description/category/steps/decisionPoints）
 *   每卡操作：「绑定到项目…」→ 行内选择器选目标项目 → 确认绑定
 *
 * 数据源：GET /api/flow-templates + GET /api/flows（项目下拉）
 */
import { useEffect, useState } from 'react'
import { GitBranch, Layers, Scale } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { Spinner } from '../components/ui/Spinner'
import { EmptyState } from '../components/ui/EmptyState'
import { toast } from '../components/Toast'
import type { FlowSummary, FlowTemplateMeta } from '../types'

/** 类目徽标配色（tailwind 任意值类，与暗色主题协调） */
const CATEGORY_STYLE: Record<string, { label: string; cls: string }> = {
  standard: { label: '标准', cls: 'border-accent/40 bg-accent/10 text-accent' },
  bugfix: { label: '缺陷修复', cls: 'border-error/40 bg-error/10 text-error' },
  feature: { label: '功能迭代', cls: 'border-success/40 bg-success/10 text-success' },
  research: { label: '调研验证', cls: 'border-fg-3/40 bg-bg-3 text-fg-2' },
  general: { label: '通用', cls: 'border-line bg-bg-3 text-fg-2' },
}

function CategoryBadge({ category }: { category: string }) {
  const s = CATEGORY_STYLE[category] ?? CATEGORY_STYLE.general
  return (
    <span className={`rounded border px-1.5 py-px text-[10px] ${s.cls}`}>
      {s.label}
    </span>
  )
}

export default function FlowTemplates() {
  const [templates, setTemplates] = useState<FlowTemplateMeta[]>([])
  const [projects, setProjects] = useState<FlowSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  /** 正在为哪个模板选目标项目（null = 无绑定面板展开） */
  const [bindingId, setBindingId] = useState<string | null>(null)
  const [targetProject, setTargetProject] = useState('')
  const [binding, setBinding] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const [ts, ps] = await Promise.all([api.listFlowTemplates(), api.listFlows()])
        if (!cancelled) {
          setTemplates(ts)
          setProjects(ps)
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof ApiError ? e.message : String(e))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  const handleBind = async (tpl: FlowTemplateMeta) => {
    if (!targetProject) {
      toast.warn('请选择要应用该模板的项目')
      return
    }
    setBinding(true)
    try {
      await api.bindTemplate(targetProject, tpl.id)
      toast.ok(`已为「${targetProject}」绑定模板「${tpl.title}」`)
      setBindingId(null)
      setTargetProject('')
    } catch (e) {
      toast.err(
        `绑定失败：${e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)}`,
      )
    } finally {
      setBinding(false)
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
      <div>
        <h1 className="text-lg font-semibold text-fg-1">流程模板</h1>
        <p className="mt-0.5 text-xs text-fg-3">
          立项后为项目选择执行流程：任务分派、监察、验收均按该模板进行。
          绑定即生效，可重复绑定覆盖。
        </p>
      </div>

      {/* 错误 */}
      {error && (
        <div className="rounded-lg border border-error/30 bg-error/10 p-3 text-xs text-error">
          加载失败：{error}
        </div>
      )}

      {/* 加载 */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <Spinner className="h-8 w-8" />
        </div>
      )}

      {/* 模板卡片网格 */}
      {!loading && templates.length > 0 && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {templates.map((t) => {
            const expanded = bindingId === t.id
            return (
              <div
                key={t.id}
                className={`flex flex-col gap-2 rounded-lg border bg-bg-2 p-4 transition-colors ${
                  expanded ? 'border-accent' : 'border-line hover:border-fg-3/50'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <GitBranch className="h-3.5 w-3.5 shrink-0 text-accent" />
                      <span className="truncate text-sm font-medium text-fg-1">
                        {t.title}
                      </span>
                    </div>
                    <p className="mt-0.5 font-mono-id text-[10px] text-fg-3">{t.id}</p>
                  </div>
                  <CategoryBadge category={t.category} />
                </div>

                <p className="text-xs leading-relaxed text-fg-2">{t.description}</p>

                <div className="flex items-center gap-3 text-[11px] text-fg-3">
                  <span className="inline-flex items-center gap-1">
                    <Layers className="h-3 w-3" />
                    {t.steps} 步骤
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <Scale className="h-3 w-3" />
                    {t.decisionPoints} 个拍板点
                  </span>
                </div>

                {/* 绑定面板 */}
                {expanded ? (
                  <div className="flex flex-col gap-1.5 rounded-md border border-line bg-bg-3 p-2">
                    <select
                      value={targetProject}
                      onChange={(e) => setTargetProject(e.target.value)}
                      disabled={binding}
                      className="w-full rounded border border-line bg-bg-2 px-2 py-1.5 text-xs text-fg-1 focus:border-accent focus:outline-none disabled:opacity-50"
                    >
                      <option value="">— 选择项目 —</option>
                      {projects.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.title}（{p.id}）
                        </option>
                      ))}
                    </select>
                    <div className="flex justify-end gap-1.5">
                      <button
                        type="button"
                        onClick={() => {
                          setBindingId(null)
                          setTargetProject('')
                        }}
                        disabled={binding}
                        className="rounded px-2 py-1 text-[11px] text-fg-2 transition-colors hover:bg-bg-2 disabled:opacity-40"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleBind(t)}
                        disabled={binding || !targetProject}
                        className="inline-flex items-center gap-1 rounded bg-accent px-2 py-1 text-[11px] font-medium text-white transition-colors hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {binding && <Spinner className="h-3 w-3" />}
                        {binding ? '绑定中…' : '确认绑定'}
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setBindingId(t.id)
                      setTargetProject('')
                    }}
                    className="mt-auto inline-flex items-center justify-center gap-1 rounded-md border border-line px-2 py-1.5 text-xs text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1"
                  >
                    绑定到项目…
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}

      {!loading && templates.length === 0 && !error && (
        <EmptyState
          title="暂无可用模板"
          hint="在 flow-engine/engine/flows/ 下放置 template-*.yaml（含 meta 段）后刷新。"
        />
      )}
    </div>
  )
}
