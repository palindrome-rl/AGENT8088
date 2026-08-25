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
    <div className="relative bg-zinc-950 dark:bg-zinc-950 light:bg-zinc-50 pb-4 pt-2">
      {showCommands && filteredCommands.length > 0 && (
        <div className="absolute bottom-full left-1/2 mb-2 w-full max-w-2xl -translate-x-1/2 overflow-hidden rounded-xl border border-zinc-800 dark:border-zinc-800 light:border-zinc-200 bg-zinc-900 dark:bg-zinc-900 light:bg-white shadow-2xl shadow-black/40">
          <div className="border-b border-zinc-800/60 dark:border-zinc-800/60 light:border-zinc-200 px-3 py-1 text-[11px] text-zinc-500 dark:text-zinc-500 light:text-zinc-400">
            Commands
          </div>
          {filteredCommands.map(cmd => (
            <button
              key={cmd}
              onClick={() => { setText(`/${cmd} `); setShowCommands(false); inputRef.current?.focus() }}
              className="block w-full px-3 py-1.5 text-left text-[13px] transition-colors hover:bg-zinc-800/50 dark:hover:bg-zinc-800/50 light:hover:bg-zinc-100"
            >
              <span className="font-mono text-brand-cyan">/{cmd}</span>
            </button>
          ))}
        </div>
      )}
      <div className="mx-auto w-full max-w-2xl">
        <div className="flex items-center gap-2 rounded-xl border border-zinc-800 dark:border-zinc-800 light:border-zinc-300 bg-zinc-900/50 dark:bg-zinc-900/50 light:bg-white px-3 py-2 transition-colors focus-within:border-brand-primary/40">
          <textarea
            ref={inputRef}
            value={text}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder="Send a message..."
            rows={1}
            className="flex-1 resize-none bg-transparent text-[14px] text-zinc-100 dark:text-zinc-100 light:text-zinc-900 placeholder:text-zinc-600 dark:placeholder:text-zinc-600 light:placeholder:text-zinc-400 focus:outline-none"
          />
          <button
            onClick={isStreaming ? () => wsSend({ type: 'interrupt' }) : handleSend}
            disabled={!isStreaming && !text.trim()}
            className={cn(
              'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-all',
              isStreaming
                ? 'bg-red-600/15 text-red-400 hover:bg-red-600/25'
                : text.trim()
                  ? 'bg-brand-primary/15 text-brand-cyan hover:bg-brand-primary/25'
                  : 'text-zinc-600 dark:text-zinc-600 light:text-zinc-400',
            )}
          >
            {isStreaming ? <Square className="h-3 w-3" /> : <Send className="h-4 w-4" />}
          </button>
        </div>
      </div>
    </div>
  )
}