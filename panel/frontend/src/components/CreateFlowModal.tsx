/**
 * CreateFlowModal — 新建项目模态框（006 §3.3 基础上 2026-08-23 项目化改造）
 *
 * 两种模式（2026-08-23 P2 项目层级）：
 *   new      —— 新建项目：输入项目名，从零开始
 *   improve  —— 改进项目：选择要改进的已有项目（父项目），在其下建子项目。
 *               入口：Sidebar「新建项目 ▾」下拉 + ManageFlows 行内「改进」按钮
 *               （行内入口会预选该行为父项目）
 *
 * Props：
 *   open        —— 是否显示
 *   onClose     —— 关闭回调（ESC / backdrop / ✕ / 成功后 1s 自动）
 *   onCreated   —— 创建成功回调（父组件可用于 refetch）
 *   initialParent —— 预选的父项目 id（ManageFlows 行内「改进」入口传入）
 *
 * 实现细节：
 *   - 用自定义 backdrop（避免 <dialog>::backdrop 在 Edge --app 模式渲染差异，007 X3）
 *   - ESC + 点击 backdrop + 点击 ✕ 都能关闭
 *   - 复用 v1 Settings 的 api.createFlow 逻辑（parent 透传后端 → gen_project.py --parent）
 *   - 成功后 toast + 1s 自动 onClose；失败保留输入 + 错误 toast
 *   - 需求详情（flow-engine-reqmate 2026-09-05）：描述 textarea + 素材三入口
 *     （选文件 / 选文件夹 webkitdirectory / URL）；提交链路 = createFlow
 *     （描述落 brief）→ uploadMaterials（素材上传失败不回滚，UI 明示）
 *   - V2 语义（iter4）：已添加项列表的删除仅作用于「本会话未提交项」——
 *     已提交/已落盘素材不可删（列表标题注「仅可移除本次未提交项」）
 *   - V3 失败明细（iter4）：创建成功但素材部分失败（failed_items 非空）→
 *     不再只弹一条 toast，改用醒目错误列表块逐项展示（类型+来源+原因），
 *     并提示「项目已保留，可对同一项目重新提交素材（重试）」；
 *     响应三态区分：全部成功 / 部分失败 / URL 待取（urls_pending>0）
 */
import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { X } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { toast } from './Toast'
import { Spinner } from './ui/Spinner'
import type { FlowSummary } from '../types'

type CreateMode = 'new' | 'improve'

/** 素材列表展示用：字节数 → 人类可读大小 */
function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** 素材部分失败明细项（后端 materials 端点 failed_items，iter4 C4） */
interface MaterialFailedItem {
  type: string
  source: string
  reason: string
  ts: string
}

/** failed_items.type → 中文标签（未知类型原样透出） */
const FAILED_TYPE_LABEL: Record<string, string> = {
  url: 'URL',
  file: '文件',
  brief: '需求简报',
}

interface CreateFlowModalProps {
  open: boolean
  onClose: () => void
  onCreated?: (flow: FlowSummary) => void
  /** 行内「改进」入口预选的父项目 id（打开时若提供则自动切 improve 模式） */
  initialParent?: string
}

export function CreateFlowModal({
  open,
  onClose,
  onCreated,
  initialParent,
}: CreateFlowModalProps) {
  const [mode, setMode] = useState<CreateMode>('new')
  const [parent, setParent] = useState('')
  /** 父项目候选：{id,title,hasInstance}（hasInstance=false = 仅目录的早期根项目） */
  const [projects, setProjects] = useState<{ id: string; title: string; hasInstance: boolean }[]>([])
  const [template, setTemplate] = useState('')
  const [creating, setCreating] = useState(false)
  // ── 需求详情（flow-engine-reqmate 2026-09-05，002 §T3）─────────────
  /** 需求描述：随 createFlow 落 exchange/000-requirements-brief.md */
  const [description, setDescription] = useState('')
  /** 已添加素材文件（文件夹选择的文件带 webkitRelativePath 相对路径） */
  const [files, setFiles] = useState<File[]>([])
  /** 已添加 URL 列表（提交时换行连接传后端抓取） */
  const [urls, setUrls] = useState<string[]>([])
  const [urlInput, setUrlInput] = useState('')
  // ── V3 失败明细（iter4）：部分失败时不自动关闭，错误列表块留在模态内 ──
  /** 素材部分失败明细（后端 failed_items）；空 = 无部分失败 */
  const [failedItems, setFailedItems] = useState<MaterialFailedItem[]>([])
  /** 部分失败所属项目 id（错误块提示「项目已保留，可重试」用） */
  const [failedFlow, setFailedFlow] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dirInputRef = useRef<HTMLInputElement>(null)

  // 打开时聚焦 + 复位（initialParent 提供时直接进 improve 模式并预选；
  // 空串 = 改进模式但不预选，由用户在下拉里自选）
  useEffect(() => {
    if (open) {
      setTemplate('')
      setMode(initialParent !== undefined ? 'improve' : 'new')
      setParent(initialParent ?? '')
      // 需求详情复位（每次打开从空白开始）
      setDescription('')
      setFiles([])
      setUrls([])
      setUrlInput('')
      setFailedItems([])
      setFailedFlow('')
      // 拉父项目候选（2026-08-24：/api/projects = 实例 ∪ 根目录，
      // 修复 flow-engine 本体等无实例根项目选不到的缺口）
      api
        .listProjectCandidates()
        .then((res) =>
          setProjects([
            ...res.projects.map((p) => ({ id: p.id, title: p.title, hasInstance: true })),
            ...res.dirProjects.map((p) => ({ id: p.id, title: p.title, hasInstance: false })),
          ]),
        )
        .catch(() => setProjects([]))
      // webkitdirectory 不在 React TS 类型里，挂载后直接补属性
      // （Chromium 面板环境支持；文件夹打包上传，002 §T3）
      dirInputRef.current?.setAttribute('webkitdirectory', 'true')
      // 等一帧再 focus，避免 modal 尚未挂载
      const id = window.setTimeout(() => inputRef.current?.focus(), 50)
      return () => window.clearTimeout(id)
    }
  }, [open, initialParent])

  // ESC 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !creating) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, creating, onClose])

  if (!open) return null

  // ── 需求详情交互（素材文件 / 文件夹 / URL） ─────────────

  /** 选择文件/文件夹回调（文件夹项自带 webkitRelativePath 相对路径） */
  const onPickMaterials = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? [])
    if (picked.length) setFiles((prev) => [...prev, ...picked])
    e.target.value = '' // 允许重复选同名内容
  }

  const addUrl = () => {
    const u = urlInput.trim()
    if (!u) return
    if (!/^https?:\/\//i.test(u)) {
      toast.warn('URL 需以 http:// 或 https:// 开头')
      return
    }
    setUrls((prev) => (prev.includes(u) ? prev : [...prev, u]))
    setUrlInput('')
  }

  const removeFile = (index: number) =>
    setFiles((prev) => prev.filter((_, i) => i !== index))

  const removeUrl = (u: string) => setUrls((prev) => prev.filter((x) => x !== u))

  const handleCreate = async () => {
    const name = template.trim()
    if (!name) {
      toast.warn('请输入项目名')
      return
    }
    if (mode === 'improve' && !parent) {
      toast.warn('请选择要改进的项目')
      return
    }
    const desc = description.trim()
    const hasMaterials = files.length > 0 || urls.length > 0
    setCreating(true)
    try {
      // 第一步：建项目（描述随 createFlow 落 exchange/000-requirements-brief.md）
      const flow = await api.createFlow(
        name,
        mode === 'improve' ? parent : undefined,
        desc || undefined,
      )
      // 第二步：有素材则上传（失败不回滚项目，UI 明示，002 §D1 默认）
      let materialsError = ''
      let partialFailed: MaterialFailedItem[] = []
      if (hasMaterials) {
        const fd = new FormData()
        for (const f of files) {
          // 文件夹打包：相对路径放 filename 字段，后端按路径递归落盘
          const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath
          fd.append('files', f, rel || f.name)
        }
        fd.append('urls', urls.join('\n'))
        // description 已由 createFlow 落 brief，这里不重复传（避免追加重复节）
        fd.append('description', '')
        try {
          const up = await api.uploadMaterials(flow.id, fd)
          partialFailed = up.failed_items ?? []
          // V3 三态之一：URL 待取（无其他失败项）→ 专属提示文案
          if (partialFailed.length === 0 && up.urls_pending > 0) {
            toast.warn(
              `项目已创建，${up.urls_pending} 个 URL 待取（抓取失败已登记 MANIFEST，可稍后重试）`,
            )
          }
        } catch (me) {
          materialsError = me instanceof Error ? me.message : String(me)
        }
      }
      if (partialFailed.length > 0) {
        // V3 部分失败：醒目错误列表块展示明细，不自动关闭
        //（项目已保留，可对同一项目重新提交素材重试）
        setFailedItems(partialFailed)
        setFailedFlow(flow.id)
        toast.ok(`项目 ${flow.id} 已创建，但部分素材未落盘（失败明细见弹窗）`)
        onCreated?.(flow)
        return
      }
      toast.ok(
        mode === 'improve'
          ? `已创建子项目 ${flow.id}（改进 ${parent}）`
          : `已创建项目 ${flow.id}`,
      )
      if (materialsError) toast.err(`素材上传失败（项目已保留）：${materialsError}`)
      onCreated?.(flow)
      // 1s 后自动关闭，让用户感知到「已创建」
      window.setTimeout(onClose, 1000)
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : String(e)
      toast.err(`创建失败：${msg}`)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget && !creating) onClose()
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-flow-modal-title"
    >
      <div className="w-full max-w-md rounded-lg border border-line bg-bg-2 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2
            id="create-flow-modal-title"
            className="text-sm font-semibold text-fg-1"
          >
            新建项目
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={creating}
            className="rounded-md p-1 text-fg-3 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:opacity-40"
            title="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-4">
          {/* V3 素材部分失败明细（iter4）：醒目错误列表块 + 重试提示，
              不再只用一条 toast（项目已保留，可对同一项目重新提交素材） */}
          {failedItems.length > 0 && (
            <div className="mb-3 rounded-md border border-error/40 bg-error/10 p-3">
              <p className="mb-2 text-xs font-semibold text-error">
                ⚠ 素材部分失败明细 — 项目 {failedFlow} 已保留，可对同一项目重新提交素材（重试）
              </p>
              <ul className="max-h-28 space-y-1 overflow-y-auto">
                {failedItems.map((it, i) => (
                  <li
                    key={`${it.type}-${it.source}-${i}`}
                    className="break-all text-xs text-error"
                  >
                    [{FAILED_TYPE_LABEL[it.type] ?? it.type}] {it.source} — {it.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 模式切换：新建项目 / 改进项目 */}
          <div className="mb-3 flex items-center gap-1 rounded-md border border-line bg-bg-3 p-0.5">
            {(
              [
                { key: 'new', label: '新建项目' },
                { key: 'improve', label: '改进现有项目' },
              ] as const
            ).map((m) => (
              <button
                key={m.key}
                type="button"
                onClick={() => setMode(m.key)}
                className={`flex-1 rounded px-2.5 py-1 text-xs transition-colors ${
                  mode === m.key
                    ? 'bg-accent text-white'
                    : 'text-fg-2 hover:bg-bg-2 hover:text-fg-1'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>

          {mode === 'improve' && (
            <div className="mb-3">
              <label className="mb-1 block text-xs text-fg-3">
                选择要改进的项目（新项目将作为其子项目）
              </label>
              <select
                value={parent}
                onChange={(e) => setParent(e.target.value)}
                disabled={creating}
                className="w-full rounded-md border border-line bg-bg-3 px-3 py-2 text-sm text-fg-1 focus:border-accent focus:outline-none disabled:opacity-50"
              >
                <option value="">— 选择父项目 —</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.title}（{p.id}）
                    {!p.hasInstance && ' ·仅目录'}
                  </option>
                ))}
              </select>
            </div>
          )}

          <p className="mb-2 text-xs text-fg-3">
            {mode === 'improve'
              ? '输入子项目名 slug（如 flow-engine-frontend-v3），后端调用 gen_project.py --parent 初始化。'
              : '输入英文项目名 slug（与 flows/*.yaml 对应），后端调用 gen_project.py 初始化。'}
          </p>
          <input
            ref={inputRef}
            type="text"
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !creating) void handleCreate()
            }}
            placeholder="例：my-new-project"
            disabled={creating}
            className="w-full rounded-md border border-line bg-bg-3 px-3 py-2 text-sm text-fg-1 placeholder:text-fg-3 focus:border-accent focus:outline-none disabled:opacity-50"
          />

          {/* 需求详情（flow-engine-reqmate，002 §T3）：描述 + 素材 + URL */}
          <div className="mt-3 border-t border-line pt-3">
            <label className="mb-1 block text-xs font-medium text-fg-2">
              需求详情（可选）
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={creating}
              rows={3}
              placeholder="描述需求、背景、验收目标…（落盘为 exchange/000-requirements-brief.md，供立项访谈引用）"
              className="w-full resize-y rounded-md border border-line bg-bg-3 px-3 py-2 text-sm text-fg-1 placeholder:text-fg-3 focus:border-accent focus:outline-none disabled:opacity-50"
            />

            {/* 三种素材添加方式：选择文件 / 选择文件夹 / 添加 URL */}
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={creating}
                className="rounded-md border border-line px-2.5 py-1 text-xs text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:opacity-40"
              >
                ＋ 选择文件
              </button>
              <button
                type="button"
                onClick={() => dirInputRef.current?.click()}
                disabled={creating}
                className="rounded-md border border-line px-2.5 py-1 text-xs text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:opacity-40"
              >
                ＋ 选择文件夹
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={onPickMaterials}
                disabled={creating}
              />
              <input
                ref={dirInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={onPickMaterials}
                disabled={creating}
              />
              <div className="flex min-w-[180px] flex-1 gap-1">
                <input
                  type="text"
                  value={urlInput}
                  onChange={(e) => setUrlInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !creating) {
                      e.preventDefault()
                      addUrl()
                    }
                  }}
                  placeholder="https://…（URL 正文后端抓取）"
                  disabled={creating}
                  className="min-w-0 flex-1 rounded-md border border-line bg-bg-3 px-2.5 py-1 text-xs text-fg-1 placeholder:text-fg-3 focus:border-accent focus:outline-none disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={addUrl}
                  disabled={creating || !urlInput.trim()}
                  className="rounded-md border border-line px-2.5 py-1 text-xs text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:opacity-40"
                >
                  添加
                </button>
              </div>
            </div>

            {/* 已添加项列表（可删除）。V2 语义（iter4）：删除仅作用于
                「本会话未提交项」——已提交/已落盘素材不可删 */}
            {(files.length > 0 || urls.length > 0) && (
              <div className="mt-2">
                <p className="mb-1 text-[11px] text-fg-3">
                  已添加（仅可移除本次未提交项；已提交/已落盘素材不可删）
                </p>
              <ul className="max-h-28 space-y-1 overflow-y-auto rounded-md border border-line bg-bg-3 p-2">
                {files.map((f, i) => {
                  const rel = (f as File & { webkitRelativePath?: string })
                    .webkitRelativePath
                  return (
                    <li
                      key={`file-${i}-${rel || f.name}`}
                      className="flex items-center justify-between gap-2 text-xs text-fg-2"
                    >
                      <span className="truncate">
                        📄 {rel || f.name}（{formatBytes(f.size)}）
                      </span>
                      <button
                        type="button"
                        onClick={() => removeFile(i)}
                        disabled={creating}
                        className="shrink-0 rounded p-0.5 text-fg-3 transition-colors hover:bg-bg-2 hover:text-fg-1 disabled:opacity-40"
                        title="移除"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </li>
                  )
                })}
                {urls.map((u) => (
                  <li
                    key={`url-${u}`}
                    className="flex items-center justify-between gap-2 text-xs text-fg-2"
                  >
                    <span className="truncate">🔗 {u}</span>
                    <button
                      type="button"
                      onClick={() => removeUrl(u)}
                      disabled={creating}
                      className="shrink-0 rounded p-0.5 text-fg-3 transition-colors hover:bg-bg-2 hover:text-fg-1 disabled:opacity-40"
                      title="移除"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </li>
                ))}
              </ul>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={creating}
            className="rounded-md border border-line px-4 py-1.5 text-xs text-fg-1 transition-colors hover:bg-bg-3 disabled:opacity-40"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void handleCreate()}
            disabled={creating || !template.trim() || (mode === 'improve' && !parent)}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {creating && <Spinner className="h-3.5 w-3.5" />}
            {creating ? '创建中…' : mode === 'improve' ? '创建子项目' : '创建'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default CreateFlowModal
