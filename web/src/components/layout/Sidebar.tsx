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
      'flex flex-col border-r border-zinc-800 bg-zinc-950 transition-all duration-200',
      sidebarCollapsed ? 'w-16' : 'w-56',
    )}>
      <div className="flex h-14 items-center gap-2.5 border-b border-zinc-800/80 px-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-brand-border/20 bg-brand-primary/10 font-bold">
          <span className="text-xs tracking-tighter text-brand-cyan">
            8088
          </span>
        </div>
        {!sidebarCollapsed && (
          <span className="text-sm font-semibold tracking-tight text-zinc-200">Agent8088</span>
        )}
      </div>

      <nav className="flex-1 space-y-1 p-2">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) => cn(
              'flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
              isActive
                ? 'bg-brand-primary/15 text-brand-cyan'
                : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200',
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {!sidebarCollapsed && <span>{label}</span>}
          </NavLink>
        ))}
      </nav>

      <button
        onClick={toggleSidebar}
        className="flex h-10 items-center justify-center border-t border-zinc-800 text-zinc-500 hover:text-zinc-200"
      >
        {sidebarCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
      </button>
    </aside>
  )
}