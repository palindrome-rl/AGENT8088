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
    <div className="my-1 rounded-lg border border-zinc-800 bg-zinc-900/50 text-sm">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left"
      >
        {expanded ? <ChevronDown className="h-3 w-3 text-zinc-500" /> : <ChevronRight className="h-3 w-3 text-zinc-500" />}
        <Wrench className="h-3.5 w-3.5 text-brand-primary" />
        <span className="font-mono text-xs text-zinc-300">{name}</span>
        <span className={cn(
          'ml-auto text-xs',
          status === 'running' ? 'animate-pulse text-brand-cyan' : 'text-green-500',
        )}>
          {status === 'running' ? 'running...' : 'done'}
        </span>
      </button>
      {expanded && result && (
        <pre className="max-h-48 overflow-auto border-t border-zinc-800 p-2 font-mono text-xs text-zinc-400">
          {result}
        </pre>
      )}
    </div>
  )
}