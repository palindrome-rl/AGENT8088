import { create } from 'zustand'

type Theme = 'dark' | 'light'

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
  theme: 'dark',
  approvalPending: null,
  planApprovalPending: null,

  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
  toggleTheme: () => set((s) => ({ theme: s.theme === 'dark' ? 'light' : 'dark' })),
  setTheme: (theme) => set({ theme }),
  setApprovalPending: (approval) => set({ approvalPending: approval }),
  setPlanApprovalPending: (plan) => set({ planApprovalPending: plan }),
}))