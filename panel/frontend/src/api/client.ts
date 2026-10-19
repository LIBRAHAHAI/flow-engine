/**
 * 原生 fetch 封装 —— v2 共 9 端点
 *
 * 现有 5 端点（v1）：
 *   listFlows / getFlow / decide / createFlow / getHistory
 *
 * v2 新增 4 端点（006 §5.3 / §4.1）：
 *   listLogs            — GET    /flows/{id}/logs
 *   deleteFlow          — DELETE /flows/{id}
 *   getGrillMeContext   — GET    /flows/{id}/grill-context
 *   submitGrillMeTurn   — POST   /flows/{id}/grill-turn
 *
 * 包含 HTTP 状态码契约处理（006 §6.3）+ 新增端点的 400/404 友好提示。
 */
import type {
  FlowSummary,
  FlowDetail,
  DecideResult,
  HistoryEntry,
  LogEntry,
  FlowTemplateMeta,
  KanbanTasksResult,
  NudgeResult,
  ProjectsResult,
  GrillMeContext,
  GrillMeSubmitRequest,
  GrillMeSubmitResult,
  GrillMeInterviewSubmitRequest,
  GrillMeInterviewSubmitResult,
  GrillMeStopResult,
  GrillMeConfirmResult,
} from '../types'
import { getApiBase } from './config'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const base = getApiBase()
  let res: Response
  try {
    res = await fetch(`${base}/api${path}`, {
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      ...init,
    })
  } catch (e) {
    throw new ApiError(0, `网络不可达（${base}）：${e instanceof Error ? e.message : String(e)}`)
  }

  if (res.status === 404) {
    throw new ApiError(404, '资源不存在（404）')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body && typeof body.detail === 'string') detail = body.detail
    } catch {
      /* 非 JSON 响应体 */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`)
  }
  return (await res.json()) as T
}

export const api = {
  // ── 现有 5 端点（v1 不变） ─────────────────────────────

  listFlows: () => request<FlowSummary[]>('/flows'),

  getFlow: (id: string) => request<FlowDetail>(`/flows/${encodeURIComponent(id)}`),

  decide: (id: string, decision: string) =>
    request<DecideResult>(`/flows/${encodeURIComponent(id)}/decide`, {
      method: 'POST',
      body: JSON.stringify({ decision }),
    }),

  createFlow: (template: string, parent?: string, description?: string) =>
    request<FlowSummary>('/flows', {
      method: 'POST',
      body: JSON.stringify({
        template,
        ...(parent ? { parent } : {}),
        ...(description ? { description } : {}),
      }),
    }),

  /** 上传需求素材（flow-engine-reqmate，002 §T1/§T3）。
      multipart：files（文件夹打包时相对路径放 filename 字段）+ urls + description。
      注意：multipart 不能手动设 Content-Type（浏览器自动填 boundary），
      故不复用 request()（其固定 JSON 头）。素材上传失败不回滚项目（002 §D1）。
      iter4 C4/C7：failed_items = 部分失败明细（URL 抓取失败/文件超限/
      brief 落盘失败），空列表 = 全部成功。 */
  uploadMaterials: async (
    flowId: string,
    formData: FormData,
  ): Promise<{
    status: string
    flow: string
    saved: number
    skipped_over_limit: number
    urls_pending: number
    failed_items: { type: string; source: string; reason: string; ts: string }[]
    manifest: string
  }> => {
    const base = getApiBase()
    let res: Response
    try {
      res = await fetch(
        `${base}/api/flows/${encodeURIComponent(flowId)}/materials`,
        { method: 'POST', body: formData },
      )
    } catch (e) {
      throw new ApiError(
        0,
        `网络不可达（${base}）：${e instanceof Error ? e.message : String(e)}`,
      )
    }
    if (!res.ok) {
      let detail = res.statusText
      try {
        const body = await res.json()
        if (body && typeof body.detail === 'string') detail = body.detail
      } catch {
        /* 非 JSON 响应体 */
      }
      throw new ApiError(res.status, detail || `HTTP ${res.status}`)
    }
    return await res.json()
  },

  // ── 流程模板库（2026-08-23 项目化改造 P3）────────────────

  /** 可用执行流程模板列表（flows/template-*.yaml，meta 驱动） */
  listFlowTemplates: () => request<FlowTemplateMeta[]>('/flow-templates'),

  /** 给项目绑定执行流程模板（立项后选择执行流程） */
  bindTemplate: (id: string, templateId: string) =>
    request<{ status: string; flow: string; template: string }>(
      `/flows/${encodeURIComponent(id)}/template`,
      { method: 'POST', body: JSON.stringify({ template_id: templateId }) },
    ),

  /** 查询项目当前绑定的执行流程模板（未绑定为 null） */
  getCurrentTemplate: (id: string) =>
    request<{ flow: string; template: string | null }>(
      `/flow-templates/current/${encodeURIComponent(id)}`,
    ),

  getHistory: (id: string) =>
    request<HistoryEntry[]>(`/flows/${encodeURIComponent(id)}/history`),

  // ── v2 新增 4 端点（006 §5.3） ─────────────────────────

  /** 获取流程日志（倒序：最新在前） */
  listLogs: (id: string) =>
    request<LogEntry[]>(`/flows/${encodeURIComponent(id)}/logs`),

  /** 删除流程（同时清理 instance JSON + 关联文件） */
  deleteFlow: (id: string) =>
    request<{ status: string; flow: string }>(
      `/flows/${encodeURIComponent(id)}`,
      { method: 'DELETE' },
    ),

  /** 获取 grill-me 上下文（首轮 + 后续轮次在此返回） */
  getGrillMeContext: (id: string) =>
    request<GrillMeContext>(`/flows/${encodeURIComponent(id)}/grill-context`),

  /** 提交 grill-me 单轮（confirm / edit / skip） */
  submitGrillMeTurn: (id: string, body: GrillMeSubmitRequest) =>
    request<GrillMeSubmitResult>(
      `/flows/${encodeURIComponent(id)}/grill-turn`,
      {
        method: 'POST',
        body: JSON.stringify(body),
      },
    ),

  // ── mechanism-fix-engine-verify-wait F2 新增 2 端点（002 §F2.2）──

  /** 恢复 paused 流程实例（200 / 404 / 409） */
  resumeFlow: (id: string) =>
    request<{ status: string; flow: string; action: string }>(
      `/flows/${encodeURIComponent(id)}/resume`,
      { method: 'POST' },
    ),

  /** 暂停 running 流程实例（200 / 404 / 409） */
  pauseFlow: (id: string) =>
    request<{ status: string; flow: string; action: string }>(
      `/flows/${encodeURIComponent(id)}/pause`,
      { method: 'POST' },
    ),

  // ── grillme-deep 新增（002 §1.3/§1.4/§1.5，discuss 设计树访谈）──

  /** discuss 分支提交（answer_node / answer_followup / skip_node）。
      LLM 追问生成本请求内完成（≤25s），响应带最新 interview 对象。 */
  submitInterviewTurn: (id: string, body: GrillMeInterviewSubmitRequest) =>
    request<GrillMeInterviewSubmitResult>(
      `/flows/${encodeURIComponent(id)}/grill-turn`,
      {
        method: 'POST',
        body: JSON.stringify(body),
      },
    ),

  /** 停止追问（G3）：冻结问题流 + 生成 spec（未答=采用推荐，幂等） */
  stopGrillMe: (id: string, sessionId: string) =>
    request<GrillMeStopResult>(
      `/flows/${encodeURIComponent(id)}/grill-stop`,
      {
        method: 'POST',
        body: JSON.stringify({ sessionId }),
      },
    ),

  /** 确认共识（G5）：spec draft → confirmed → 引擎 discuss 步推进 */
  confirmGrillMe: (id: string, sessionId: string) =>
    request<GrillMeConfirmResult>(
      `/flows/${encodeURIComponent(id)}/grill-confirm`,
      {
        method: 'POST',
        body: JSON.stringify({ sessionId }),
      },
    ),

  // ── Kanban 可视化 + 催动 + 父项目数据源（2026-08-24 六项优化）──

  /** Kanban 任务列表（含执行者 + 后端推导状态） */
  listKanbanTasks: () => request<KanbanTasksResult>('/kanban/tasks'),

  /** 人工催动：往执行者待办桥队列 append 提醒（不代投拍板；10 分钟冷却 409） */
  nudgeKanbanTask: (taskId: string, message?: string) =>
    request<NudgeResult>(
      `/kanban/tasks/${encodeURIComponent(taskId)}/nudge`,
      { method: 'POST', body: JSON.stringify(message ? { message } : {}) },
    ),

  /** 父项目候选 = 引擎实例 ∪ Project 根目录（改进项目下拉数据源，替代 listFlows） */
  listProjectCandidates: () => request<ProjectsResult>('/projects'),
}