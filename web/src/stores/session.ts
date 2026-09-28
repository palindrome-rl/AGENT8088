import { create } from 'zustand'
import type { ChatAttachment, ChatMessage, StatusInfo, ToolDiffPayload } from '@/types/api'

export interface QueuedPrompt {
  id: string
  /** A leading "/" makes it a command; the distinction is fixed at enqueue
   *  time so an edit cannot silently turn a chat turn into a command. */
  kind: 'chat' | 'command'
  text: string
  attachmentIds: string[]
  attachments?: ChatAttachment[]
}

interface SessionState {
  sessionName: string
  messages: ChatMessage[]
  status: StatusInfo | null
  isStreaming: boolean
  activity: string
  streamingText: string
  streamingReasoning: string[]
  toolEvents: Array<{
    name: string
    status: 'running' | 'done' | 'failed'
    result?: string
    diff?: ToolDiffPayload
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
  /** Live WebSocket state. Without this the UI had no way to know the socket
   *  was gone and kept the status bar reading "ready" over a dead connection. */
  connection: 'connecting' | 'open' | 'reconnecting' | 'failed'
  /** Set when a send could not be delivered, so the composer can say so
   *  instead of spinning on a message that was never transmitted. */
  sendFailure: string
  /** Prompts typed while a turn was running, dispatched one per turn in order.
   *  The backend refuses a second concurrent turn, so without this the composer
   *  could only drop what the user typed mid-turn. */
  promptQueue: QueuedPrompt[]
  /** A stopped or failed turn holds the queue instead of draining it: the next
   *  prompt usually assumes the last one succeeded. Cleared by Resume. */
  queuePaused: boolean

  setMessages: (messages: ChatMessage[]) => void
  setRawResult: (result: SessionState['rawResult']) => void
  setRawLoading: (loading: boolean) => void
  setSessionName: (name: string) => void
  clearChat: () => void
  addMessage: (message: ChatMessage) => void
  setStatus: (status: StatusInfo) => void
  setConnection: (connection: SessionState['connection']) => void
  setSendFailure: (message: string) => void
  enqueuePrompt: (prompt: Omit<QueuedPrompt, 'id'>) => void
  updateQueuedPrompt: (id: string, text: string) => void
  removeQueuedPrompt: (id: string) => void
  clearQueue: () => void
  setQueuePaused: (paused: boolean) => void
  setStreaming: (streaming: boolean) => void
  setActivity: (activity: string) => void
  appendStreamingText: (delta: string) => void
  appendStreamingReasoning: (delta: string) => void
  resetStreaming: () => void
  addToolEvent: (name: string) => void
  updateToolEvent: (name: string, result: string, diff?: ToolDiffPayload) => void
  updatePlanStep: (step: Partial<SessionState['planSteps'][0]> & { index: number }) => void
  resetToolEvents: () => void
}

export const useSessionStore = create<SessionState>((set) => ({
  sessionName: '',
  messages: [],
  status: null,
  isStreaming: false,
  activity: 'Working',
  streamingText: '',
  streamingReasoning: [],
  toolEvents: [],
  planSteps: [],
  rawResult: null,
  rawLoading: false,
  connection: 'connecting',
  sendFailure: '',
  promptQueue: [],
  queuePaused: false,

  setMessages: (messages) => set({ messages }),
  setRawResult: (rawResult) => set({ rawResult }),
  setRawLoading: (rawLoading) => set({ rawLoading }),
  setSessionName: (sessionName) => set(s => ({ sessionName,
    status: s.status ? { ...s.status, session_name: sessionName } : null })),
  clearChat: () => set({
    messages: [],
    isStreaming: false,
    streamingText: '',
    streamingReasoning: [],
    toolEvents: [],
    planSteps: [],
    // Queued prompts were written for the conversation being cleared.
    promptQueue: [],
    queuePaused: false,
  }),
  addMessage: (message) => set((s) => ({ messages: [...s.messages, message] })),
  setStatus: (status) => set({ status }),
  setConnection: (connection) => set({ connection }),
  setSendFailure: (sendFailure) => set({ sendFailure }),
  enqueuePrompt: (prompt) => set((s) => ({
    promptQueue: [...s.promptQueue, { ...prompt, id: crypto.randomUUID() }],
  })),
  updateQueuedPrompt: (id, text) => set((s) => ({
    promptQueue: s.promptQueue.map((p) => p.id === id ? { ...p, text } : p),
  })),
  removeQueuedPrompt: (id) => set((s) => ({
    promptQueue: s.promptQueue.filter((p) => p.id !== id),
  })),
  clearQueue: () => set({ promptQueue: [], queuePaused: false }),
  setQueuePaused: (queuePaused) => set({ queuePaused }),
  setStreaming: (streaming) => set({ isStreaming: streaming }),
  setActivity: (activity) => set({ activity }),
  appendStreamingText: (delta) => set((s) => ({ streamingText: s.streamingText + delta })),
  appendStreamingReasoning: (delta) => set((s) => ({
    streamingReasoning: [...s.streamingReasoning, delta],
  })),
  resetStreaming: () => set({ streamingText: '', streamingReasoning: [], toolEvents: [], planSteps: [] }),
  addToolEvent: (name) => set((s) => ({
    toolEvents: [...s.toolEvents, { name, status: 'running' }],
  })),
  updateToolEvent: (name, result, diff) => set((s) => ({
    toolEvents: s.toolEvents.map((e) =>
      e.name === name && e.status === 'running'
        ? { ...e, status: /^(Error:|Invalid JSON|Command timed out)/i.test(result) ? 'failed' : 'done', result, diff }
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
