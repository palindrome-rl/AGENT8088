import { NavLink } from 'react-router-dom'
import {
  MessageSquare, Wrench, BookOpen, Bot, Network, Brain,
  FolderClock, Settings, Stethoscope, ChevronLeft, ChevronRight,
} from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { cn } from '@/lib/utils'

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

export function Sidebar() {
  const { sidebarCollapsed, toggleSidebar } = useUIStore()

  return (
    <aside className={cn(
      'sidebar-transition relative flex flex-col border-r border-zinc-800/60 bg-zinc-950 overflow-hidden',
      sidebarCollapsed ? 'w-14' : 'w-56',
    )}>
      {/* Header */}
      <div className="flex h-12 items-center gap-2 px-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-brand-border/20 bg-brand-primary/10">
          <span className="text-[11px] font-bold tracking-tight text-brand-cyan">
            8088
          </span>
        </div>
        {!sidebarCollapsed && (
          <span className="text-sm font-medium tracking-tight text-zinc-200">Agent8088</span>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 px-2 py-2">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) => cn(
              'flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[13px] transition-colors duration-150',
              isActive
                ? 'bg-brand-primary/10 text-brand-cyan'
                : 'text-zinc-400 hover:bg-zinc-800/40 hover:text-zinc-200',
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {!sidebarCollapsed && <span className="truncate">{label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={toggleSidebar}
        className="flex h-8 items-center justify-center border-t border-zinc-800/60 text-zinc-600 hover:text-zinc-300"
      >
        {sidebarCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
      </button>
    </aside>
  )
}