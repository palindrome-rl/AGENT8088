import { useState } from 'react'
import { Brain, ChevronDown, ChevronRight } from 'lucide-react'
import { useSessionStore } from '@/stores/session'

export function ThinkingTrace() {
  const { streamingReasoning } = useSessionStore()
  const [expanded, setExpanded] = useState(false)
  if (!streamingReasoning.length) return null
  return (
    <div className="my-1 rounded-lg border border-zinc-800/60 bg-zinc-900/20">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[11px] text-zinc-500"
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Brain className="h-3 w-3" />
        <span>Thinking ({streamingReasoning.length} tokens)</span>
      </button>
      {expanded && (
        <pre className="max-h-56 overflow-auto border-t border-zinc-800/60 p-2 font-mono text-[11px] text-zinc-500">
          {streamingReasoning.join('')}
        </pre>
      )}
    </div>
  )
}