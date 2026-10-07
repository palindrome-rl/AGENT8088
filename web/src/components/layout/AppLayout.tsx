import { useEffect } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Sidebar } from './Sidebar'
import { StatusBar } from './StatusBar'
import { ConnectionBanner } from './ConnectionBanner'
import { CapabilityBadge } from './CapabilityBadge'
import { CommandPalette } from '@/components/CommandPalette'
import { ModelSwitcher } from '@/components/ModelSwitcher'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useUIStore } from '@/stores/ui'

export function AppLayout() {
  useWebSocket()
  const { theme, accent, motion, customAccent, uiFont, contentFont, codeFont, sidebarTranslucent, contrast } = useUIStore()
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    const html = document.documentElement
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const syncTheme = () => {
      html.classList.toggle('dark', theme === 'dark' || (theme === 'system' && media.matches))
    }
    syncTheme()
    if (theme !== 'system') return
    media.addEventListener('change', syncTheme)
    return () => media.removeEventListener('change', syncTheme)
  }, [theme])

  useEffect(() => {
    const html = document.documentElement
    html.dataset.accent = accent
    html.dataset.motion = motion
    html.dataset.uiFont = uiFont
    html.dataset.contentFont = contentFont
    html.dataset.codeFont = codeFont
    html.dataset.sidebarTranslucent = String(sidebarTranslucent)
    html.style.setProperty('--ui-contrast', String(contrast / 100))
    if (accent !== 'custom') {
      for (const name of ['--brand-primary', '--brand-cyan', '--brand-border', '--brand-primary-rgb', '--brand-cyan-rgb', '--brand-border-rgb', '--ambient-primary-rgb', '--ambient-secondary-rgb']) html.style.removeProperty(name)
      return
    }
    const hex = customAccent.replace('#', '')
    if (!/^[0-9a-f]{6}$/i.test(hex)) return
    const rgb = `${parseInt(hex.slice(0, 2), 16)} ${parseInt(hex.slice(2, 4), 16)} ${parseInt(hex.slice(4, 6), 16)}`
    html.style.setProperty('--brand-primary', customAccent)
    html.style.setProperty('--brand-cyan', customAccent)
    html.style.setProperty('--brand-border', customAccent)
    for (const name of ['--brand-primary-rgb', '--brand-cyan-rgb', '--brand-border-rgb', '--ambient-primary-rgb', '--ambient-secondary-rgb']) html.style.setProperty(name, rgb)
  }, [accent, motion, customAccent, uiFont, contentFont, codeFont, sidebarTranslucent, contrast])

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
    <div className="app-shell relative flex h-screen overflow-hidden bg-zinc-50 dark:bg-zinc-950">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <ConnectionBanner />
        <div className="flex h-11 shrink-0 items-center gap-2 border-b border-zinc-200 bg-white px-3 dark:border-zinc-800/60 dark:bg-zinc-950">
          {location.pathname !== '/' && (
            <button
              type="button"
              aria-label="Back to chat"
              onClick={() => navigate('/')}
              className="flex shrink-0 items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/50 dark:hover:text-zinc-200"
            >
              <ArrowLeft className="h-4 w-4" />
              <span className="hidden sm:inline">Back to chat</span>
            </button>
          )}
          <ModelSwitcher />
          <CapabilityBadge />
        </div>
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
        <StatusBar />
      </div>
      <CommandPalette />
    </div>
  )
}
