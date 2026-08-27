import { create } from 'zustand'
import type { ChatMessage, StatusInfo } from '@/types/api'

interface SessionState {
  sessionName: string
  messages: ChatMessage[]
  status: StatusInfo | null
  isStreaming: boolean
  streamingText: string
  streamingReasoning: string[]
  toolEvents: Array<{
    name: string
    status: 'running' | 'done'
    result?: string
  }>
  planSteps: Array<{
    index: number
    stepText: string
    toolName: string
    status: 'pending' | 'running' | 'done' | 'failed'
    result?: string
  }>
  rawResult: { content: string; reasoning?: string; tool_calls?: unknown } | null
  rawLoading: boolean

  setMessages: (messages: ChatMessage[]) => void
  setRawResult: (result: SessionState['rawResult']) => void
  setRawLoading: (loading: boolean) => void
  setSessionName: (name: string) => void
  clearChat: () => void
  addMessage: (message: ChatMessage) => void
  setStatus: (status: StatusInfo) => void
  setStreaming: (streaming: boolean) => void
  appendStreamingText: (delta: string) => void
  appendStreamingReasoning: (delta: string) => void
  resetStreaming: () => void
  addToolEvent: (name: string) => void
  updateToolEvent: (name: string, result: string) => void
  updatePlanStep: (step: Partial<SessionState['planSteps'][0]> & { index: number }) => void
  resetToolEvents: () => void
}

export const useSessionStore = create<SessionState>((set) => ({
  sessionName: '',
  messages: [],
  status: null,
  isStreaming: false,
  streamingText: '',
  streamingReasoning: [],
  toolEvents: [],
  planSteps: [],
  rawResult: null,
  rawLoading: false,

  setMessages: (messages) => set({ messages }),
  setRawResult: (rawResult) => set({ rawResult }),
  setRawLoading: (rawLoading) => set({ rawLoading }),
  setSessionName: (sessionName) => set({ sessionName }),
  clearChat: () => set({
    messages: [],
    status: null,
    isStreaming: false,
    streamingText: '',
    streamingReasoning: [],
    toolEvents: [],
    planSteps: [],
  }),
  addMessage: (message) => set((s) => ({ messages: [...s.messages, message] })),
  setStatus: (status) => set({ status }),
  setStreaming: (streaming) => set({ isStreaming: streaming }),
  appendStreamingText: (delta) => set((s) => ({ streamingText: s.streamingText + delta })),
  appendStreamingReasoning: (delta) => set((s) => ({
    streamingReasoning: [...s.streamingReasoning, delta],
  })),
  resetStreaming: () => set({ streamingText: '', streamingReasoning: [], toolEvents: [], planSteps: [] }),
  addToolEvent: (name) => set((s) => ({
    toolEvents: [...s.toolEvents, { name, status: 'running' }],
  })),
  updateToolEvent: (name, result) => set((s) => ({
    toolEvents: s.toolEvents.map((e) =>
      e.name === name && e.status === 'running'
        ? { ...e, status: 'done', result }
        : e
    ),
  })),
  updatePlanStep: (step) => set((s) => ({
    planSteps: s.planSteps.some((p) => p.index === step.index)
      ? s.planSteps.map((p) => p.index === step.index ? { ...p, ...step } : p)
      : [...s.planSteps, step as SessionState['planSteps'][0]],
  })),
  resetToolEvents: () => set({ toolEvents: [], planSteps: [] }),
}))
