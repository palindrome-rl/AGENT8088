import { useState } from 'react'
import { Terminal, X, Loader2 } from 'lucide-react'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { cn } from '@/lib/utils'

export function RawPanel() {
  const [text, setText] = useState('')
  const { send } = useWebSocket()
  const { rawResult, rawLoading, setRawLoading } = useSessionStore()
  const { setRawPanelOpen } = useUIStore()

  const handleSend = () => {
    if (!text.trim() || rawLoading) return
    setRawLoading(true)
    send({ type: 'command', command: 'raw', args: text })
  }

  return (
    <div className="flex h-full flex-col border-b border-zinc-800 bg-zinc-950">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm text-zinc-300">
          <Terminal className="h-4 w-4 text-brand-primary" />
          <span className="font-medium">Raw Model Call</span>
          <span className="text-xs text-zinc-500">— content, reasoning & tool_calls JSON</span>
        </div>
        <button
          onClick={() => setRawPanelOpen(false)}
          className="flex h-6 w-6 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
          aria-label="Close raw panel"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Input row */}
      <div className="flex gap-2 p-3">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Enter text for a raw model call..."
          className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-primary focus:outline-none"
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
        />
        <button
          onClick={handleSend}
          disabled={rawLoading || !text.trim()}
          className={cn(
            'flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm transition-colors',
            rawLoading || !text.trim()
              ? 'bg-zinc-800 text-zinc-600'
              : 'bg-brand-primary/20 text-brand-cyan hover:bg-brand-primary/30',
          )}
        >
          {rawLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {rawLoading ? 'Calling...' : 'Call'}
        </button>
      </div>

      {/* Result */}
      {rawResult && (
        <div className="flex-1 overflow-auto p-3">
          <pre className="max-h-full overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-300">
            {JSON.stringify(rawResult, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}