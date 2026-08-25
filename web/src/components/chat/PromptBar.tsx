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
    <div className="relative border-t border-zinc-800 bg-zinc-950 p-3">
      {showCommands && filteredCommands.length > 0 && (
        <div className="absolute bottom-full left-3 right-3 mb-1 rounded-lg border border-zinc-700 bg-zinc-900 p-1 shadow-xl">
          {filteredCommands.map(cmd => (
            <button
              key={cmd}
              onClick={() => { setText(`/${cmd} `); setShowCommands(false); inputRef.current?.focus() }}
              className="block w-full rounded px-3 py-1.5 text-left text-sm text-zinc-300 hover:bg-zinc-800"
            >
              <span className="font-mono text-brand-cyan">/{cmd}</span>
            </button>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2">
        <textarea
          ref={inputRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="Send a message or /command..."
          rows={1}
          className="flex-1 resize-none rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-primary focus:outline-none"
        />
        <button
          onClick={isStreaming ? () => wsSend({ type: 'interrupt' }) : handleSend}
          className={cn(
            'flex h-9 w-9 items-center justify-center rounded-lg transition-colors',
            isStreaming
              ? 'bg-red-600/20 text-red-400 hover:bg-red-600/30'
              : 'bg-brand-primary/20 text-brand-cyan hover:bg-brand-primary/30',
          )}
        >
          {isStreaming ? <Square className="h-4 w-4" /> : <Send className="h-4 w-4" />}
        </button>
      </div>
    </div>
  )
}