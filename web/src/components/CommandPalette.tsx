import { useState, useRef, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Search, MessageSquare, Wrench, BookOpen, Bot, Network, Brain,
  FolderClock, FolderOpen, Settings, Stethoscope, Sparkles, ClipboardList,
  Terminal, ArrowRight, CornerDownLeft,
} from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useCommandCatalog } from '@/lib/commands'
import type { CommandInfo } from '@/types/api'
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

const NAV_ITEMS: NavItem[] = [
  { label: 'Chat', path: '/', icon: MessageSquare, keywords: 'chat message conversation' },
  { label: 'Artifacts', path: '/artifacts', icon: FolderOpen, keywords: 'artifacts files browse outputs' },
  { label: 'Tools', path: '/tools', icon: Wrench, keywords: 'tools functions invoke' },
  { label: 'Skills', path: '/skills', icon: BookOpen, keywords: 'skills packages knowledge' },
  { label: 'Sub-Agents', path: '/agents', icon: Bot, keywords: 'agents subagents profiles' },
  { label: 'Fusion', path: '/fusion', icon: Sparkles, keywords: 'fusion panel judge models answers' },
  { label: 'Durable Tasks', path: '/tasks', icon: ClipboardList, keywords: 'tasks durable resume progress output' },
  { label: 'MCP', path: '/mcp', icon: Network, keywords: 'mcp servers connections' },
  { label: 'Memory', path: '/memory', icon: Brain, keywords: 'memory facts recall persistent' },
  { label: 'Sessions', path: '/sessions', icon: FolderClock, keywords: 'sessions history save resume' },
  { label: 'Config', path: '/config', icon: Settings, keywords: 'config settings model providers limits' },
  { label: 'Doctor', path: '/doctor', icon: Stethoscope, keywords: 'doctor health check diagnostics' },
]

type ResultEntry =
  | { kind: 'nav'; item: NavItem }
  | { kind: 'cmd'; item: CommandInfo }

export function CommandPalette() {
  const { commandPaletteOpen, setCommandPaletteOpen } = useUIStore()
  const navigate = useNavigate()
  useWebSocket()
  const { data: commands = [] } = useCommandCatalog()
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
        ...commands.filter(item => item.name).slice(0, 8).map(item => ({ kind: 'cmd' as const, item })),
      ]
    }

    const navMatches = NAV_ITEMS.filter(item =>
      item.label.toLowerCase().includes(q) ||
      item.keywords.toLowerCase().includes(q) ||
      item.path.toLowerCase().includes(q),
    ).map(item => ({ kind: 'nav' as const, item }))

    const cmdMatches = commands.filter(item =>
      item.name.toLowerCase().includes(q) ||
      item.aliases.some(alias => alias.toLowerCase().includes(q)) ||
      item.usage.toLowerCase().includes(q) ||
      item.description.toLowerCase().includes(q),
    ).map(item => ({ kind: 'cmd' as const, item }))

    return [...navMatches, ...cmdMatches]
  }, [commands, query])

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
      navigate('/')
      window.dispatchEvent(new CustomEvent('agent8088:insert-command', {
        detail: `/${entry.item.name} `,
      }))
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
                    key={`cmd-${item.name}`}
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
                      <span className="font-mono text-sm text-brand-cyan">{item.usage}</span>
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
