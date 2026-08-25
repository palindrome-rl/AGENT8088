import { useEffect } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Sidebar } from './Sidebar'
import { StatusBar } from './StatusBar'
import { CommandPalette } from '@/components/CommandPalette'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useUIStore } from '@/stores/ui'

export function AppLayout() {
  useWebSocket()
  const { theme } = useUIStore()
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    const html = document.documentElement
    if (theme === 'dark') {
      html.classList.add('dark')
    } else {
      html.classList.remove('dark')
    }
  }, [theme])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        useUIStore.getState().setCommandPaletteOpen(!useUIStore.getState().commandPaletteOpen)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  return (
    <div className="flex h-screen overflow-hidden bg-zinc-50 dark:bg-zinc-950">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        {location.pathname !== '/' && (
          <div className="flex h-11 shrink-0 items-center border-b border-zinc-200 bg-white px-4 dark:border-zinc-800/60 dark:bg-zinc-950">
            <button
              type="button"
              aria-label="Back to chat"
              onClick={() => navigate('/')}
              className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/50 dark:hover:text-zinc-200"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to chat
            </button>
          </div>
        )}
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
        <StatusBar />
      </div>
      <CommandPalette />
    </div>
  )
}
