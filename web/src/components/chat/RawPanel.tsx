import { useState } from 'react'
import { Terminal } from 'lucide-react'
import { useWebSocket } from '@/hooks/useWebSocket'
import { cn } from '@/lib/utils'

export function RawPanel() {
  const [text, setText] = useState('')
  const [result, setResult] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const { send } = useWebSocket()

  const handleSend = () => {
    if (!text.trim()) return
    setLoading(true)
    setResult(null)
    // Send as /raw command
    send({ type: 'command', command: 'raw', args: text })
    // Listen for the command result via a one-time WS message handler
    // The WebSocket hook already handles command_result events
    // For the raw panel we use a simple timeout fallback
    setTimeout(() => setLoading(false), 5000)
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2 text-sm text-zinc-400">
        <Terminal className="h-4 w-4 text-brand-primary" />
        <span>Raw Model Call — single call showing content, reasoning, and tool_calls JSON</span>
      </div>
      <div className="flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Enter text for a raw model call..."
          className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-primary focus:outline-none"
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
        />
        <button
          onClick={handleSend}
          disabled={loading || !text.trim()}
          className={cn(
            'rounded-lg px-4 py-2 text-sm transition-colors',
            loading || !text.trim()
              ? 'bg-zinc-800 text-zinc-600'
              : 'bg-brand-primary/20 text-brand-cyan hover:bg-brand-primary/30',
          )}
        >
          {loading ? 'Calling...' : 'Call'}
        </button>
      </div>
      {result && (
        <pre className="max-h-96 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-300">
          {result}
        </pre>
      )}
    </div>
  )
}