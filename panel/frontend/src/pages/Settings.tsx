/**
 * Settings — 设置页（006 §3.8，瘦身）
 *
 * 改动 vs v1：
 *   - 删除「创建新流程」整个 section（迁到 CreateFlowModal，由 Sidebar / Dashboard 入口触发）
 *   - 保留「引擎连接」+「关于」两块
 *   - 「关于」明确指向 v2 设计引用
 */
import { useEffect, useState } from 'react'
import { Spinner } from '../components/ui/Spinner'
import { StatusBadge } from '../components/StatusBadge'
import { api, ApiError } from '../api/client'
import { DEFAULT_API_BASE } from '../api/config'

type EngineState = 'ok' | 'err' | 'checking'

async function checkEngine(): Promise<EngineState> {
  try {
    await api.listFlows() // 顺便 warm-up 一次列表
    return 'ok'
  } catch (e) {
    if (e instanceof ApiError && e.status !== 0) return 'ok'
    return 'err'
  }
}

export default function Settings() {
  const [engineState, setEngineState] = useState<EngineState>('checking')
  const [engineMsg, setEngineMsg] = useState('检测中…')

  const check = async () => {
    setEngineState('checking')
    setEngineMsg('检测中…')
    const s = await checkEngine()
    setEngineState(s)
    if (s === 'ok') {
      setEngineMsg(`引擎在线 · ${DEFAULT_API_BASE}/api`)
    } else {
      setEngineMsg(`引擎离线 · 期望 ${DEFAULT_API_BASE}`)
    }
  }

  useEffect(() => {
    void check()
  }, [])

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-6">
      <div>
        <h1 className="text-lg font-semibold text-fg-1">设置</h1>
        <p className="mt-0.5 text-xs text-fg-3">引擎连接与版本信息</p>
      </div>

      <div className="rounded-[10px] border border-line bg-bg-2 p-4 shadow-[0_1px_2px_hsl(222,30%,4%)]">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium text-fg-1">引擎连接</h2>
            <p className="mt-1 text-xs text-fg-2">{engineMsg}</p>
          </div>
          <div className="flex items-center gap-2">
            {engineState === 'checking' ? (
              <Spinner className="h-4 w-4" />
            ) : (
              <StatusBadge status={engineState === 'ok' ? 'completed' : 'failed'} />
            )}
            <button
              type="button"
              onClick={() => void check()}
              className="rounded-md border border-line bg-bg-3 px-3 py-1.5 text-xs text-fg-1 hover:bg-bg-2"
            >
              ↻ 检测
            </button>
          </div>
        </div>
      </div>

      <div className="rounded-[10px] border border-line bg-bg-2 p-4 shadow-[0_1px_2px_hsl(222,30%,4%)]">
        <h2 className="mb-3 text-sm font-medium text-fg-1">关于</h2>
        <dl className="space-y-1.5 text-xs text-fg-2">
          <div className="flex justify-between">
            <dt className="text-fg-3">版本</dt>
            <dd className="font-mono">2.0.0 (侧边栏 + grill-me)</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-fg-3">前端栈</dt>
            <dd className="font-mono">React 19 + Vite 6 + Tailwind v4</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-fg-3">后端地址</dt>
            <dd className="font-mono">{DEFAULT_API_BASE}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-fg-3">设计</dt>
            <dd>006-athena-frontend-upgrade-proposal.md（006 § 拍板）</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-fg-3">实施</dt>
            <dd>008-athena-frontend-upgrade-report.md（v2 落地报告）</dd>
          </div>
        </dl>
      </div>
    </div>
  )
}