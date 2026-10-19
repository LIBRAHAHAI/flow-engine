// ContextPreview —— 拍板上下文文件摘要卡片（006 §4.1 #10）
import { FileText } from 'lucide-react'
import type { ContextFile } from '../types'

export function ContextPreview({ files }: { files: ContextFile[] }) {
  if (!files || files.length === 0) return null
  return (
    <div className="space-y-2">
      {files.map((f) => (
        <div key={f.path} className="rounded-md border border-line bg-bg-3 p-3">
          <div className="flex items-center gap-2 text-xs">
            <FileText className="h-3.5 w-3.5 shrink-0 text-accent" />
            <span className="font-medium text-fg-1">{f.title}</span>
          </div>
          <p className="mt-1.5 line-clamp-4 text-xs leading-relaxed text-fg-2">
            {f.excerpt}
          </p>
          <p className="mt-1.5 truncate font-mono-id text-[10px] text-fg-3">{f.path}</p>
        </div>
      ))}
    </div>
  )
}
