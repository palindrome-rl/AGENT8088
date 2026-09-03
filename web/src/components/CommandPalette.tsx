import { useState, useRef, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Search, MessageSquare, Wrench, BookOpen, Bot, Network, Brain,
  FolderClock, FolderOpen, Settings, Stethoscope,
  Terminal, ArrowRight, CornerDownLeft,
} from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { useWebSocket } from '@/hooks/useWebSocket'
import { cn } from '@/lib/utils'

/* ─────────────────────────────────────────────────────────
 * COMMAND PALETTE (⌘K)
 * Searchable navigation + slash command execution.
 * Two groups: Navigate (pages) and Commands (slash commands).
 * Keyboard: ArrowUp/Down to move, Enter to select, Escape to close.
 * ───────────────────────────────────────────────────────── */

interface NavItem {
  label: string
  path: string
  icon: React.ComponentType<{ className?: string }>
  keywords: string
}

interface CmdItem {
  label: string
  command: string
  description: string
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Chat', path: '/', icon: MessageSquare, keywords: 'chat message conversation' },
  { label: 'Artifacts', path: '/artifacts', icon: FolderOpen, keywords: 'artifacts files browse outputs' },
  { label: 'Tools', path: '/tools', icon: Wrench, keywords: 'tools functions invoke' },
  { label: 'Skills', path: '/skills', icon: BookOpen, keywords: 'skills packages knowledge' },
  { label: 'Sub-Agents', path: '/agents', icon: Bot, keywords: 'agents subagents profiles' },
  { label: 'MCP', path: '/mcp', icon: Network, keywords: 'mcp servers connections' },
  { label: 'Memory', path: '/memory', icon: Brain, keywords: 'memory facts recall persistent' },
  { label: 'Sessions', path: '/sessions', icon: FolderClock, keywords: 'sessions history save resume' },
  { label: 'Config', path: '/config', icon: Settings, keywords: 'config settings model providers limits' },
  { label: 'Doctor', path: '/doctor', icon: Stethoscope, keywords: 'doctor health check diagnostics' },
]

const COMMANDS: CmdItem[] = [
  { label: '/help', command: 'help', description: 'Show the command list' },
  { label: '/capabilities', command: 'capabilities', description: 'Full self-report: tools, MCP, skills, limits' },
  { label: '/plan', command: 'plan', description: 'Enter plan mode — propose, approve, then run' },
  { label: '/audit on', command: 'audit', description: 'Verify each step against real files' },
  { label: '/raw', command: 'raw', description: 'One raw model call — content, reasoning, tool_calls' },
  { label: '/image', command: 'image', description: 'Analyze a screenshot/diagram with vision' },
  { label: '/paste', command: 'paste', description: 'Analyze an image from the clipboard' },
  { label: '/cli-anything', command: 'cli-anything', description: 'Find, run, build, refine, test CLI apps' },
  { label: '/model', command: 'model', description: 'Show/switch providers or add one' },
  { label: '/models', command: 'models', description: 'Pick provider/model or connect custom endpoint' },
  { label: '/mcp', command: 'mcp', description: 'List MCP servers, connection state, tools' },
  { label: '/sandbox', command: 'sandbox', description: 'Show/configure command isolation' },
  { label: '/mode', command: 'mode', description: 'Show or set permission mode' },
  { label: '/new', command: 'new', description: 'Create a named persistent session' },
  { label: '/sessions', command: 'sessions', description: 'List named sessions' },
  { label: '/resume', command: 'resume', description: 'Load a named session' },
  { label: '/reset', command: 'reset', description: 'Clear active session, keep its name' },
  { label: '/compact', command: 'compact', description: 'Summarize older turns, retain newest' },
  { label: '/save', command: 'save', description: 'Save conversation + last trace to JSON' },
  { label: '/clear', command: 'clear', description: 'Clear conversation context' },
  { label: '/history', command: 'history', description: 'Show current conversation' },
  { label: '/trace on', command: 'trace', description: 'Toggle step-by-step JSON trace' },
  { label: '/reasoning on', command: 'reasoning', description: 'Show/hide model thinking' },
  { label: '/verbose on', command: 'verbose', description: 'Control tool activity detail' },
  { label: '/usage tokens', command: 'usage', description: 'Control post-turn usage summaries' },
  { label: '/temp', command: 'temp', description: 'Set sampling temperature' },
  { label: '/maxturns', command: 'maxturns', description: 'Set max agent turns' },
  { label: '/limits', command: 'limits', description: 'Show/change turn, budget, tool limits' },
  { label: '/dump', command: 'dump', description: 'Write a redacted diagnostic bundle' },
  { label: '/memory search', command: 'memory', description: 'Persistent memory: search, add, forget, toggle' },
  { label: '/status', command: 'status', description: 'Model, context, tool, skill, session status' },
  { label: '/doctor', command: 'doctor', description: 'Check endpoint, auth, tools, skills' },
  { label: '/config', command: 'config', description: 'Show active configuration' },
  { label: '/tools', command: 'tools', description: 'List every tool with args, mode, description' },
  { label: '/tool', command: 'tool', description: 'Invoke ONE tool directly' },
  { label: '/skills', command: 'skills', description: 'Browse a skill or enable/disable' },
  { label: '/agents', command: 'agents', description: 'List available sub-agent profiles' },
  { label: '/agent', command: 'agent', description: 'Run a sub-agent' },
]

type ResultEntry =
  | { kind: 'nav'; item: NavItem }
  | { kind: 'cmd'; item: CmdItem }

export function CommandPalette() {
  const { commandPaletteOpen, setCommandPaletteOpen } = useUIStore()
  const navigate = useNavigate()
  const { send } = useWebSocket()
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // Reset state on open
  useEffect(() => {
    if (commandPaletteOpen) {
      setQuery('')
      setSelected(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [commandPaletteOpen])

  // Filter results
  const results = useMemo<ResultEntry[]>(() => {
    const q = query.trim().toLowerCase()
    if (!q) {
      return [
        ...NAV_ITEMS.map(item => ({ kind: 'nav' as const, item })),
        ...COMMANDS.slice(0, 8).map(item => ({ kind: 'cmd' as const, item })),
      ]
    }

    const navMatches = NAV_ITEMS.filter(item =>
      item.label.toLowerCase().includes(q) ||
      item.keywords.toLowerCase().includes(q) ||
      item.path.toLowerCase().includes(q),
    ).map(item => ({ kind: 'nav' as const, item }))

    const cmdMatches = COMMANDS.filter(item =>
      item.label.toLowerCase().includes(q) ||
      item.description.toLowerCase().includes(q),
    ).map(item => ({ kind: 'cmd' as const, item }))

    return [...navMatches, ...cmdMatches]
  }, [query])

  // Clamp selected index
  useEffect(() => {
    if (selected >= results.length) setSelected(0)
  }, [results.length, selected])

  // Scroll selected item into view
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${selected}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  // Keyboard navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelected(s => Math.min(s + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelected(s => Math.max(s - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      selectResult(results[selected])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setCommandPaletteOpen(false)
    }
  }

  const selectResult = (entry: ResultEntry | undefined) => {
    if (!entry) return
    if (entry.kind === 'nav') {
      navigate(entry.item.path)
    } else {
      send({ type: 'command', command: entry.item.command, args: '' })
    }
    setCommandPaletteOpen(false)
  }

  if (!commandPaletteOpen) return null

  const navResults = results.filter(r => r.kind === 'nav')
  const cmdResults = results.filter(r => r.kind === 'cmd')

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-20"
      onClick={() => setCommandPaletteOpen(false)}
    >
      <div
        className="w-full max-w-xl overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900"
        onClick={(e) => e.stopPropagation()}
        style={{ animation: 'pop-in 180ms cubic-bezier(0.23,1,0.32,1) both' }}
      >
        {/* Search input */}
        <div className="flex items-center gap-2.5 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <Search className="h-4 w-4 shrink-0 text-zinc-400 dark:text-zinc-500" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setSelected(0) }}
            onKeyDown={handleKeyDown}
            placeholder="Search pages and commands…"
            className="w-full bg-transparent text-sm text-zinc-900 outline-none placeholder:text-zinc-400 dark:text-zinc-100 dark:placeholder:text-zinc-600"
          />
          <kbd className="shrink-0 rounded border border-zinc-200 bg-zinc-100 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-500">
            esc
          </kbd>
        </div>

        {/* Results */}
        <div ref={listRef} className="max-h-[60vh] overflow-y-auto py-1">
          {results.length === 0 && (
            <div className="px-4 py-8 text-center text-sm text-zinc-400 dark:text-zinc-500">
              No results for "{query}"
            </div>
          )}

          {/* Navigate section */}
          {navResults.length > 0 && (
            <>
              <div className="px-3 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-600">
                Navigate
              </div>
              {navResults.map((entry) => {
                const idx = results.indexOf(entry)
                const item = entry.item
                const Icon = item.icon
                return (
                  <button
                    key={`nav-${item.path}`}
                    data-idx={idx}
                    onClick={() => selectResult(entry)}
                    className={cn(
                      'flex w-full items-center gap-3 px-3 py-2 text-left transition-colors',
                      idx === selected
                        ? 'bg-zinc-100 dark:bg-zinc-800/60'
                        : 'hover:bg-zinc-50 dark:hover:bg-zinc-800/30',
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0 text-brand-cyan" />
                    <span className="flex-1 text-sm text-zinc-800 dark:text-zinc-200">{item.label}</span>
                    {idx === selected && <ArrowRight className="h-3.5 w-3.5 text-zinc-400" />}
                  </button>
                )
              })}
            </>
          )}

          {/* Commands section */}
          {cmdResults.length > 0 && (
            <>
              <div className="mt-1 px-3 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-600">
                Commands
              </div>
              {cmdResults.map((entry) => {
                const idx = results.indexOf(entry)
                const item = entry.item
                return (
                  <button
                    key={`cmd-${item.command}`}
                    data-idx={idx}
                    onClick={() => selectResult(entry)}
                    className={cn(
                      'flex w-full items-center gap-3 px-3 py-2 text-left transition-colors',
                      idx === selected
                        ? 'bg-zinc-100 dark:bg-zinc-800/60'
                        : 'hover:bg-zinc-50 dark:hover:bg-zinc-800/30',
                    )}
                  >
                    <Terminal className="h-4 w-4 shrink-0 text-zinc-400 dark:text-zinc-500" />
                    <div className="min-w-0 flex-1">
                      <span className="font-mono text-sm text-brand-cyan">{item.label}</span>
                      <span className="ml-2 text-xs text-zinc-500 dark:text-zinc-500">{item.description}</span>
                    </div>
                    {idx === selected && <CornerDownLeft className="h-3.5 w-3.5 text-zinc-400" />}
                  </button>
                )
              })}
            </>
          )}
        </div>

        {/* Footer hint */}
        <div className="flex items-center gap-3 border-t border-zinc-200 px-4 py-2 text-[11px] text-zinc-400 dark:border-zinc-800 dark:text-zinc-600">
          <span className="flex items-center gap-1">
            <kbd className="rounded border border-zinc-200 bg-zinc-100 px-1 dark:border-zinc-700 dark:bg-zinc-800">↑↓</kbd>
            navigate
          </span>
          <span className="flex items-center gap-1">
            <kbd className="rounded border border-zinc-200 bg-zinc-100 px-1 dark:border-zinc-700 dark:bg-zinc-800">↵</kbd>
            select
          </span>
          <span className="flex items-center gap-1">
            <kbd className="rounded border border-zinc-200 bg-zinc-100 px-1 dark:border-zinc-700 dark:bg-zinc-800">esc</kbd>
            close
          </span>
        </div>
      </div>
    </div>
  )
}