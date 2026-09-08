import { create } from 'zustand'

export type Theme = 'dark' | 'light' | 'system'
export type Accent = 'ocean' | 'violet' | 'ember' | 'mono'
export type Motion = 'full' | 'reduced'

const savedTheme = typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-theme') : null
const initialTheme: Theme = savedTheme === 'light' || savedTheme === 'system' ? savedTheme : 'dark'
const savedAccent = typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-accent') : null
const initialAccent: Accent = savedAccent === 'violet' || savedAccent === 'ember' || savedAccent === 'mono' ? savedAccent : 'ocean'
const savedMotion = typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-motion') : null
const initialMotion: Motion = savedMotion === 'reduced'
  || (savedMotion === null && typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  ? 'reduced'
  : 'full'

interface UIState {
  sidebarCollapsed: boolean
  commandPaletteOpen: boolean
  theme: Theme
  accent: Accent
  motion: Motion
  approvalPending: {
    id: string
    toolName: string
    changeType: string
    description: string
  } | null
  planApprovalPending: {
    id: string
    plan: string
  } | null
  rawPanelOpen: boolean

  toggleSidebar: () => void
  setCommandPaletteOpen: (open: boolean) => void
  toggleTheme: () => void
  setTheme: (theme: Theme) => void
  setAccent: (accent: Accent) => void
  toggleMotion: () => void
  setApprovalPending: (approval: UIState['approvalPending']) => void
  setPlanApprovalPending: (plan: UIState['planApprovalPending']) => void
  toggleRawPanel: () => void
  setRawPanelOpen: (open: boolean) => void
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: false,
  commandPaletteOpen: false,
  theme: initialTheme,
  accent: initialAccent,
  motion: initialMotion,
  approvalPending: null,
  planApprovalPending: null,
  rawPanelOpen: false,

  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
  toggleTheme: () => set((s) => {
    const theme = s.theme === 'dark' ? 'light' : s.theme === 'light' ? 'system' : 'dark'
    window.localStorage.setItem('agent8088-theme', theme)
    return { theme }
  }),
  setTheme: (theme) => {
    window.localStorage.setItem('agent8088-theme', theme)
    set({ theme })
  },
  setAccent: (accent) => {
    window.localStorage.setItem('agent8088-accent', accent)
    set({ accent })
  },
  toggleMotion: () => set((s) => {
    const motion = s.motion === 'full' ? 'reduced' : 'full'
    window.localStorage.setItem('agent8088-motion', motion)
    return { motion }
  }),
  setApprovalPending: (approval) => set({ approvalPending: approval }),
  setPlanApprovalPending: (plan) => set({ planApprovalPending: plan }),
  toggleRawPanel: () => set((s) => ({ rawPanelOpen: !s.rawPanelOpen })),
  setRawPanelOpen: (open) => set({ rawPanelOpen: open }),
}))
