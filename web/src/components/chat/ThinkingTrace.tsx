import { useState } from 'react'
import { Brain, ChevronDown, ChevronRight } from 'lucide-react'
import { useSessionStore } from '@/stores/session'

export function ThinkingTrace() {
  const { streamingReasoning } = useSessionStore()
  const [expanded, setExpanded] = useState(false)
  if (!streamingReasoning.length) return null
  return (
    <div className="my-2 rounded-lg border border-zinc-800 bg-zinc-900/30">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-zinc-500"
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Brain className="h-3.5 w-3.5" />
        <span>Thinking ({streamingReasoning.length} tokens)</span>
      </button>
      {expanded && (
        <pre className="max-h-64 overflow-auto border-t border-zinc-800 p-2 font-mono text-xs text-zinc-500">
          {streamingReasoning.join('')}
        </pre>
      )}
    </div>
  )
}