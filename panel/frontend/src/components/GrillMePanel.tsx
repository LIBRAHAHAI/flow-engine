/**
 * GrillMePanel — grill-me 多轮交互面板（006 §3.4 + §4.2；grillme-deep 002 §4）
 *
 * 双模式（002 §4.1，按 grill-context.mode 分支渲染）：
 *   - decision（mode 缺失/'decision'）：既有 turns UI，一字不动——
 *     确认/修改/跳过三按钮逐轮处理（P2a/P2b 已验收行为不得破坏）
 *   - interview（discuss 设计树访谈）：
 *       进度条 + 当前节点卡（一次一问 + 推荐答案 + 回答 textarea）+
 *       LLM 追问流 + 「停止追问」常驻按钮 + spec 确认区 +
 *       断点续谈（挂载即 GET /grill-context，从 INTERVIEW.json 重建）
 *
 * decision 状态机：useReducer（006 §4.2）
 *   idle → loading → ready → editing/submitting → ready/done/error
 *
 * 不持久化到 localStorage（避免与引擎实例不一致，006 §4.1.3 注释；
 * 002 §3.2：INTERVIEW.json 是唯一真源）
 *
 * Props：
 *   flowId  —— 当前流程 id
 *   onClose —— 全部完成后父组件回调（用于折叠面板）
 *   onDone  —— 全部完成后通知父组件 refetch flow（可选）
 */
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { Check, Pencil, SkipForward, X, RefreshCw, AlertCircle, OctagonX, FileCheck, MessageCircleQuestion } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { Button } from './ui/Button'
import { Spinner } from './ui/Spinner'
import { toast } from './Toast'
import type { GrillMeInterviewContext, GrillMeTurn } from '../types'

// ── 状态机 ──────────────────────────────────────────────

type Status = 'idle' | 'loading' | 'ready' | 'editing' | 'submitting' | 'done' | 'error'

interface State {
  status: Status
  sessionId: string | null
  turns: GrillMeTurn[]
  currentTurnIndex: number
  editedText: string
  errorMsg: string | null
}

type Action =
  | { type: 'LOAD_START' }
  | { type: 'LOAD_SUCCESS'; sessionId: string; turns: GrillMeTurn[] }
  | { type: 'LOAD_ERROR'; msg: string }
  | { type: 'EDIT_BEGIN'; text: string }
  | { type: 'EDIT_CHANGE'; text: string }
  | { type: 'EDIT_CANCEL' }
  | { type: 'SUBMIT_START' }
  | { type: 'SUBMIT_NEXT'; nextTurn: GrillMeTurn | null; done: boolean }
  | { type: 'SUBMIT_ERROR'; msg: string }
  | { type: 'CLOSE' }
  | { type: 'RESET' }

const INITIAL: State = {
  status: 'idle',
  sessionId: null,
  turns: [],
  currentTurnIndex: -1,
  editedText: '',
  errorMsg: null,
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'LOAD_START':
      return { ...state, status: 'loading', errorMsg: null }
    case 'LOAD_SUCCESS':
      return {
        ...state,
        status: 'ready',
        sessionId: action.sessionId,
        turns: action.turns,
        currentTurnIndex: 0,
        editedText: '',
        errorMsg: null,
      }
    case 'LOAD_ERROR':
      return { ...state, status: 'error', errorMsg: action.msg }
    case 'EDIT_BEGIN':
      return { ...state, status: 'editing', editedText: action.text }
    case 'EDIT_CHANGE':
      return { ...state, editedText: action.text }
    case 'EDIT_CANCEL':
      return { ...state, status: 'ready', editedText: '' }
    case 'SUBMIT_START':
      return { ...state, status: 'submitting', errorMsg: null }
    case 'SUBMIT_NEXT':
      if (action.done) {
        return { ...state, status: 'done' }
      }
      // 把当前 turn 标记为已提交（推到 turns 末尾），推进到下一轮
      return {
        ...state,
        status: 'ready',
        turns: action.nextTurn
          ? [...state.turns, action.nextTurn]
          : state.turns,
        currentTurnIndex: state.currentTurnIndex + 1,
        editedText: '',
      }
    case 'SUBMIT_ERROR':
      return { ...state, status: 'error', errorMsg: action.msg }
    case 'CLOSE':
    case 'RESET':
      return INITIAL
    default:
      return state
  }
}

// ── 组件 ─────────────────────────────────────────────────

interface GrillMePanelProps {
  flowId: string
  onClose?: () => void
  onDone?: () => void
}

export function GrillMePanel({ flowId, onClose, onDone }: GrillMePanelProps) {
  // grillme-deep（002 §4.1）：先探 mode——interview 渲染访谈分支，
  // decision/未触发走既有 turns UI（一字不动）。
  const [mode, setMode] = useState<'unknown' | 'decision' | 'interview'>('unknown')
  const [modeChecked, setModeChecked] = useState(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const ctx = await api.getGrillMeContext(flowId)
        if (cancelled) return
        if (ctx.active && ctx.mode === 'interview') {
          setMode('interview')
        } else {
          setMode('decision')
        }
      } catch {
        if (cancelled) return
        // 探测失败 → 回退 decision 分支（其自带错误态 UI）
        setMode('decision')
      } finally {
        if (!cancelled) setModeChecked(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [flowId])

  if (!modeChecked) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-lg border border-line bg-bg-2 p-6 text-xs text-fg-3">
        <Spinner className="h-4 w-4" />
        正在探测 grill-me 模式…
      </div>
    )
  }

  if (mode === 'interview') {
    return <InterviewPanel flowId={flowId} onClose={onClose} onDone={onDone} />
  }

  return <DecisionGrillPanel flowId={flowId} onClose={onClose} onDone={onDone} />
}

// ── decision 分支（既有实现，一字未动） ────────────────────

function DecisionGrillPanel({ flowId, onClose, onDone }: GrillMePanelProps) {
  const [state, dispatch] = useReducer(reducer, INITIAL)

  // 进入完成态：通知父组件 + toast
  useEffect(() => {
    if (state.status === 'done') {
      toast.ok('grill-me 对话完成')
      onDone?.()
    }
  }, [state.status, onDone])

  // ── 动作 ──────────────────────────────────────────────

  const startGrill = async () => {
    dispatch({ type: 'LOAD_START' })
    try {
      const ctx = await api.getGrillMeContext(flowId)
      if (!ctx.active) {
        dispatch({ type: 'LOAD_ERROR', msg: '当前步骤未触发 grill-me' })
        return
      }
      if (ctx.turns.length === 0) {
        dispatch({ type: 'LOAD_SUCCESS', sessionId: ctx.sessionId, turns: [] })
        // 没有 turn 但 active=true，直接 done
        dispatch({ type: 'SUBMIT_NEXT', nextTurn: null, done: true })
        return
      }
      dispatch({
        type: 'LOAD_SUCCESS',
        sessionId: ctx.sessionId,
        turns: ctx.turns,
      })
    } catch (e) {
      dispatch({
        type: 'LOAD_ERROR',
        msg: e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e),
      })
    }
  }

  const submitTurn = async (
    action: 'confirm' | 'skip' | 'edit',
    userText?: string,
  ) => {
    if (!state.sessionId) return
    if (action === 'edit' && !userText?.trim()) {
      toast.warn('编辑内容不能为空')
      return
    }
    dispatch({ type: 'SUBMIT_START' })
    try {
      const res = await api.submitGrillMeTurn(flowId, {
        sessionId: state.sessionId,
        turnIndex: state.currentTurnIndex,
        action,
        userText: action === 'edit' ? userText : undefined,
      })
      if (res.status !== 'ok') {
        dispatch({ type: 'SUBMIT_ERROR', msg: '引擎返回非 ok 状态' })
        return
      }
      dispatch({
        type: 'SUBMIT_NEXT',
        nextTurn: res.nextTurn,
        done: res.done,
      })
    } catch (e) {
      dispatch({
        type: 'SUBMIT_ERROR',
        msg: e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e),
      })
    }
  }

  // ── 渲染分支 ──────────────────────────────────────────

  if (state.status === 'idle') {
    return (
      <div className="rounded-lg border border-line bg-bg-2 p-4">
        <h3 className="mb-1 text-sm font-medium text-fg-1">延伸追问（grill-me）</h3>
        <p className="mb-3 text-xs text-fg-3">
          引擎会针对当前决策点生成若干追问，并给出推荐答案。你可以选择「确认」/「修改」/「跳过」逐一处理。
        </p>
        <Button variant="primary" onClick={() => void startGrill()}>
          开始 grill-me 对话
        </Button>
      </div>
    )
  }

  if (state.status === 'loading') {
    return (
      <div className="flex items-center justify-center gap-2 rounded-lg border border-line bg-bg-2 p-6 text-xs text-fg-3">
        <Spinner className="h-4 w-4" />
        正在拉取 grill-me 上下文…
      </div>
    )
  }

  if (state.status === 'error') {
    return (
      <div className="rounded-lg border border-error/30 bg-error/10 p-4">
        <div className="flex items-start gap-2 text-xs text-error">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="flex-1">
            <p className="font-medium">grill-me 失败：{state.errorMsg}</p>
          </div>
        </div>
        <div className="mt-3 flex gap-2">
          <Button variant="secondary" onClick={() => void startGrill()}>
            <RefreshCw className="h-3.5 w-3.5" /> 重试
          </Button>
          <Button variant="ghost" onClick={() => dispatch({ type: 'RESET' })}>
            关闭面板
          </Button>
        </div>
      </div>
    )
  }

  if (state.status === 'done') {
    return (
      <div className="rounded-lg border border-success/30 bg-success/10 p-4">
        <div className="flex items-center gap-2 text-sm text-success">
          <Check className="h-4 w-4" />
          grill-me 对话完成
        </div>
        <p className="mt-2 text-xs text-fg-3">
          共完成 {state.turns.length} 轮，所有推荐答案已记录到引擎实例。
        </p>
        <div className="mt-3 flex gap-2">
          <Button variant="secondary" onClick={onClose}>
            关闭面板
          </Button>
        </div>
      </div>
    )
  }

  // ready / editing / submitting：渲染当前轮 TurnCard
  const currentTurn = state.turns[state.currentTurnIndex]
  if (!currentTurn) {
    return (
      <div className="rounded-lg border border-line bg-bg-2 p-4 text-xs text-fg-3">
        没有可显示的轮次。
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-line bg-bg-2 p-4">
      {/* 头部：进度 */}
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-fg-1">延伸追问（grill-me）</h3>
        <span className="font-mono-id text-[10px] text-fg-3">
          第 {state.currentTurnIndex + 1} / {state.turns.length} 轮
        </span>
      </div>

      {/* 问题 */}
      <div className="mb-3 rounded-md border border-line bg-bg-3 p-3">
        <p className="text-[10px] uppercase tracking-wider text-fg-3">Q</p>
        <p className="mt-1 text-sm text-fg-1">{currentTurn.question}</p>
        {currentTurn.contextRefs.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {currentTurn.contextRefs.map((ref) => (
              <span
                key={ref}
                className="rounded bg-bg-2 px-1.5 py-0.5 font-mono-id text-[10px] text-fg-3"
                title={ref}
              >
                📎 {ref.split(/[/\\]/).slice(-2).join('/')}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 推荐答案 / 编辑区 */}
      {state.status === 'editing' ? (
        <div className="mb-3">
          <p className="mb-1 text-[10px] uppercase tracking-wider text-fg-3">
            修改答案
          </p>
          <textarea
            value={state.editedText}
            onChange={(e) =>
              dispatch({ type: 'EDIT_CHANGE', text: e.target.value })
            }
            disabled={state.status !== 'editing'}
            rows={5}
            className="w-full resize-y rounded-md border border-line bg-bg-3 px-3 py-2 text-sm text-fg-1 focus:border-accent focus:outline-none"
          />
        </div>
      ) : (
        <div className="mb-3 rounded-md border border-accent/30 bg-accent/5 p-3">
          <p className="text-[10px] uppercase tracking-wider text-accent">
            推荐答案
          </p>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-fg-1">
            {currentTurn.recommendedAnswer}
          </p>
        </div>
      )}

      {/* 操作按钮 */}
      {state.status === 'editing' ? (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={!state.editedText.trim()}
            onClick={() => void submitTurn('edit', state.editedText.trim())}
          >
            <Check className="h-3.5 w-3.5" /> 保存并提交
          </Button>
          <Button
            variant="ghost"
            onClick={() => dispatch({ type: 'EDIT_CANCEL' })}
          >
            <X className="h-3.5 w-3.5" /> 取消修改
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={state.status === 'submitting'}
            onClick={() => void submitTurn('confirm')}
          >
            {state.status === 'submitting' ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : (
              <Check className="h-3.5 w-3.5" />
            )}
            确认
          </Button>
          <Button
            variant="secondary"
            disabled={state.status === 'submitting'}
            onClick={() =>
              dispatch({
                type: 'EDIT_BEGIN',
                text: currentTurn.recommendedAnswer,
              })
            }
          >
            <Pencil className="h-3.5 w-3.5" /> 修改
          </Button>
          <Button
            variant="ghost"
            disabled={state.status === 'submitting'}
            onClick={() => void submitTurn('skip')}
          >
            <SkipForward className="h-3.5 w-3.5" /> 跳过
          </Button>
        </div>
      )}
    </div>
  )
}

export default GrillMePanel

// ── interview 分支（grillme-deep 002 §4.2，全新实现） ──────

type IvStatus = 'loading' | 'ready' | 'submitting' | 'stopping' | 'confirming' | 'error'

interface IvState {
  status: IvStatus
  sessionId: string
  interview: GrillMeInterviewContext | null
  treeComplete: boolean
  answerText: string
  followupText: string
  /** 正在提交的追问 index（null = 节点首问提交中 / 无） */
  submittingFollowup: number | null
  specFile: string | null
  stopSummary: string | null
  confirmStop: boolean
  /** 一次性降级提示（002 §2.3：读后已清，仅本会话展示一次） */
  degradedBanner: string | null
  errorMsg: string | null
}

const IV_INITIAL: IvState = {
  status: 'loading',
  sessionId: '',
  interview: null,
  treeComplete: false,
  answerText: '',
  followupText: '',
  submittingFollowup: null,
  specFile: null,
  stopSummary: null,
  confirmStop: false,
  degradedBanner: null,
  errorMsg: null,
}

/**
 * discuss 设计树访谈面板（002 §4.2 S1-S4）：
 *   1. 进度条（answered/total）
 *   2. 当前节点卡：骨架问题 + 推荐答案 + textarea + 提交/跳过
 *   3. LLM 追问流：已答灰显，未答待答位（一次一问）
 *   4. 「停止追问」常驻按钮（二次确认 → grill-stop → spec 确认区）
 *   5. spec 确认区（树全覆盖 / stopped / status=spec_draft → 确认共识 → grill-confirm）
 *   6. 一次性降级提示条（llmDegraded）
 * 断点续谈（G4，002 §3.2）：挂载即 getGrillMeContext，从 INTERVIEW.json 重建。
 */
function InterviewPanel({ flowId, onClose, onDone }: GrillMePanelProps) {
  const [st, setSt] = useState<IvState>(IV_INITIAL)
  const patch = useCallback((p: Partial<IvState>) => setSt((s) => ({ ...s, ...p })), [])
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const errMsg = (e: unknown) =>
    e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)

  // ── 断点续谈：挂载自动拉取（002 §4.3：discuss 无「开始」按钮） ──
  const loadContext = useCallback(async () => {
    try {
      const ctx = await api.getGrillMeContext(flowId)
      if (!mountedRef.current) return
      if (!ctx.active || ctx.mode !== 'interview' || !ctx.interview) {
        patch({ status: 'error', errorMsg: '当前步骤未触发设计树访谈' })
        return
      }
      const iv = ctx.interview
      patch({
        status: 'ready',
        sessionId: ctx.sessionId,
        interview: iv,
        treeComplete: iv.nodeKey === null,
        answerText: '',
        followupText: '',
        submittingFollowup: null,
        degradedBanner: iv.llmDegraded
          ? `LLM 追问暂不可用（${iv.degradedReason || '未知原因'}），已切换骨架问题`
          : null,
        errorMsg: null,
      })
    } catch (e) {
      if (!mountedRef.current) return
      patch({ status: 'error', errorMsg: errMsg(e) })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowId, patch])

  useEffect(() => {
    void loadContext()
  }, [loadContext])

  // ── 提交（answer_node / answer_followup / skip_node，002 §1.3） ──
  const applyResult = useCallback(
    (iv: GrillMeInterviewContext, treeComplete: boolean) => {
      patch({
        status: 'ready',
        interview: iv,
        treeComplete,
        answerText: '',
        followupText: '',
        submittingFollowup: null,
        degradedBanner: iv.llmDegraded
          ? `LLM 追问暂不可用（${iv.degradedReason || '未知原因'}），已切换骨架问题`
          : null,
      })
    },
    [patch],
  )

  const submitNode = async () => {
    const iv = st.interview
    if (!iv?.nodeKey) return
    const text = st.answerText.trim()
    if (!text) {
      toast.warn('回答内容不能为空（或用「跳过」采用推荐答案）')
      return
    }
    patch({ status: 'submitting', submittingFollowup: null })
    try {
      const res = await api.submitInterviewTurn(flowId, {
        sessionId: st.sessionId,
        mode: 'interview',
        action: 'answer_node',
        nodeKey: iv.nodeKey,
        userText: text,
      })
      applyResult(res.interview, res.treeComplete)
    } catch (e) {
      patch({ status: 'ready' })
      toast.err(`提交失败：${errMsg(e)}`)
    }
  }

  const submitFollowup = async (index: number) => {
    const iv = st.interview
    if (!iv?.nodeKey) return
    const text = st.followupText.trim()
    if (!text) {
      toast.warn('回答内容不能为空')
      return
    }
    patch({ status: 'submitting', submittingFollowup: index })
    try {
      const res = await api.submitInterviewTurn(flowId, {
        sessionId: st.sessionId,
        mode: 'interview',
        action: 'answer_followup',
        nodeKey: iv.nodeKey,
        followupIndex: index,
        userText: text,
      })
      applyResult(res.interview, res.treeComplete)
    } catch (e) {
      patch({ status: 'ready', submittingFollowup: null })
      toast.err(`提交失败：${errMsg(e)}`)
    }
  }

  const skipNode = async () => {
    const iv = st.interview
    if (!iv?.nodeKey) return
    patch({ status: 'submitting', submittingFollowup: null })
    try {
      const res = await api.submitInterviewTurn(flowId, {
        sessionId: st.sessionId,
        mode: 'interview',
        action: 'skip_node',
        nodeKey: iv.nodeKey,
      })
      applyResult(res.interview, res.treeComplete)
      toast.ok('已跳过本节点，采用推荐答案')
    } catch (e) {
      patch({ status: 'ready' })
      toast.err(`跳过失败：${errMsg(e)}`)
    }
  }

  // ── 停止追问（G3，002 §1.4：二次确认 → grill-stop） ──
  const stopInterview = async () => {
    patch({ status: 'stopping', confirmStop: false })
    try {
      const res = await api.stopGrillMe(flowId, st.sessionId)
      toast.ok(res.summary || '已冻结问题流')
      patch({ specFile: res.specFile, stopSummary: res.summary })
      await loadContext()
    } catch (e) {
      patch({ status: 'ready' })
      toast.err(`停止失败：${errMsg(e)}`)
    }
  }

  // ── 确认共识（G5，002 §1.5：grill-confirm → 引擎推进） ──
  const confirmSpec = async () => {
    patch({ status: 'confirming' })
    try {
      await api.confirmGrillMe(flowId, st.sessionId)
      toast.ok('已确认，引擎推进中')
      patch({ status: 'ready' })
      onDone?.()
    } catch (e) {
      patch({ status: 'ready' })
      toast.err(`确认失败：${errMsg(e)}`)
    }
  }

  // ── 渲染 ──

  if (st.status === 'loading') {
    return (
      <div className="flex items-center justify-center gap-2 rounded-lg border border-line bg-bg-2 p-6 text-xs text-fg-3">
        <Spinner className="h-4 w-4" />
        正在恢复访谈现场（断点续谈）…
      </div>
    )
  }

  if (st.status === 'error') {
    return (
      <div className="rounded-lg border border-error/30 bg-error/10 p-4">
        <div className="flex items-start gap-2 text-xs text-error">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="flex-1">
            <p className="font-medium">设计树访谈加载失败：{st.errorMsg}</p>
          </div>
        </div>
        <div className="mt-3 flex gap-2">
          <Button variant="secondary" onClick={() => void loadContext()}>
            <RefreshCw className="h-3.5 w-3.5" /> 重试
          </Button>
          <Button variant="ghost" onClick={onClose}>
            关闭面板
          </Button>
        </div>
      </div>
    )
  }

  const iv = st.interview
  if (!iv) return null

  const busy = st.status === 'submitting' || st.status === 'stopping' || st.status === 'confirming'
  // spec 确认区可见（002 §4.2-5）：树全覆盖 / stopped / spec_draft
  const specZoneVisible =
    st.treeComplete || iv.stopped || iv.status === 'spec_draft' || st.specFile !== null
  const pendingFollowup = iv.followups.find((f) => !f.answered) ?? null

  return (
    <div className="rounded-lg border border-line bg-bg-2 p-4">
      {/* 头部：标题 + 进度条 + 停止追问（常驻，002 §4.2-4） */}
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-fg-1">立项访谈（设计树）</h3>
        <div className="flex items-center gap-2">
          <span className="font-mono-id text-[10px] text-fg-3">
            {iv.progress.answered} / {iv.progress.total} 节点
          </span>
          {!iv.stopped && !specZoneVisible && (
            <Button
              variant="danger"
              disabled={busy}
              onClick={() => patch({ confirmStop: true })}
              title="冻结问题流：未答节点采用推荐答案并生成 spec"
            >
              <OctagonX className="h-3.5 w-3.5" /> 停止追问
            </Button>
          )}
        </div>
      </div>

      {/* 进度条（002 §4.2-1） */}
      <div className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-bg-3">
        <div
          className="h-full rounded-full bg-accent transition-all"
          style={{
            width: `${iv.progress.total > 0 ? (iv.progress.answered / iv.progress.total) * 100 : 0}%`,
          }}
        />
      </div>

      {/* 一次性降级提示条（002 §4.2-6 / §2.3） */}
      {st.degradedBanner && (
        <div className="mb-3 flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 p-2.5 text-xs text-warning">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">{st.degradedBanner}</span>
          <button
            type="button"
            className="text-warning/70 hover:text-warning"
            onClick={() => patch({ degradedBanner: null })}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* 停止追问二次确认 */}
      {st.confirmStop && (
        <div className="mb-3 rounded-md border border-error/40 bg-error/10 p-3">
          <p className="mb-2 text-xs text-error">
            确认停止追问？未回答的节点将采用推荐答案并立即生成 spec。
          </p>
          <div className="flex gap-2">
            <Button
              variant="danger"
              disabled={st.status === 'stopping'}
              onClick={() => void stopInterview()}
            >
              {st.status === 'stopping' ? (
                <Spinner className="h-3.5 w-3.5" />
              ) : (
                <OctagonX className="h-3.5 w-3.5" />
              )}
              确认停止
            </Button>
            <Button variant="ghost" onClick={() => patch({ confirmStop: false })}>
              再想想
            </Button>
          </div>
        </div>
      )}

      {/* 当前节点卡（一次一问 + 推荐答案，002 §4.2-2） */}
      {!specZoneVisible && iv.nodeKey && (
        <>
          <div className="mb-3 rounded-md border border-line bg-bg-3 p-3">
            <p className="text-[10px] uppercase tracking-wider text-fg-3">
              当前问题 · {iv.nodeKey}
            </p>
            <p className="mt-1 text-sm text-fg-1">{iv.nodeQuestion}</p>
            {iv.recommendedAnswer && (
              <p className="mt-2 rounded border border-accent/30 bg-accent/5 p-2 text-xs leading-relaxed text-fg-2">
                ➡️ {iv.recommendedAnswer}
              </p>
            )}
          </div>

          {/* 节点首问未答：回答区；已答但挂追问：提示（一次一问由追问流接管） */}
          {!iv.nodeAnswered ? (
            <div className="mb-3">
              <textarea
                value={st.answerText}
                onChange={(e) => patch({ answerText: e.target.value })}
                disabled={busy}
                rows={4}
                placeholder="写下你的回答…（提交后 LLM 可能针对回答继续追问，最长 25s）"
                className="w-full resize-y rounded-md border border-line bg-bg-3 px-3 py-2 text-sm text-fg-1 focus:border-accent focus:outline-none"
              />
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  variant="primary"
                  disabled={busy || !st.answerText.trim()}
                  onClick={() => void submitNode()}
                >
                  {st.status === 'submitting' && st.submittingFollowup === null ? (
                    <Spinner className="h-3.5 w-3.5" />
                  ) : (
                    <Check className="h-3.5 w-3.5" />
                  )}
                  {st.status === 'submitting' ? '记录中…（可能生成追问，最长 25s）' : '提交'}
                </Button>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void skipNode()}
                  title="采用推荐答案并推进到下一节点"
                >
                  <SkipForward className="h-3.5 w-3.5" /> 跳过（用推荐）
                </Button>
              </div>
            </div>
          ) : null}

          {/* 追问流（002 §4.2-3）：已答灰显，未答待答位 */}
          {iv.followups.length > 0 && (
            <div className="mb-2">
              <p className="mb-2 flex items-center gap-1 text-[10px] uppercase tracking-wider text-fg-3">
                <MessageCircleQuestion className="h-3 w-3" /> 追问澄清（{iv.followups.length}）
              </p>
              <div className="flex flex-col gap-2">
                {iv.followups.map((fu) =>
                  fu.answered ? (
                    <div
                      key={fu.index}
                      className="rounded-md border border-line bg-bg-3/50 p-2.5 opacity-60"
                    >
                      <p className="text-xs text-fg-2">Q：{fu.question}</p>
                      <p className="mt-1 text-xs text-fg-3">A：{fu.answer}</p>
                    </div>
                  ) : (
                    <div
                      key={fu.index}
                      className="rounded-md border border-accent/40 bg-accent/5 p-2.5"
                    >
                      <p className="text-xs font-medium text-fg-1">追问：{fu.question}</p>
                      {pendingFollowup?.index === fu.index && (
                        <>
                          <textarea
                            value={st.followupText}
                            onChange={(e) => patch({ followupText: e.target.value })}
                            disabled={busy}
                            rows={3}
                            placeholder="回答这条追问…"
                            className="mt-2 w-full resize-y rounded-md border border-line bg-bg-3 px-3 py-2 text-xs text-fg-1 focus:border-accent focus:outline-none"
                          />
                          <div className="mt-2 flex gap-2">
                            <Button
                              variant="primary"
                              disabled={busy || !st.followupText.trim()}
                              onClick={() => void submitFollowup(fu.index)}
                            >
                              {st.status === 'submitting' && st.submittingFollowup === fu.index ? (
                                <Spinner className="h-3.5 w-3.5" />
                              ) : (
                                <Check className="h-3.5 w-3.5" />
                              )}
                              提交追问回答
                            </Button>
                            <Button
                              variant="ghost"
                              disabled={busy}
                              onClick={() => void skipNode()}
                              title="关闭本节点追问流并推进（已答内容保留）"
                            >
                              <SkipForward className="h-3.5 w-3.5" /> 跳过剩余追问
                            </Button>
                          </div>
                        </>
                      )}
                    </div>
                  ),
                )}
              </div>
            </div>
          )}
        </>
      )}

      {/* spec 确认区（002 §4.2-5） */}
      {specZoneVisible && (
        <div className="rounded-md border border-success/30 bg-success/10 p-4">
          <div className="flex items-center gap-2 text-sm text-success">
            <FileCheck className="h-4 w-4" />
            {iv.status === 'confirmed' ? 'spec 已确认' : 'spec 已生成'}
          </div>
          <p className="mt-2 text-xs text-fg-3">
            {st.stopSummary ??
              (iv.stopped
                ? '访谈已冻结，未答节点采用推荐答案。'
                : '设计树 7 节点已全覆盖。')}
            {st.specFile ? ` spec 路径：${st.specFile}` : ''}
          </p>
          {iv.status !== 'confirmed' && (
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                variant="primary"
                disabled={st.status === 'confirming'}
                onClick={() => void confirmSpec()}
              >
                {st.status === 'confirming' ? (
                  <Spinner className="h-3.5 w-3.5" />
                ) : (
                  <Check className="h-3.5 w-3.5" />
                )}
                确认共识
              </Button>
              <Button variant="secondary" onClick={onClose}>
                关闭面板
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}