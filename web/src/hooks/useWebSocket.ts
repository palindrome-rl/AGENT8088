import { useCallback, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { scrubMarkup } from '@/lib/scrub'
import type { ChatMessage, PendingApprovalFrame, StatusInfo, WSClientMessage, WSEvent } from '@/types/api'

/* ─────────────────────────────────────────────────────────
 * SINGLE SHARED WEBSOCKET
 *
 * AppLayout, PromptBar, and ApprovalCard all need `send`, but each
 * instance of this hook previously opened its OWN socket (3+ connections
 * per page load, zombie reconnects after unmount). The socket is now a
 * module-level singleton: the first consumer creates it, everyone shares
 * `send`, and reconnect only happens while the app is actually mounted.
 * ───────────────────────────────────────────────────────── */

let sharedWs: WebSocket | null = null
let disposed = false
let consumers = 0
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempts = 0
/** Messages that arrived while the socket was down, replayed on reconnect. */
let outbox: WSClientMessage[] = []
/** Once Stop is pressed, late stream frames must not turn the composer red again. */
let interruptRequested = false
/** A sent turn can reach the backend after a simultaneous status request. */
let localTurnPending = false
/** A model step began (spin 'thinking...' / tool_start) and has not streamed
 *  reasoning yet: its first delta opens a new section. Pending rather than an
 *  eager '' push, so a step without reasoning leaves no empty section. */
let reasoningBreak = false

const RECONNECT_BASE_MS = 1000
const RECONNECT_MAX_MS = 15_000

function wireSocket(ws: WebSocket) {
  const {
    setStreaming, appendStreamingText, appendStreamingReasoning,
    addToolEvent, updateToolEvent, updatePlanStep,
    resetStreaming, addMessage, setStatus, setSessionName,
  } = useSessionStore.getState()
  const { setApprovalPending, setPlanApprovalPending, setRawPanelOpen } = useUIStore.getState()

  ws.onmessage = (event) => {
    let data: WSEvent
    try {
      data = JSON.parse(event.data) as WSEvent
    } catch {
      // A frame we cannot parse is not worth tearing the socket down for.
      return
    }
    // A turn event belongs to the chat that started the turn. Switching chats
    // does not move it: events for another chat update that chat's badge, and
    // its saved history is re-read when the user opens it.
    const eventSession = data.session_id || ''
    const visible = !eventSession || eventSession === useSessionStore.getState().sessionId
    switch (data.type) {
      case 'status':
        setStatus(data.data)
        break
      case 'capabilities':
        {
          // A capability changed state server-side (search fell back, model
          // failed over, ...): patch just that slice of the status.
          const current = useSessionStore.getState().status
          if (current) setStatus({ ...current, capabilities: data.data })
        }
        break
      case 'spin':
        if (interruptRequested || !visible) break
        setStreaming(true)
        if (data.message === 'thinking...') reasoningBreak = true
        useSessionStore.getState().setActivity(data.message === 'thinking...'
          ? (useSessionStore.getState().toolEvents.length ? 'Reviewing results' : 'Preparing request')
          : data.message)
        break
      case 'token':
        if (interruptRequested || !visible) break
        if (data.kind === 'reasoning') {
          if (data.delta) {
            appendStreamingReasoning(data.delta, reasoningBreak)
            reasoningBreak = false
          }
        } else {
          appendStreamingText(data.delta)
          if (scrubMarkup(useSessionStore.getState().streamingText).trim()) {
            useSessionStore.getState().setActivity('Writing answer')
          }
        }
        break
      case 'stream_reset':
        // The reply broke off mid-stream and is being retried: drop the
        // fragment already shown so the retry doesn't append to it. Only the
        // current step's reasoning is the fragment; earlier steps stay.
        if (!visible) break
        useSessionStore.setState((s) => ({
          streamingText: '',
          streamingReasoning: reasoningBreak ? s.streamingReasoning : s.streamingReasoning.slice(0, -1),
        }))
        reasoningBreak = true
        break
      case 'tool_calls':
        break
      case 'tool_start':
        if (interruptRequested || !visible) break
        reasoningBreak = true
        addToolEvent(data.name)
        useSessionStore.getState().setActivity(data.name === 'web_search' ? 'Searching the web' : `Running ${data.name}`)
        break
      case 'tool_result':
        if (interruptRequested || !visible) break
        updateToolEvent(data.name, data.result, data.diff)
        break
      case 'plan_step':
        if (interruptRequested || !visible) break
        updatePlanStep({
          index: data.index,
          stepText: data.step_text,
          toolName: data.tool_name,
          status: data.status,
          result: data.result,
        })
        break
      case 'escalation':
      case 'plan_approval':
        // Kept with its chat: the card shows there, and a background chat
        // gets a "needs approval" badge until the user opens it.
        offerApproval(data)
        if (!visible) useSessionStore.getState().setAttention(eventSession, 'approval')
        // the engine is in plan-only now; show it while the card waits
        if (data.type === 'plan_approval') void syncSession()
        break
      case 'answer':
        if (!visible) {
          useSessionStore.getState().setAttention(eventSession, 'done')
          break
        }
        if (interruptRequested) break
        {
          const current = useSessionStore.getState().status
          if (current && data.usage) {
            setStatus({ ...current, last_usage: data.usage, rate_limit_status: data.rate_limit_status ?? current.rate_limit_status })
          }
        }
        addMessage({ role: 'assistant', content: scrubMarkup(data.text), ...turnTrace() })
        // Reset now, not at turn_complete: in between, the live block and the
        // finished bubble would both show the same thinking and tool rows.
        resetStreaming()
        break
      case 'interrupted':
        if (visible) stopLocally()
        break
      case 'error':
        console.error('Agent error:', data.message)
        if (!visible) {
          // Saved progress stays in that chat; flag it instead of showing the
          // error in the chat being viewed.
          useSessionStore.getState().setAttention(eventSession, 'failed')
          pauseQueueIfPending()
          break
        }
        setApprovalPending(null)
        setPlanApprovalPending(null)
        addMessage({ role: 'assistant', content: `Error: ${scrubMarkup(data.message)}`, ...turnTrace() })
        resetStreaming()
        pauseQueueIfPending()
        break
      case 'turn_complete':
        {
          // An approval cannot outlive the turn that asked for it.
          const ui = useUIStore.getState()
          if (ui.approvalPending?.sessionId === eventSession) setApprovalPending(null)
          if (ui.planApprovalPending?.sessionId === eventSession) setPlanApprovalPending(null)
          localTurnPending = false
          useSessionStore.getState().setRunningSession(null)
          const wasInterrupted = interruptRequested
          interruptRequested = false
          if (wasInterrupted) pauseQueueIfPending()
        }
        // There is one running turn across chats. A background completion must
        // also clear any working state left by a history refresh of this view.
        setStreaming(false)
        if (visible) {
          resetStreaming()
        }
        // Clear the finished turn before starting the queued one. Otherwise
        // that cleanup also clears the next turn's streaming/Stop state.
        drainPromptQueue()
        // A turn can change the permission mode itself (/plan enters plan-only,
        // an approved plan switches to the mode it runs in, a declined one stays
        // in plan-only), and the mode picker kept showing the old one.
        void syncSession()
        void useQueryClientHelper().invalidateQueries({ queryKey: ['sessions'] })
        break
      case 'session_saved':
        if (data.name && visible) setSessionName(data.name)
        void useQueryClientHelper().invalidateQueries({ queryKey: ['sessions'] })
        break
      case 'command_result':
        {
        const commandName = data.command.toLowerCase()
        const sessionOps = ['new', 'resume', 'reset', 'compact']
        if (!visible && !sessionOps.includes(commandName)) break
        if (data.command.toLowerCase() === 'raw') {
          // Parse the raw model call result (content, reasoning, tool_calls)
          let parsed: { content: string; reasoning?: string; tool_calls?: unknown } | null = null
          try {
            const obj = typeof data.structured === 'string' ? JSON.parse(data.structured) : data.structured
            if (obj && typeof obj === 'object') {
              parsed = {
                content: (obj as Record<string, unknown>).content as string ?? data.result,
                reasoning: (obj as Record<string, unknown>).reasoning as string | undefined,
                tool_calls: (obj as Record<string, unknown>).tool_calls,
              }
            }
          } catch {
            // structured isn't JSON — fall back to plain result text
          }
          if (!parsed) {
            parsed = { content: data.result }
          }
          useSessionStore.getState().setRawResult(parsed)
          useSessionStore.getState().setRawLoading(false)
          setRawPanelOpen(true)
        }
        if (data.result.toLowerCase().startsWith('unknown command')) {
          addMessage({ role: 'assistant', content: scrubMarkup(data.result), format: 'terminal' })
        }
        // Display command output for commands that produce user-visible text
        // (parity with CLI — every /command prints to console; the web UI
        // should show that output in the chat). Skip 'raw' (handled above
        // with structured parsing) and session ops (handled with notifications).
        const cmd = commandName
        if (sessionOps.includes(cmd)) {
          void syncSession(true).then(() => {
            void useQueryClientHelper().invalidateQueries({ queryKey: ['sessions'] })
            if (data.result.trim().length > 0) {
              addMessage({ role: 'assistant', content: scrubMarkup(data.result), format: 'terminal' })
            }
          })
        } else {
          void syncSession()
          void useQueryClientHelper().invalidateQueries({ queryKey: ['commands'] })
        }
        if (!sessionOps.includes(cmd) &&
            !data.result.toLowerCase().startsWith('unknown command') &&
            cmd !== 'raw' &&
            data.result.trim().length > 0) {
          addMessage({ role: 'assistant', content: scrubMarkup(data.result), format: 'terminal' })
        }
        break
        }
    }
  }

  ws.onopen = () => {
    reconnectAttempts = 0
    useSessionStore.getState().setConnection('open')
    useSessionStore.getState().setSendFailure('')
    // Anything typed while the socket was down goes now, in order.
    const pending = outbox
    outbox = []
    localTurnPending = pending.some((msg) => msg.type === 'chat' || msg.type === 'command')
    for (let index = 0; index < pending.length; index += 1) {
      try {
        ws.send(JSON.stringify(pending[index]))
      } catch {
        // Keep every unsent frame if the connection closes during replay.
        outbox.unshift(...pending.slice(index))
        localTurnPending = false
        const store = useSessionStore.getState()
        store.setStreaming(false)
        store.setRunningSession(null)
        store.setSendFailure('Connection interrupted — unsent messages are queued for reconnection.')
        ws.close()
        return
      }
    }
    // A reconnect may have missed events, so re-read authoritative state.
    void syncSession(true)
  }

  ws.onerror = () => {
    // onclose always follows, which is where the retry is scheduled.
    if (!disposed) useSessionStore.getState().setConnection('reconnecting')
  }

  ws.onclose = () => {
    localTurnPending = false
    // Reconnect only while the app wants a socket — no zombie loops after unmount.
    if (disposed) return
    reconnectAttempts += 1
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** (reconnectAttempts - 1), RECONNECT_MAX_MS)
    const store = useSessionStore.getState()
    store.setConnection('reconnecting')
    // A turn cannot still be streaming over a socket that just closed. Route
    // through the same cleanup as an explicit Stop, so a dropped connection
    // doesn't silently lose partial streamed text or leave stale streaming
    // state / a stuck queue behind (only setStreaming(false) used to run here).
    if (store.isStreaming) stopLocally()
    reconnectTimer = setTimeout(() => ensureConnection(), delay)
  }
}

function ensureConnection() {
  if (disposed || (sharedWs && sharedWs.readyState <= WebSocket.OPEN)) return
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${window.location.host}/ws`
  if (reconnectAttempts === 0) useSessionStore.getState().setConnection('connecting')
  let ws: WebSocket
  try {
    ws = new WebSocket(wsUrl)
  } catch {
    useSessionStore.getState().setConnection('failed')
    return
  }
  sharedWs = ws
  wireSocket(ws)
}

/** Show an approval request in the chat that owns it (see ApprovalCard). */
function offerApproval(frame: PendingApprovalFrame | Extract<WSEvent, { type: 'escalation' | 'plan_approval' }>) {
  const { setApprovalPending, setPlanApprovalPending } = useUIStore.getState()
  if (frame.type === 'escalation') {
    setApprovalPending({
      id: frame.id,
      toolName: frame.tool_name,
      changeType: frame.change_type,
      description: frame.description,
      sessionId: frame.session_id,
    })
  } else {
    setPlanApprovalPending({ id: frame.id, plan: frame.plan, sessionId: frame.session_id })
  }
}

/** Re-read the viewed chat from the server: name, id, the running task and,
 *  with includeHistory, its messages and any approval its task waits on.
 *  Also what the sidebar calls after switching chats. */
export async function syncSession(includeHistory = false) {
  try {
    const statusResponse = await fetch('/api/status')
    if (!statusResponse.ok) return
    const status = await statusResponse.json() as StatusInfo
    const store = useSessionStore.getState()
    store.setStatus(status)
    store.setSessionName(status.session_name || '')
    store.setSessionId(status.session_id || '')
    // A queued turn may not have reached the backend yet. Its completion frame
    // clears local state; a simultaneous idle status must not clear it early.
    if (status.running_session === null && !localTurnPending) store.setRunningSession(null)

    if (!includeHistory) return
    const historyResponse = await fetch('/api/history')
    if (!historyResponse.ok) return
    const history = await historyResponse.json() as {
      messages?: ChatMessage[]
      session_id?: string
      running?: boolean
      pending_approvals?: PendingApprovalFrame[]
      pending_plan_approvals?: PendingApprovalFrame[]
    }
    if (Array.isArray(history.messages)) {
      useSessionStore.getState().setMessages(history.messages)
    }
    if (history.session_id) useSessionStore.getState().setAttention(history.session_id, null)
    // Back in the chat whose task is still running: show it as working.
    if (history.running && history.session_id
        && useSessionStore.getState().runningSession?.id === history.session_id) {
      useSessionStore.getState().setActivity('Working')
      useSessionStore.getState().setStreaming(true)
    }
    for (const frame of [...(history.pending_approvals ?? []), ...(history.pending_plan_approvals ?? [])]) {
      offerApproval(frame)
    }
  } catch {
    // The WebSocket reconnect loop remains the source of truth if the API is unavailable.
  }
}

/** Send one frame, or hold it in the outbox until the socket is back. */
function sendFrame(msg: WSClientMessage): boolean {
  if ((msg.type === 'chat' || msg.type === 'command') && !msg.session_id) {
    // Capture the owner before a disconnected frame waits in the outbox.
    msg = { ...msg, session_id: useSessionStore.getState().sessionId || undefined }
  }
  if (msg.type === 'interrupt') {
    interruptRequested = true
    stopLocally()
  }
  if (msg.type === 'chat' || msg.type === 'command') {
    interruptRequested = false
    localTurnPending = true
    const store = useSessionStore.getState()
    const target = ((msg.type === 'chat' || msg.type === 'command') && msg.session_id) || store.sessionId
    store.setRunningSession({ id: target, name: target === store.sessionId ? store.sessionName : '' })
    // Only the chat being viewed shows the turn streaming.
    if (target === store.sessionId) {
      store.resetStreaming()
      store.setActivity('Working')
      store.setStreaming(true)
    }
  }
  if (sharedWs && sharedWs.readyState === WebSocket.OPEN) {
    try {
      sharedWs.send(JSON.stringify(msg))
      return true
    } catch {
      // The frame was not accepted. Queue it below and reconnect instead of
      // leaving an optimistic running turn that can never complete.
      sharedWs.close()
    }
  }
  // Queue it and say so, rather than returning silently - a dropped message
  // used to leave the composer spinning on a turn that was never sent.
  // `interrupt` is deliberately not queued: stopping a turn that no longer
  // exists is meaningless, and replaying it would cancel the next one.
  const store = useSessionStore.getState()
  if (msg.type === 'interrupt') {
    return false
  }
  outbox.push(msg)
  localTurnPending = false
  store.setStreaming(false)
  store.setRunningSession(null)
  store.setSendFailure(
    'Not connected to Agent8088 — this message is queued and will be sent '
    + 'as soon as the connection is back.',
  )
  ensureConnection()
  return false
}

/** Dispatch the head of the prompt queue. Called when a turn ends, and by
 *  Resume. One prompt per turn: the backend refuses concurrent turns. */
/** The task of a `/plan <task>` command, else ''. The server runs that task as
 *  a chat turn, so the composer shows it as a message the user sent. */
export function planTaskOf(text: string): string {
  const match = /^\/plan\s+(\S[\s\S]*)$/i.exec(text.trim())
  return match ? match[1].trim() : ''
}

export function drainPromptQueue() {
  const store = useSessionStore.getState()
  if (store.queuePaused) return
  const next = store.promptQueue[0]
  if (!next) return
  store.removeQueuedPrompt(next.id)
  if (next.kind === 'command') {
    const [command, ...rest] = next.text.replace(/^\//, '').split(' ')
    const target = next.sessionId || store.sessionId
    if (target === store.sessionId) {
      store.addMessage({ role: 'user', content: next.text })
    }
    sendFrame({ type: 'command', command, args: rest.join(' '), session_id: target || undefined })
    return
  }
  // The transcript echo happens here rather than at enqueue time, so a queued
  // prompt does not appear to have been asked before it actually runs. It runs
  // in the chat it was typed in, which may no longer be the one on screen.
  const target = next.sessionId || store.sessionId
  if (target === store.sessionId) {
    store.addMessage({ role: 'user', content: next.text, attachments: next.attachments })
  }
  sendFrame({ type: 'chat', text: next.text, attachments: next.attachmentIds, session_id: target || undefined })
  useSessionStore.getState().setRunningSession({
    id: target, name: target === store.sessionId ? store.sessionName : next.sessionName ?? '' })
}

/** A turn that stopped or failed holds the queue for the user to look at. */
function pauseQueueIfPending() {
  const store = useSessionStore.getState()
  if (store.promptQueue.length) store.setQueuePaused(true)
}

/** The turn's thinking and tool rows, to carry onto its finished message so
 *  they outlive resetStreaming. A tool still running when the turn ended never
 *  finished, so it is shown as failed rather than spinning forever. */
function turnTrace(): Pick<ChatMessage, 'reasoning' | 'tools'> {
  const { streamingReasoning, toolEvents } = useSessionStore.getState()
  return {
    reasoning: streamingReasoning.length ? streamingReasoning : undefined,
    tools: toolEvents.length
      ? toolEvents.map((t) => t.status === 'running' ? { ...t, status: 'failed' as const } : t)
      : undefined,
  }
}

function stopLocally() {
  const store = useSessionStore.getState()
  const partial = scrubMarkup(store.streamingText).trim()
  // A second call finds the state already reset, so nothing attaches twice.
  const trace = turnTrace()
  if (partial || trace.reasoning || trace.tools) {
    store.addMessage({ role: 'assistant', content: partial, ...trace })
  }
  store.setStreaming(false)
  store.resetStreaming()
  store.setActivity('Stopped')
  pauseQueueIfPending()
}

// Late-bound to avoid a circular import at module load.
let _queryClient: ReturnType<typeof useQueryClient> | null = null
function useQueryClientHelper() {
  return _queryClient ?? ({ invalidateQueries: async () => {} } as ReturnType<typeof useQueryClient>)
}

export function useWebSocket() {
  const queryClient = useQueryClient()
  _queryClient = queryClient

  const send = useCallback(sendFrame, [])

  useEffect(() => {
    consumers += 1
    disposed = false
    ensureConnection()
    // includeHistory: the backend still holds the transcript (/api/history) and
    // the session file is on disk, so a refresh must restore what the user can
    // see. Syncing without it is what made every reload look like a new chat.
    if (consumers === 1) void syncSession(true)
    return () => {
      consumers -= 1
      if (consumers <= 0) {
        consumers = 0
        disposed = true
        if (reconnectTimer) clearTimeout(reconnectTimer)
        reconnectAttempts = 0
        outbox = []
        sharedWs?.close()
        sharedWs = null
      }
    }
  }, [])

  return { send, ws: sharedWs }
}
