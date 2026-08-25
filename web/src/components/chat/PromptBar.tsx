import { useState, useRef } from 'react'
import { Send, Square } from 'lucide-react'
import { useSessionStore } from '@/stores/session'
import { useWebSocket } from '@/hooks/useWebSocket'
import { cn } from '@/lib/utils'

const COMMANDS = [
  'help', 'tools', 'tool', 'capabilities', 'agents', 'agent', 'plan', 'image',
  'paste', 'audit', 'skills', 'cli-anything', 'raw', 'model', 'models', 'mcp',
  'config', 'status', 'doctor', 'dump', 'sandbox', 'mode', 'search',
  'new', 'sessions', 'resume', 'reset', 'compact',
  'history', 'trace', 'reasoning', 'think', 'verbose', 'usage', 'temp',
  'maxturns', 'limits', 'save', 'clear', 'memory',
  'exit', 'quit', 'stop', 'approve', 'deny',
]

export function PromptBar() {
  const [text, setText] = useState('')
  const [showCommands, setShowCommands] = useState(false)
  const [commandFilter, setCommandFilter] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const { isStreaming } = useSessionStore()
  const { send: wsSend } = useWebSocket()

  const filteredCommands = commandFilter
    ? COMMANDS.filter(c => c.startsWith(commandFilter)).slice(0, 8)
    : COMMANDS.slice(0, 8)

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || isStreaming) return
    if (trimmed.startsWith('/')) {
      const [cmd, ...rest] = trimmed.slice(1).split(' ')
      wsSend({ type: 'command', command: cmd, args: rest.join(' ') })
    } else {
      wsSend({ type: 'chat', text: trimmed })
    }
    setText('')
    setShowCommands(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value
    setText(value)
    if (value.startsWith('/') && !value.includes(' ')) {
      setShowCommands(true)
      setCommandFilter(value.slice(1))
    } else {
      setShowCommands(false)
    }
  }

  return (
    <div className="relative bg-gradient-to-t from-zinc-950 to-zinc-950/80 px-4 pb-4 pt-2">
      {/* Subtle top fade */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-2 bg-gradient-to-t from-transparent to-zinc-950" />

      {showCommands && filteredCommands.length > 0 && (
        <div className="absolute bottom-full left-4 right-4 mb-2 overflow-hidden rounded-xl border border-zinc-700 bg-zinc-900 shadow-2xl shadow-black/50">
          <div className="border-b border-zinc-800 px-3 py-1.5 text-xs text-zinc-500">
            Commands
          </div>
          {filteredCommands.map(cmd => (
            <button
              key={cmd}
              onClick={() => { setText(`/${cmd} `); setShowCommands(false); inputRef.current?.focus() }}
              className="block w-full px-3 py-2 text-left text-sm transition-colors hover:bg-zinc-800/60"
            >
              <span className="font-mono text-brand-cyan">/{cmd}</span>
            </button>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2.5 rounded-2xl border border-zinc-700/80 bg-zinc-900/60 p-2.5 transition-colors focus-within:border-brand-primary/50 focus-within:bg-zinc-900">
        <textarea
          ref={inputRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="Send a message or / for commands..."
          rows={1}
          className="flex-1 resize-none bg-transparent px-1.5 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
        />
        <button
          onClick={isStreaming ? () => wsSend({ type: 'interrupt' }) : handleSend}
          disabled={!isStreaming && !text.trim()}
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-all',
            isStreaming
              ? 'bg-red-600/20 text-red-400 hover:bg-red-600/30'
              : text.trim()
                ? 'bg-brand-primary/20 text-brand-cyan hover:bg-brand-primary/30'
                : 'bg-zinc-800 text-zinc-600',
          )}
        >
          {isStreaming ? <Square className="h-3.5 w-3.5" /> : <Send className="h-4 w-4" />}
        </button>
      </div>
    </div>
  )
}