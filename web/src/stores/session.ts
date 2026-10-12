import { create } from 'zustand'
import type { ChatAttachment, ChatMessage, StatusInfo, ToolDiffPayload, ToolEvent } from '@/types/api'
import { parseEscalation } from '@/lib/scrub'

export type { ToolEvent }

export interface QueuedPrompt {
  id: string
  /** A leading "/" makes it a command; the distinction is fixed at enqueue
   *  time so an edit cannot silently turn a chat turn into a command. */
  kind: 'chat' | 'command'
  text: string
  attachmentIds: string[]
  attachments?: ChatAttachment[]
  /** The chat it was typed in; it runs there even if the user moves on. */
  sessionId?: string
  sessionName?: string
}

/** What a background chat needs the user to see. */
export type ChatAttention = 'done' | 'failed' | 'approval'

interface SessionState {
  sessionName: string
  /** Stable id of the chat being viewed. */
  sessionId: string
  /** The chat that owns the running task (one at a time), which may not be
   *  the one being viewed. `isStreaming` is only about the viewed chat. */
  runningSession: { id: string; name: string } | null
  /** Background chats that finished, failed or wait for approval, by id. */
  attention: Record<string, ChatAttention>
  messages: ChatMessage[]
  status: StatusInfo | null
  isStreaming: boolean
  activity: string
  streamingText: string
  /** Reasoning sections, one per model step; deltas append to the last. */
  streamingReasoning: string[]
  toolEvents: ToolEvent[]
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
  setSessionId: (id: string) => void
  setRunningSession: (running: SessionState['runningSession']) => void
  setAttention: (id: string, attention: ChatAttention | null) => void
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
  appendStreamingReasoning: (delta: string, newSection?: boolean) => void
  resetStreaming: () => void
  addToolEvent: (name: string) => void
  updateToolEvent: (name: string, result: string, diff?: ToolDiffPayload) => void
  updatePlanStep: (step: Partial<SessionState['planSteps'][0]> & { index: number }) => void
  resetToolEvents: () => void
}

export const useSessionStore = create<SessionState>((set) => ({
  sessionName: '',
  sessionId: '',
  runningSession: null,
  attention: {},
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
  setSessionId: (sessionId) => set({ sessionId }),
  setRunningSession: (runningSession) => set({ runningSession }),
  setAttention: (id, kind) => set((s) => {
    const attention = { ...s.attention }
    if (kind) attention[id] = kind
    else delete attention[id]
    return { attention }
  }),
  // Clears the view only. Queued prompts stay: each one names its own chat,
  // and switching chats while a task runs must not drop them.
  clearChat: () => set((s) => ({
    messages: [],
    isStreaming: false,
    streamingText: '',
    streamingReasoning: [],
    toolEvents: [],
    planSteps: [],
    // The status bar's "last turn" belonged to the old conversation (every
    // New chat / resume / delete path clears the chat through here).
    status: s.status ? { ...s.status, last_usage: null } : s.status,
  })),
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
  appendStreamingReasoning: (delta, newSection) => set((s) => {
    const sections = s.streamingReasoning
    return {
      streamingReasoning: newSection || !sections.length
        ? [...sections, delta]
        : [...sections.slice(0, -1), sections[sections.length - 1] + delta],
    }
  }),
  resetStreaming: () => set({ streamingText: '', streamingReasoning: [], toolEvents: [], planSteps: [] }),
  addToolEvent: (name) => set((s) => ({
    toolEvents: [...s.toolEvents, { name, status: 'running' }],
  })),
  updateToolEvent: (name, result, diff) => set((s) => {
    // A blocked call's result is the approval request, which the approval card
    // shows; the approved retry gets its own row. Drop the row rather than
    // leave the raw request code in the transcript.
    if (parseEscalation(result)) {
      const blocked = s.toolEvents.findIndex((e) => e.name === name && e.status === 'running')
      return blocked < 0 ? {} : { toolEvents: s.toolEvents.filter((_, i) => i !== blocked) }
    }
    return { toolEvents: s.toolEvents.map((e) =>
      e.name === name && e.status === 'running'
        ? { ...e, status: /^(Error:|Invalid JSON|Command timed out)/i.test(result) ? 'failed' : 'done', result, diff }
        : e
    ) }
  }),
  updatePlanStep: (step) => set((s) => ({
    planSteps: s.planSteps.some((p) => p.index === step.index)
      ? s.planSteps.map((p) => p.index === step.index ? { ...p, ...step } : p)
      : [...s.planSteps, step as SessionState['planSteps'][0]],
  })),
  resetToolEvents: () => set({ toolEvents: [], planSteps: [] }),
}))
