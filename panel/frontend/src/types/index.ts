/**
 * Flow Engine — TypeScript 类型契约
 * 数据契约对齐 backend/api.py 的响应字段。
 * 字段命名保持 snake_case ↔ camelCase 双兼容：
 *   - 服务端响应字段原样保留（如 currentStepType），前端直接消费
 *   - 历史条目里的 action / step / detail 等也是服务端字段名
 *
 * v2 增量（006 §4.1）：
 *   - LogEntry：日志条目（GET /flows/{id}/logs）
 *   - GrillMeContext / GrillMeTurn / GrillMeRecommended：grill-me 多轮交互数据
 *   - GrillMeAction / GrillMeSubmitRequest / GrillMeSubmitResult：提交请求/响应
 */

// ── 流程状态 ─────────────────────────────────────────────

export type FlowStatus = 'running' | 'completed' | 'failed' | 'idle' | 'paused'

// 2026-08-22 返工（P1b）：新增 discuss（立项访谈）步骤类型，
// 与引擎 T_DISCUSS / template-new-project.yaml 的 discuss 步对齐。
export type StepType = 'decision' | 'dispatch' | 'wait' | 'verify' | 'done' | 'discuss' | ''

// ── 历史条目 ─────────────────────────────────────────────

export interface HistoryEntry {
  step: string
  action: string
  iteration?: number
  detail?: string
  log?: string
  decision?: string
  passed?: boolean
  timestamp?: string
}

// ── 流程摘要（GET /flows）────────────────────────────────

export interface FlowSummary {
  id: string
  title: string
  status: FlowStatus
  /** 父项目 id（子项目/改进项目才有；2026-08-23 项目层级） */
  parent?: string | null
  /** 执行流程模板名（立项后绑定；2026-08-23 流程模板库） */
  template?: string | null
  currentStep: string
  currentStepType: StepType
  iteration: number
  totalSteps: number
  currentStepIndex: number
  recentHistory: HistoryEntry[]
  hasPendingDecision: boolean
  updatedAt: string
}

// ── 流程详情（GET /flows/{id}）───────────────────────────

export interface StepDef {
  id: string
  type: StepType
  description: string
  question?: string
  options?: { key: string; label: string }[]
  contextFiles?: string[]
}

export interface ContextFile {
  path: string
  title: string
  excerpt: string
}

export interface FlowDetail extends FlowSummary {
  steps: StepDef[]
  contextFiles: ContextFile[]
  history: HistoryEntry[]
}

// ── 拍板请求/响应 ────────────────────────────────────────

export interface DecideRequest {
  decision: string
}

export interface DecideResult {
  status: string
  decision: string
  flow: string
}

// ── 创建流程 ─────────────────────────────────────────────

export interface CreateFlowRequest {
  template: string
  project_dir?: string
  /** 父项目 id（提供时 = 改进现有项目的子项目；2026-08-23） */
  parent?: string
}

// ── 执行流程模板（2026-08-23 流程模板库）────────────────

export interface FlowTemplateMeta {
  id: string
  title: string
  description: string
  category: string
  source: string
  steps: number
  decisionPoints: number
}

// ── 拍板选项（决策点的 A/B/C 候选）─────────────────────

export interface DecisionOption {
  key: string
  label: string
}

// ── 日志条目（v2 新增，GET /flows/{id}/logs）─────────────

export type LogLevel = 'info' | 'warn' | 'error' | 'debug'

export interface LogEntry {
  /** ISO 时间戳 */
  timestamp: string
  /** 日志级别 */
  level: LogLevel
  /** 日志来源（如 step 名 / 模块名） */
  source?: string
  /** 日志正文 */
  message: string
}

// ── grill-me 多轮交互（v2 新增，006 §4.1.1 / §4.1.2）────

/** 单轮推荐答案 + 用户最终选择 */
export interface GrillMeTurn {
  index: number
  question: string
  recommendedAnswer: string
  contextRefs: string[]
}

/** grill-me 会话上下文 */
export interface GrillMeContext {
  active: boolean
  sessionId: string
  turns: GrillMeTurn[]
  /** grillme-deep（002 §1.2）：discuss 分支为 'interview'，decision 分支无此字段 */
  mode?: GrillMeMode
  /** grillme-deep：mode=interview 时的访谈对象 */
  interview?: GrillMeInterviewContext
}

/** 用户对单轮的动作 */
export type GrillMeAction = 'confirm' | 'edit' | 'skip'

/** 提交单轮的请求体 */
export interface GrillMeSubmitRequest {
  sessionId: string
  turnIndex: number
  action: GrillMeAction
  /** 仅 action=edit 时必填 */
  userText?: string
}

/** 提交单轮的响应 */
export interface GrillMeSubmitResult {
  status: 'ok' | 'error'
  /** 下一轮；null + done=true 表示完成 */
  nextTurn: GrillMeTurn | null
  done: boolean
  summary?: string
}

// ── grillme-deep：discuss 设计树访谈（002 §4.4）────────

/** 单条 LLM 追问（含已答标记） */
export interface GrillMeFollowup {
  index: number
  question: string
  answered: boolean
  answer?: string | null
}

/** discuss 访谈上下文（GET /grill-context mode=interview 分支的 interview 对象） */
export interface GrillMeInterviewContext {
  nodeKey: string | null
  nodeQuestion: string | null
  recommendedAnswer: string | null
  followups: GrillMeFollowup[]
  nodeAnswered: boolean | null
  progress: { answered: number; total: number }
  stopped: boolean
  llmDegraded: boolean
  degradedReason?: string
  status: string // active | spec_draft | confirmed
}

/** grill-me 上下文扩展（002 §1.2：mode/interview 为可选字段，旧响应兼容） */
export type GrillMeMode = 'decision' | 'interview'

// 注：GrillMeContext 上方定义处补充可选字段 mode / interview（向后兼容）

/** discuss 分支提交请求（002 §1.3） */
export interface GrillMeInterviewSubmitRequest {
  sessionId: string
  mode: 'interview'
  action: 'answer_node' | 'answer_followup' | 'skip_node'
  userText?: string
  nodeKey: string
  followupIndex?: number
}

/** discuss 分支提交响应（002 §1.3） */
export interface GrillMeInterviewSubmitResult {
  status: 'ok' | 'error'
  interview: GrillMeInterviewContext
  treeComplete: boolean
  llmDegraded: boolean
  degradedReason?: string
}

/** grill-stop 响应（002 §1.4） */
export interface GrillMeStopResult {
  status: 'ok' | 'error'
  specFile: string
  summary: string
  alreadyStopped?: boolean
}

/** grill-confirm 响应（002 §1.5） */
export interface GrillMeConfirmResult {
  status: 'ok' | 'error'
  confirmed: boolean
}

// ── Kanban 任务可视化 + 催动 + 父项目数据源（2026-08-24 六项优化 P0/P1）──

/** 面板侧任务展示状态（后端 derivedState 推导好，前端直接渲染不自行判断） */
export type KanbanDerivedState =
  | 'ready' // 待认领
  | 'running' // 执行中（心跳新鲜）
  | 'stuck' // 疑似卡住（running 但心跳超 15min）
  | 'blocked' // 连续失败 ≥2 次
  | 'unassigned-agent' // 派给了不存在的执行者，没人会认领
  | 'done' // 完成
  | 'other'
  | (string & {}) // 引擎未知状态原样透出

export interface KanbanTask {
  id: string
  title: string
  /** 从标题解析出的项目名（[引擎:<flow>]），非引擎卡为 null */
  flow: string | null
  /** 步骤标题（去前缀后剩余部分，含 iter 信息原文） */
  stepTitle: string
  iteration: number | null
  assignee: string
  status: string
  derivedState: KanbanDerivedState
  /** ready 超 30 分钟无人认领 → 前端亮「久无人领」提示 */
  readyStale: boolean
  failureCount?: number | null
  lastFailureError?: string | null
  createdAt?: number | null
  startedAt?: number | null
  completedAt?: number | null
  lastHeartbeatAt?: number | null
  idempotencyKey?: string | null
}

export interface KanbanTasksResult {
  tasks: KanbanTask[]
  count: number
}

/** 催动响应（target=实际入队对象；assignee 无效时兜底 hermes 并在 effectiveTarget 说明） */
export interface NudgeResult {
  status: string
  target: string
  queueFile: string
  effectiveTarget: string
}

/** 父项目候选（GET /projects）：hasInstance=true 来自实例库，false=仅有目录的早期根项目 */
export interface ProjectCandidate {
  id: string
  title: string
  parent?: string | null
  template?: string | null
  status?: string | null
  hasInstance: boolean
  isDir: boolean
}

export interface ProjectsResult {
  projects: ProjectCandidate[]
  dirProjects: ProjectCandidate[]
}