import { useState } from 'react'
import { ChevronDown, ChevronRight, Wrench } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ToolChipProps {
  name: string
  status: 'running' | 'done'
  result?: string
}

export function ToolChip({ name, status, result }: ToolChipProps) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="my-0.5 rounded-lg border border-zinc-200 dark:border-zinc-800/60 bg-zinc-50 dark:bg-zinc-900/30 text-[13px]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left"
      >
        {expanded ? <ChevronDown className="h-3 w-3 text-zinc-400 dark:text-zinc-600" /> : <ChevronRight className="h-3 w-3 text-zinc-400 dark:text-zinc-600" />}
        <Wrench className="h-3 w-3 text-brand-primary" />
        <span className="font-mono text-[11px] text-zinc-700 dark:text-zinc-300">{name}</span>
        <span className={cn(
          'ml-auto text-[11px]',
          status === 'running' ? 'animate-pulse text-brand-cyan' : 'text-green-500/80',
        )}>
          {status === 'running' ? 'running...' : 'done'}
        </span>
      </button>
      {expanded && result && (
        <pre className="max-h-40 overflow-auto border-t border-zinc-200 dark:border-zinc-800/60 p-2 font-mono text-[11px] text-zinc-600 dark:text-zinc-400">
          {result}
        </pre>
      )}
    </div>
  )
}