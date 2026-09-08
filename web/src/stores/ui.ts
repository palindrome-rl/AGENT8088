import { create } from 'zustand'

export type Theme = 'dark' | 'light' | 'system'
export type Accent = 'ocean' | 'violet' | 'ember' | 'mono' | 'cobalt' | 'rose' | 'forest' | 'gold' | 'custom'
export type Motion = 'full' | 'reduced'
export type UIFont = 'system' | 'inter' | 'serif' | 'mono'
export type ContentFont = UIFont | 'ui'
export type CodeFont = 'system' | 'mono' | 'serif'

const savedTheme = typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-theme') : null
const initialTheme: Theme = savedTheme === 'light' || savedTheme === 'system' ? savedTheme : 'dark'
const savedAccent = typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-accent') : null
const initialAccent: Accent = ['violet', 'ember', 'mono', 'cobalt', 'rose', 'forest', 'gold', 'custom'].includes(savedAccent ?? '') ? savedAccent as Accent : 'ocean'
const savedMotion = typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-motion') : null
const initialMotion: Motion = savedMotion === 'reduced'
  || (savedMotion === null && typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  ? 'reduced'
  : 'full'
const savedUIFont = typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-ui-font') : null
const initialUIFont: UIFont = ['system', 'serif', 'mono'].includes(savedUIFont ?? '') ? savedUIFont as UIFont : 'inter'
const savedContentFont = typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-content-font') : null
const initialContentFont: ContentFont = ['system', 'inter', 'serif', 'mono'].includes(savedContentFont ?? '') ? savedContentFont as ContentFont : 'ui'
const savedCodeFont = typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-code-font') : null
const initialCodeFont: CodeFont = ['system', 'serif'].includes(savedCodeFont ?? '') ? savedCodeFont as CodeFont : 'mono'
const savedContrast = typeof window !== 'undefined' ? Number(window.localStorage.getItem('agent8088-contrast')) : NaN
const initialContrast = Number.isFinite(savedContrast) ? Math.min(115, Math.max(85, savedContrast)) : 100

interface UIState {
  sidebarCollapsed: boolean
  commandPaletteOpen: boolean
  theme: Theme
  accent: Accent
  motion: Motion
  customAccent: string
  uiFont: UIFont
  contentFont: ContentFont
  codeFont: CodeFont
  sidebarTranslucent: boolean
  contrast: number
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
  setCustomAccent: (color: string) => void
  setUIFont: (font: UIFont) => void
  setContentFont: (font: ContentFont) => void
  setCodeFont: (font: CodeFont) => void
  setSidebarTranslucent: (value: boolean) => void
  setContrast: (value: number) => void
  resetAppearance: () => void
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
  customAccent: typeof window !== 'undefined' ? window.localStorage.getItem('agent8088-custom-accent') || '#237dd7' : '#237dd7',
  uiFont: initialUIFont,
  contentFont: initialContentFont,
  codeFont: initialCodeFont,
  sidebarTranslucent: typeof window !== 'undefined' && window.localStorage.getItem('agent8088-sidebar-translucent') === 'true',
  contrast: initialContrast,
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
  setCustomAccent: (customAccent) => {
    window.localStorage.setItem('agent8088-custom-accent', customAccent)
    window.localStorage.setItem('agent8088-accent', 'custom')
    set({ accent: 'custom', customAccent })
  },
  setUIFont: (uiFont) => {
    window.localStorage.setItem('agent8088-ui-font', uiFont)
    set({ uiFont })
  },
  setContentFont: (contentFont) => {
    window.localStorage.setItem('agent8088-content-font', contentFont)
    set({ contentFont })
  },
  setCodeFont: (codeFont) => {
    window.localStorage.setItem('agent8088-code-font', codeFont)
    set({ codeFont })
  },
  setSidebarTranslucent: (sidebarTranslucent) => {
    window.localStorage.setItem('agent8088-sidebar-translucent', String(sidebarTranslucent))
    set({ sidebarTranslucent })
  },
  setContrast: (contrast) => {
    const value = Math.min(115, Math.max(85, contrast))
    window.localStorage.setItem('agent8088-contrast', String(value))
    set({ contrast: value })
  },
  resetAppearance: () => {
    for (const key of ['agent8088-accent', 'agent8088-custom-accent', 'agent8088-ui-font', 'agent8088-content-font', 'agent8088-code-font', 'agent8088-sidebar-translucent', 'agent8088-contrast', 'agent8088-motion']) window.localStorage.removeItem(key)
    set({ accent: 'ocean', customAccent: '#237dd7', uiFont: 'inter', contentFont: 'ui', codeFont: 'mono', sidebarTranslucent: false, contrast: 100, motion: 'full' })
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
