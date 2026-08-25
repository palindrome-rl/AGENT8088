import { NavLink } from 'react-router-dom'
import {
  MessageSquare, Wrench, BookOpen, Bot, Network, Brain,
  FolderClock, Settings, Stethoscope, ChevronLeft, ChevronRight,
  Sun, Moon, Search, X, Plus,
} from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { cn } from '@/lib/utils'
import { useState, useEffect, useRef } from 'react'

/* ─────────────────────────────────────────────────────────
 * SIDEBAR NAV — Beautiful UI style
 * Smooth collapse, gliding highlight, search chats,
 * workspace logo, palindrome footer.
 * ───────────────────────────────────────────────────────── */

const navItems = [
  { to: '/', label: 'Chat', icon: MessageSquare },
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
  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (searchOpen) searchRef.current?.focus()
  }, [searchOpen])

  useEffect(() => {
    if (sidebarCollapsed) {
      setSearchOpen(false)
      setQuery('')
    }
  }, [sidebarCollapsed])

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
      <div className="flex min-h-0 w-[224px] shrink-0 flex-col">
        {/* Header — logo + collapse */}
        <div className="relative mb-2 h-10 shrink-0">
          {!sidebarCollapsed ? (
            <div className="absolute left-2 top-1 flex h-8 items-center gap-1.5 rounded-lg px-1">
              <img src="/logo.png" alt="Agent8088" className="h-7 w-auto" style={{ mixBlendMode: theme === 'dark' ? 'screen' : 'normal' }} />
            </div>
          ) : (
            <div className="absolute left-2 top-1 flex h-8 w-8 items-center justify-center">
              <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-brand-border/20 bg-brand-primary/10">
                <span className="text-[11px] font-bold tracking-tight text-brand-cyan">8088</span>
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
              <ChevronLeft className="h-4 w-4" />
            </button>
          )}
          {sidebarCollapsed && (
            <button
              type="button"
              aria-label="Expand sidebar"
              onClick={toggleSidebar}
              className="absolute left-2 top-0.5 flex h-9 w-9 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/50 hover:text-zinc-700 dark:hover:text-zinc-200"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* New chat button */}
        <div className="mx-2 mb-1">
          <button
            type="button"
            className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/40 active:scale-[0.98]"
          >
            <Plus className="h-4 w-4 shrink-0 text-zinc-500 dark:text-zinc-400" />
            {!sidebarCollapsed && (
              <span className="truncate text-[13px] font-medium text-zinc-700 dark:text-zinc-300">New chat</span>
            )}
          </button>
        </div>

        {/* Nav items */}
        <nav className="space-y-0.5 px-2 py-1">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) => cn(
                'flex h-8 items-center gap-2.5 rounded-lg px-2 text-left text-[13px] transition-colors duration-150 active:scale-[0.98]',
                isActive
                  ? 'bg-zinc-100 dark:bg-zinc-800/60 text-zinc-900 dark:text-zinc-100'
                  : 'text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800/40 hover:text-zinc-900 dark:hover:text-zinc-200',
              )}
            >
              <Icon className="h-[18px] w-[18px] shrink-0" />
              {!sidebarCollapsed && <span className="truncate">{label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* Search + recent chats area */}
        <div className="mt-3 min-h-0 flex-1 overflow-y-auto">
          {!sidebarCollapsed && (
            <>
              {/* Search control */}
              <div className="relative mx-2 mb-1 h-8">
                {!searchOpen ? (
                  <div className="absolute inset-0 flex items-center gap-1.5 px-2 text-[12.5px] font-medium text-zinc-400 dark:text-zinc-500">
                    <span className="text-zinc-400">Chats</span>
                  </div>
                ) : null}
                {!searchOpen && (
                  <button
                    type="button"
                    aria-label="Search chats"
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
                      placeholder="Search chats"
                      className="ml-1.5 min-w-0 flex-1 bg-transparent text-[13px] text-zinc-700 dark:text-zinc-200 outline-none placeholder:text-zinc-400"
                    />
                    <button
                      onClick={() => { setSearchOpen(false); setQuery('') }}
                      className="flex h-8 w-8 shrink-0 items-center justify-center text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )}
              </div>

              {/* Recent chats placeholder */}
              <div className="space-y-0.5 px-2">
                <div className="px-2 py-1.5 text-[12px] text-zinc-400 dark:text-zinc-500">
                  No recent chats
                </div>
              </div>
            </>
          )}
        </div>

        {/* Footer — theme toggle + palindrome + border */}
        <div className="border-t border-zinc-200 dark:border-zinc-800/60">
          <div className="flex items-center justify-between px-3 py-1.5">
            <button
              onClick={toggleTheme}
              className="flex items-center gap-1.5 text-[12px] text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-300 transition-colors"
            >
              {theme === 'dark' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
              {!sidebarCollapsed && <span>{theme === 'dark' ? 'Light' : 'Dark'}</span>}
            </button>
          </div>
          {!sidebarCollapsed && (
            <div className="flex items-center justify-center py-1">
              <img src="/palindrome-logo.png" alt="Palindrome" className="h-5 w-auto opacity-60" style={{ mixBlendMode: theme === 'dark' ? 'screen' : 'normal' }} />
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}