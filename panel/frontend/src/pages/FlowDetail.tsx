/**
 * FlowDetail — 详情页（006 §3.9，含 GrillMePanel + hash 监听）
 *
 * 布局：
 *   顶部：标题 + 状态徽章 + 步骤进度条
 *   左列：当前时间线 + 完整历史
 *   右列：上下文预览 + DecisionPanel（决策点）+ GrillMePanel（grill-me 嵌入式）
 *
 * hash 监听：location.hash === '#grill' 时自动展开 grill-me 面板
 * grill-me 完成后调用 refetch 拉新流程状态
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { Play } from 'lucide-react'
import { useFlow } from '../hooks/useFlow'
import { useDecide } from '../hooks/useDecide'
import { api } from '../api/client'
import { ContextPreview } from '../components/ContextPreview'
import { DecisionPanel } from '../components/DecisionPanel'
import { GrillMePanel } from '../components/GrillMePanel'
import { HistoryList } from '../components/HistoryList'
import { Spinner } from '../components/ui/Spinner'
import { StatusBadge } from '../components/StatusBadge'
import { StepProgress } from '../components/StepProgress'
import { StepTimeline } from '../components/StepTimeline'
import { toast } from '../components/Toast'

export default function FlowDetail() {
  const { id } = useParams<{ id: string }>()
  const location = useLocation()
  const { flow, loading, error, refetch } = useFlow(id ?? '')
  const { decide, deciding, error: decideError } = useDecide()
  const [showGrill, setShowGrill] = useState(false)
  // 2026-08-22 返工（P2b）：本地防重复提交守卫——拍板成功后立即置位，
  // 当前步骤变化时清除；与 history 判定（alreadyDecided）双保险
  const [justDecided, setJustDecided] = useState(false)

  // hash 监听：/flow/:id#grill 或 /grill/:id 自动展开 grill-me
  useEffect(() => {
    if (location.hash === '#grill') {
      setShowGrill(true)
    }
  }, [location.hash])

  const currentStepDef = useMemo(() => {
    if (!flow) return undefined
    return flow.steps.find((s) => s.id === flow.currentStep)
  }, [flow])

  // 2026-08-22 返工（P1b）：grill-me 在 decision 与 discuss 步骤都显示
  // （原仅 decision，discuss 立项访谈步骤看不到面板 → 新项目无访谈窗口）
  const isDecisionStep = currentStepDef?.type === 'decision'
  const isDiscussStep = currentStepDef?.type === 'discuss'

  // P1b：discuss 步骤自动展开 grill-me 面板（立项访谈是主交互入口）
  useEffect(() => {
    if (isDiscussStep) setShowGrill(true)
  }, [isDiscussStep, id])

  // P2b：步骤变化时清除本地防抖守卫（新一轮拍板允许）
  useEffect(() => {
    setJustDecided(false)
  }, [flow?.currentStep, id])

  // 2026-08-22 返工（P2b）：当前 decision 步骤刚拍过板且引擎尚未推进
  // → 显示「已拍板」并禁用按钮，防止重复提交。
  // 判定收紧：拍板记录必须出现在 history 末尾 3 条内——
  // 回环场景（如 closing on_reject=verify 再回 closing）旧拍板记录
  // 之后必有其它步骤记录，不会落在末尾窗口内，避免误禁用新一轮拍板。
  const alreadyDecided = useMemo(() => {
    if (!flow || currentStepDef?.type !== 'decision') return false
    const recent = flow.history.slice(-3)
    return recent.some(
      (h) => h.step === flow.currentStep && h.action === 'decision',
    )
  }, [flow, currentStepDef])

  const handleDecide = (decisionKey: string) => {
    if (!id) return
    void (async () => {
      const ok = await decide(id, decisionKey)
      if (ok) {
        setJustDecided(true)
        toast.ok(`已拍板 ${decisionKey}，引擎已推进`)
        // 2026-08-22 返工（P2a）：拍板成功后立即 refetch（去掉 1.5s 延迟）——
        // decide 端点已同步触发引擎心跳，返回时步骤通常已推进，
        // 立即刷新可让拍板面板及时消失，避免延迟窗口内误点导致 400
        refetch()
      } else {
        toast.err(`拍板失败：${decideError ?? '未知错误'}`)
      }
    })()
  }

  // mechanism-fix-engine-verify-wait F2：paused 恢复按钮（002 §F2.2）
  const handleResume = () => {
    if (!id) return
    void (async () => {
      try {
        await api.resumeFlow(id)
        toast.ok(`已恢复流程 ${id}`)
        window.setTimeout(() => refetch(), 800)
      } catch (err) {
        toast.err(`恢复失败：${err instanceof Error ? err.message : String(err)}`)
      }
    })()
  }

  // 是否在 decision/discuss 步骤——两者都显示 grill-me 面板（P1b）
  const isGrillStep = isDecisionStep || isDiscussStep

  if (loading && !flow) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="h-8 w-8" />
      </div>
    )
  }

  if (error || !flow) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <div className="rounded-[10px] border border-line bg-bg-2 p-6">
          <p className="text-sm text-rose-300">
            无法加载流程详情：{error ?? '未知错误'}
          </p>
          <Link
            to="/"
            className="mt-3 inline-block text-xs text-accent hover:underline"
          >
            ← 返回 Dashboard
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 p-6">
      {/* 顶部标题 */}
      <div>
        <Link
          to="/"
          className="text-xs text-fg-3 hover:text-fg-1"
        >
          ← Dashboard
        </Link>
        <div className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-fg-1">{flow.title}</h1>
            <p className="mt-0.5 font-mono-id text-xs text-fg-3">{flow.id}</p>
          </div>
          <div className="flex items-center gap-2">
            {flow.status === 'paused' && (
              <button
                type="button"
                onClick={handleResume}
                className="inline-flex items-center gap-1 rounded bg-warning/15 px-2.5 py-1 text-xs font-medium text-warning transition-colors hover:bg-warning/25"
                title="恢复 paused 流程"
              >
                <Play className="h-3.5 w-3.5" />
                恢复
              </button>
            )}
            <StatusBadge status={flow.status} />
          </div>
        </div>
      </div>

      {/* 横向步骤进度条 */}
      <div className="rounded-[10px] border border-line bg-bg-2 p-4 shadow-[0_1px_2px_hsl(222,30%,4%)]">
        <div className="mb-2 flex items-baseline justify-between">
          <span className="text-[10px] uppercase tracking-wider text-fg-3">
            步骤进度
          </span>
          <span className="font-mono-id text-xs text-fg-2">
            {flow.currentStepIndex} / {flow.totalSteps} · 迭代 #{flow.iteration}
          </span>
        </div>
        <StepProgress steps={flow.steps} currentStepId={flow.currentStep} />
      </div>

      {/* 左右分栏 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        {/* 左：时间线 + 历史 */}
        <div className="flex flex-col gap-4 lg:col-span-3">
          <div className="rounded-[10px] border border-line bg-bg-2 p-4 shadow-[0_1px_2px_hsl(222,30%,4%)]">
            <h2 className="mb-3 text-xs uppercase tracking-wider text-fg-3">
              当前时间线（最近 {Math.min(6, flow.history.length)} 条）
            </h2>
            <StepTimeline history={flow.history} limit={6} />
          </div>

          <div className="rounded-[10px] border border-line bg-bg-2 p-4 shadow-[0_1px_2px_hsl(222,30%,4%)]">
            <h2 className="mb-3 text-xs uppercase tracking-wider text-fg-3">
              完整历史（{flow.history.length} 条）
            </h2>
            <HistoryList history={flow.history} />
          </div>
        </div>

        {/* 右：上下文 + 拍板 + grill-me */}
        <div className="flex flex-col gap-4 lg:col-span-2">
          <div className="rounded-[10px] border border-line bg-bg-2 p-4 shadow-[0_1px_2px_hsl(222,30%,4%)]">
            <h2 className="mb-3 text-xs uppercase tracking-wider text-fg-3">
              上下文预览（{flow.contextFiles.length}）
            </h2>
            <ContextPreview files={flow.contextFiles} />
          </div>

          {/* 2026-08-22 返工（P2a）：DecisionPanel 仅在 decision 步骤渲染。
              原实现无条件渲染 → 引擎推进到非 decision 步骤后仍显示「待拍板」，
              再点按钮后端返回 400「当前不在拍板点」。
              （P2b）当前 decision 步骤已拍过板 → 显示等待推进提示，禁用按钮。 */}
          {isDecisionStep ? (
            alreadyDecided || justDecided ? (
              <div className="rounded-lg border border-success/30 bg-success/5 p-4">
                <p className="mb-1 text-xs font-medium uppercase tracking-wider text-success">
                  ✅ 已拍板
                </p>
                <p className="text-sm text-fg-2">
                  拍板已提交，等待引擎推进到下一步骤…
                </p>
              </div>
            ) : (
              <DecisionPanel
                step={currentStepDef}
                onDecide={handleDecide}
                deciding={deciding}
              />
            )
          ) : (
            <div className="rounded-[10px] border border-line bg-bg-2 p-4">
              <p className="text-xs text-fg-3">
                {isDiscussStep
                  ? '当前处于立项访谈阶段（discuss）——请在下方 grill-me 面板完成需求访谈'
                  : '当前步骤不在决策点'}
              </p>
            </div>
          )}

          {/* GrillMePanel：decision + discuss 步骤均显示（P1b），可折叠 */}
          {isGrillStep && (
            <div className="rounded-[10px] border border-line bg-bg-2 p-3">
              <div className="mb-2 flex items-baseline justify-between">
                <span className="text-[10px] uppercase tracking-wider text-fg-3">
                  {isDiscussStep ? '立项访谈（设计树）' : '延伸追问'}
                </span>
                {showGrill && (
                  <button
                    type="button"
                    onClick={() => setShowGrill(false)}
                    className="text-[10px] text-fg-3 hover:text-fg-1"
                  >
                    折叠
                  </button>
                )}
              </div>
              {showGrill ? (
                <GrillMePanel
                  flowId={flow.id}
                  onClose={() => setShowGrill(false)}
                  onDone={() => window.setTimeout(() => refetch(), 800)}
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setShowGrill(true)}
                  className="w-full rounded-md border border-dashed border-line px-3 py-2 text-xs text-fg-2 transition-colors hover:border-accent/50 hover:bg-accent/5 hover:text-fg-1"
                >
                  展开 grill-me 面板 →
                </button>
              )}
            </div>
          )}

          <div className="rounded-[10px] border border-line bg-bg-2 p-3">
            <p className="text-[11px] leading-relaxed text-fg-3">
              拍板成功后立即刷新流程状态（引擎已同步推进）。
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}