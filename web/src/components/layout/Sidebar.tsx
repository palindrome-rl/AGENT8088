import { NavLink, useNavigate } from 'react-router-dom'
import {
  MessageSquare, Wrench, BookOpen, Bot, Network, Brain,
  FolderClock, Settings, Stethoscope,
  PanelLeftClose, PanelLeftOpen, Sun, Moon, Search, X, Plus,
} from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useUIStore } from '@/stores/ui'
import { useSessionStore } from '@/stores/session'
import { cn } from '@/lib/utils'
import { useState, useEffect, useRef } from 'react'
import type { ChatMessage, SessionInfo } from '@/types/api'

/* ─────────────────────────────────────────────────────────
 * SIDEBAR NAV — Beautiful UI style
 * Smooth collapse, gliding highlight, search chats,
 * workspace logo, palindrome footer.
 * ───────────────────────────────────────────────────────── */

const settingsItems = [
  { to: '/tools', label: 'Tools', icon: Wrench },
  { to: '/skills', label: 'Skills', icon: BookOpen },
  { to: '/agents', label: 'Sub-Agents', icon: Bot },
  { to: '/mcp', label: 'MCP', icon: Network },
  { to: '/memory', label: 'Memory', icon: Brain },
  { to: '/sessions', label: 'Sessions', icon: FolderClock },
  { to: '/config', label: 'Config', icon: Settings },
  { to: '/doctor', label: 'Doctor', icon: Stethoscope },
]

const SIDEBAR_EXPANDED = 224
const SIDEBAR_COLLAPSED = 52

export function Sidebar() {
  const { sidebarCollapsed, toggleSidebar, theme, toggleTheme } = useUIStore()
  const { clearChat, setMessages } = useSessionStore()
  const navigate = useNavigate()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [resuming, setResuming] = useState<string | null>(null)
  const [sessionError, setSessionError] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)

  const sessionsQuery = useQuery({
    queryKey: ['sessions'],
    queryFn: async () => {
      const response = await fetch('/api/sessions')
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.json() as Promise<SessionInfo[]>
    },
  })

  useEffect(() => {
    const media = window.matchMedia('(max-width: 720px)')
    const syncSidebar = () => {
      useUIStore.setState({ sidebarCollapsed: media.matches })
    }
    syncSidebar()
    media.addEventListener('change', syncSidebar)
    return () => media.removeEventListener('change', syncSidebar)
  }, [])

  useEffect(() => {
    if (searchOpen) searchRef.current?.focus()
  }, [searchOpen])

  useEffect(() => {
    if (sidebarCollapsed) {
      setSearchOpen(false)
      setQuery('')
    }
  }, [sidebarCollapsed])

  useEffect(() => {
    if (!settingsOpen) return
    const closeSettings = (event: PointerEvent) => {
      if (!(event.target as Element)?.closest('[data-settings-menu]')) setSettingsOpen(false)
    }
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSettingsOpen(false)
    }
    document.addEventListener('pointerdown', closeSettings)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('pointerdown', closeSettings)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [settingsOpen])

  const handleNewChat = () => {
    clearChat()
    setSettingsOpen(false)
    navigate('/')
  }

  const handleResume = async (name: string) => {
    setResuming(name)
    setSessionError('')
    try {
      const response = await fetch('/api/sessions/resume', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const historyResponse = await fetch('/api/history')
      if (!historyResponse.ok) throw new Error(`HTTP ${historyResponse.status}`)
      const history = await historyResponse.json() as { messages: ChatMessage[] }
      clearChat()
      setMessages(history.messages)
      await sessionsQuery.refetch()
      navigate('/')
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : 'Could not resume session')
    } finally {
      setResuming(null)
    }
  }

  const sessions = sessionsQuery.data ?? []
  const filteredSessions = sessions.filter((session) =>
    session.name.toLowerCase().includes(query.trim().toLowerCase()),
  )

  return (
    <aside
      data-sidebar-collapsed={sidebarCollapsed}
      aria-label="Workspace navigation"
      className="relative flex shrink-0 flex-col overflow-hidden border-r border-zinc-200 dark:border-zinc-800/60 bg-white dark:bg-zinc-950"
      style={{
        width: sidebarCollapsed ? SIDEBAR_COLLAPSED : SIDEBAR_EXPANDED,
        transitionDuration: '280ms',
        transitionTimingFunction: 'cubic-bezier(0.16, 1, 0.3, 1)',
      }}
    >
      <div className="flex h-full min-h-0 w-[224px] shrink-0 flex-col">
        {/* Header — logo + collapse */}
        <div className="relative mb-2 h-10 shrink-0">
          {!sidebarCollapsed ? (
            <button
              type="button"
              aria-label="Go to home"
              onClick={() => {
                setSettingsOpen(false)
                navigate('/')
              }}
              className="absolute left-2 top-1 flex h-8 items-center gap-1.5 rounded-lg px-1 transition-opacity hover:opacity-80"
            >
              <img src="/logo.png" alt="Agent8088" className="h-9 w-auto" style={{ mixBlendMode: theme === 'dark' ? 'screen' : 'normal', filter: theme === 'light' ? 'invert(1)' : undefined }} />
            </button>
          ) : (
            <div className="absolute left-2 top-1 flex h-8 w-8 items-center justify-center">
              <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-brand-border/20 bg-brand-primary/10">
                <img
                  src="/palindrome-logo.png"
                  alt="Palindrome"
                  className="h-5 w-6 object-contain"
                  style={{ mixBlendMode: theme === 'dark' ? 'screen' : 'normal', filter: theme === 'light' ? 'invert(1)' : undefined }}
                />
              </div>
            </div>
          )}

          {/* Collapse button */}
          {!sidebarCollapsed && (
            <button
              type="button"
              aria-label="Collapse sidebar"
              onClick={toggleSidebar}
              className="absolute right-2 top-1 flex h-8 w-8 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/50 hover:text-zinc-700 dark:hover:text-zinc-200"
            >
              <PanelLeftClose className="h-4 w-4" />
            </button>
          )}
          {sidebarCollapsed && (
            <button
              type="button"
              aria-label="Expand sidebar"
              onClick={toggleSidebar}
              className="absolute left-2 top-0.5 flex h-9 w-9 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/50 hover:text-zinc-700 dark:hover:text-zinc-200"
            >
              <PanelLeftOpen className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* New chat button */}
        <div className="mx-2 mb-1">
          <button
            type="button"
            onClick={handleNewChat}
            className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/40 active:scale-[0.98]"
          >
            <Plus className="h-4 w-4 shrink-0 text-zinc-500 dark:text-zinc-400" />
            {!sidebarCollapsed && (
              <span className="truncate text-[13px] font-medium text-zinc-700 dark:text-zinc-300">New chat</span>
            )}
          </button>
        </div>

        {/* Primary chat entry */}
        <nav className="px-2 py-1">
          <NavLink
            to="/"
            end
            className={({ isActive }) => cn(
              'flex h-8 items-center gap-2.5 rounded-lg px-2 text-left text-[13px] transition-colors duration-150 active:scale-[0.98]',
              isActive
                ? 'bg-zinc-100 dark:bg-zinc-800/60 text-zinc-900 dark:text-zinc-100'
                : 'text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800/40 hover:text-zinc-900 dark:hover:text-zinc-200',
            )}
          >
            <MessageSquare className="h-[18px] w-[18px] shrink-0" />
            {!sidebarCollapsed && <span className="truncate">Chat</span>}
          </NavLink>
        </nav>

        {/* Sessions */}
        <div className="mt-3 min-h-0 flex-1 overflow-y-auto">
          {!sidebarCollapsed && (
            <>
              <div className="relative mx-2 mb-1 flex h-8 items-center justify-between px-2">
                {!searchOpen ? (
                  <span className="text-[12.5px] font-medium text-zinc-400 dark:text-zinc-500">Sessions</span>
                ) : null}
                {!searchOpen && (
                  <button
                    type="button"
                    aria-label="Search sessions"
                    onClick={() => setSearchOpen(true)}
                    className="absolute right-0 top-0 z-10 flex h-8 w-8 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/40 hover:text-zinc-700 dark:hover:text-zinc-200"
                  >
                    <Search className="h-4 w-4" />
                  </button>
                )}
                {searchOpen && (
                  <div
                    className="absolute inset-0 z-20 flex h-8 items-center overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900"
                    style={{ animation: 'pop-in 180ms cubic-bezier(0.23,1,0.32,1) both' }}
                  >
                    <Search className="ml-2 h-3.5 w-3.5 shrink-0 text-zinc-400" />
                    <input
                      ref={searchRef}
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      onKeyDown={(e) => e.key === 'Escape' && (setSearchOpen(false), setQuery(''))}
                    placeholder="Search sessions"
                      className="ml-1.5 min-w-0 flex-1 bg-transparent text-[13px] text-zinc-700 dark:text-zinc-200 outline-none placeholder:text-zinc-400"
                    />
                    <button
                      type="button"
                      aria-label="Close chat search"
                      onClick={() => { setSearchOpen(false); setQuery('') }}
                      className="flex h-8 w-8 shrink-0 items-center justify-center text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )}
              </div>

              <div className="space-y-0.5 px-2">
                {sessionsQuery.isLoading && (
                  <div className="px-2 py-1.5 text-[12px] text-zinc-500">Loading sessions…</div>
                )}
                {!sessionsQuery.isLoading && filteredSessions.length === 0 && (
                  <div className="px-2 py-1.5 text-[12px] text-zinc-500">
                    {query ? 'No matching sessions' : 'No saved sessions'}
                  </div>
                )}
                {sessionError && (
                  <div className="px-2 py-1.5 text-[11px] text-red-500 dark:text-red-400">{sessionError}</div>
                )}
                {filteredSessions.map((session) => (
                  <button
                    key={session.name}
                    type="button"
                    disabled={session.active || resuming !== null}
                    onClick={() => handleResume(session.name)}
                    className={cn(
                      'group flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-[12.5px] transition-colors',
                      session.active
                        ? 'bg-brand-primary/10 text-brand-cyan'
                        : 'text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800/40 hover:text-zinc-900 dark:hover:text-zinc-200',
                    )}
                    title={session.active ? 'Active session' : `Resume ${session.name}`}
                  >
                    <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', session.active ? 'bg-brand-cyan' : 'bg-zinc-400 dark:bg-zinc-600')} />
                    <span className="min-w-0 flex-1 truncate">{session.name}</span>
                    {resuming === session.name && <span className="text-[10px] text-brand-cyan">…</span>}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

  {/* Footer — Palindrome branding above Settings, pinned to the bottom */}
  <div className="mt-auto border-t border-zinc-200 dark:border-zinc-800/60">
    <div className="flex items-center justify-center py-1">
      <img src="/palindrome-logo.png" alt="Palindrome" className="h-5 w-auto opacity-60" style={{ mixBlendMode: theme === 'dark' ? 'screen' : 'normal', filter: theme === 'light' ? 'invert(1)' : undefined }} />
    </div>
    <div data-settings-menu className="relative">
            <button
              type="button"
              aria-label="Settings"
              aria-expanded={settingsOpen}
              onClick={() => {
                if (sidebarCollapsed) toggleSidebar()
                setSettingsOpen((open) => !open)
              }}
              className="mx-2 mt-2 flex h-8 w-[calc(100%-1rem)] items-center gap-2 rounded-lg px-2 text-left text-[13px] text-zinc-500 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/40 hover:text-zinc-900 dark:hover:text-zinc-200"
            >
              <Settings className="h-4 w-4 shrink-0" />
              {!sidebarCollapsed && <span>Settings</span>}
            </button>

            {settingsOpen && (
              <div
                role="menu"
                aria-label="Settings menu"
                className="absolute bottom-full left-2 right-2 z-30 mb-2 max-h-[70vh] overflow-y-auto rounded-xl border border-zinc-200 bg-white p-2 shadow-2xl shadow-black/15 dark:border-zinc-700 dark:bg-zinc-900 dark:shadow-black/40"
              >
                <div className="flex items-center justify-between border-b border-zinc-200 px-2 pb-2 dark:border-zinc-800/60">
                  <div className="flex items-center gap-2 text-sm font-semibold text-zinc-800 dark:text-zinc-200">
                    <Settings className="h-4 w-4 text-brand-cyan" />
                    Settings
                  </div>
                  <button
                    type="button"
                    aria-label="Close settings"
                    onClick={() => setSettingsOpen(false)}
                    className="flex h-7 w-7 items-center justify-center rounded-lg text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800/50 hover:text-zinc-800 dark:hover:text-zinc-200"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <nav className="space-y-0.5 py-2">
                  {settingsItems.map(({ to, label, icon: Icon }) => (
                    <NavLink
                      key={to}
                      to={to}
                      onClick={() => setSettingsOpen(false)}
                      className={({ isActive }) => cn(
                        'flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-[13px] transition-colors',
                        isActive
                          ? 'bg-zinc-100 text-zinc-900 dark:bg-zinc-800/60 dark:text-zinc-100'
                          : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/40 dark:hover:text-zinc-200',
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      {label}
                    </NavLink>
                  ))}
                </nav>
                <div className="border-t border-zinc-200 pt-2 dark:border-zinc-800/60">
                  <span className="px-2.5 text-[11px] font-medium uppercase tracking-wider text-zinc-400 dark:text-zinc-500">Appearance</span>
                  <button
                    type="button"
                    aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                    aria-pressed={theme === 'dark'}
                    onClick={toggleTheme}
                    className="mt-1 flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[13px] text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/40 dark:hover:text-zinc-200"
                  >
                    {theme === 'dark' ? <Sun className="h-4 w-4 shrink-0" /> : <Moon className="h-4 w-4 shrink-0" />}
                    {theme === 'dark' ? 'Light mode' : 'Dark mode'}
                  </button>
                </div>
              </div>
      )}
    </div>
  </div>
      </div>
    </aside>
  )
}
