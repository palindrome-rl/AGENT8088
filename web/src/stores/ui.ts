import { create } from 'zustand'

type Theme = 'dark' | 'light'

const savedTheme = typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-theme') : null
const initialTheme: Theme = savedTheme === 'light' ? 'light' : 'dark'

interface UIState {
  sidebarCollapsed: boolean
  commandPaletteOpen: boolean
  theme: Theme
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

  toggleSidebar: () => void
  setCommandPaletteOpen: (open: boolean) => void
  toggleTheme: () => void
  setTheme: (theme: Theme) => void
  setApprovalPending: (approval: UIState['approvalPending']) => void
  setPlanApprovalPending: (plan: UIState['planApprovalPending']) => void
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: false,
  commandPaletteOpen: false,
  theme: initialTheme,
  approvalPending: null,
  planApprovalPending: null,

  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
  toggleTheme: () => set((s) => {
    const theme = s.theme === 'dark' ? 'light' : 'dark'
    window.localStorage.setItem('agent8088-theme', theme)
    return { theme }
  }),
  setTheme: (theme) => {
    window.localStorage.setItem('agent8088-theme', theme)
    set({ theme })
  },
  setApprovalPending: (approval) => set({ approvalPending: approval }),
  setPlanApprovalPending: (plan) => set({ planApprovalPending: plan }),
}))
