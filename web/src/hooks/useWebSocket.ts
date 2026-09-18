import { useCallback, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { scrubMarkup } from '@/lib/scrub'
import type { ChatMessage, StatusInfo, WSClientMessage, WSEvent } from '@/types/api'

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
    switch (data.type) {
      case 'status':
        setStatus(data.data)
        break
      case 'spin':
        if (interruptRequested) break
        setStreaming(true)
        useSessionStore.getState().setActivity(data.message === 'thinking...'
          ? (useSessionStore.getState().toolEvents.length ? 'Reviewing results' : 'Preparing request')
          : data.message)
        break
      case 'token':
        if (interruptRequested) break
        if (data.kind === 'reasoning') {
          appendStreamingReasoning(data.delta)
        } else {
          appendStreamingText(data.delta)
          if (scrubMarkup(useSessionStore.getState().streamingText).trim()) {
            useSessionStore.getState().setActivity('Writing answer')
          }
        }
        break
      case 'tool_calls':
        break
      case 'tool_start':
        if (interruptRequested) break
        addToolEvent(data.name)
        useSessionStore.getState().setActivity(data.name === 'web_search' ? 'Searching the web' : `Running ${data.name}`)
        break
      case 'tool_result':
        if (interruptRequested) break
        updateToolEvent(data.name, data.result, data.diff)
        break
      case 'plan_step':
        if (interruptRequested) break
        updatePlanStep({
          index: data.index,
          stepText: data.step_text,
          toolName: data.tool_name,
          status: data.status,
          result: data.result,
        })
        break
      case 'escalation':
        setApprovalPending({
          id: data.id,
          toolName: data.tool_name,
          changeType: data.change_type,
          description: data.description,
        })
        break
      case 'plan_approval':
        setPlanApprovalPending({ id: data.id, plan: data.plan })
        break
      case 'answer':
        if (interruptRequested) break
        {
          const current = useSessionStore.getState().status
          if (current && data.usage) {
            setStatus({ ...current, last_usage: data.usage, rate_limit_status: data.rate_limit_status ?? current.rate_limit_status })
          }
        }
        addMessage({ role: 'assistant', content: scrubMarkup(data.text) })
        break
      case 'interrupted':
        stopLocally()
        break
      case 'error':
        console.error('Agent error:', data.message)
        setApprovalPending(null)
        setPlanApprovalPending(null)
        addMessage({ role: 'assistant', content: `Error: ${scrubMarkup(data.message)}` })
        pauseQueueIfPending()
        break
      case 'turn_complete':
        {
          const wasInterrupted = interruptRequested
          interruptRequested = false
          if (wasInterrupted) pauseQueueIfPending()
          else drainPromptQueue()
        }
        setStreaming(false)
        resetStreaming()
        break
      case 'session_saved':
        if (data.name) setSessionName(data.name)
        void useQueryClientHelper().invalidateQueries({ queryKey: ['sessions'] })
        break
      case 'command_result':
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
        const cmd = data.command.toLowerCase()
        const sessionOps = ['new', 'resume', 'reset', 'compact']
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

  ws.onopen = () => {
    reconnectAttempts = 0
    useSessionStore.getState().setConnection('open')
    useSessionStore.getState().setSendFailure('')
    // Anything typed while the socket was down goes now, in order.
    const pending = outbox
    outbox = []
    pending.forEach((msg) => ws.send(JSON.stringify(msg)))
    // A reconnect may have missed events, so re-read authoritative state.
    void syncSession(true)
  }

  ws.onerror = () => {
    // onclose always follows, which is where the retry is scheduled.
    if (!disposed) useSessionStore.getState().setConnection('reconnecting')
  }

  ws.onclose = () => {
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

async function syncSession(includeHistory = false) {
  try {
    const statusResponse = await fetch('/api/status')
    if (!statusResponse.ok) return
    const status = await statusResponse.json() as StatusInfo
    useSessionStore.getState().setStatus(status)
    useSessionStore.getState().setSessionName(status.session_name || '')

    if (!includeHistory) return
    const historyResponse = await fetch('/api/history')
    if (!historyResponse.ok) return
    const history = await historyResponse.json() as { messages?: ChatMessage[] }
    if (Array.isArray(history.messages)) {
      useSessionStore.getState().setMessages(history.messages)
    }
  } catch {
    // The WebSocket reconnect loop remains the source of truth if the API is unavailable.
  }
}

/** Send one frame, or hold it in the outbox until the socket is back. */
function sendFrame(msg: WSClientMessage): boolean {
  if (msg.type === 'interrupt') {
    interruptRequested = true
    stopLocally()
  }
  if (msg.type === 'chat' || msg.type === 'command') {
    interruptRequested = false
    useSessionStore.getState().resetStreaming()
    useSessionStore.getState().setActivity('Working')
    useSessionStore.getState().setStreaming(true)
  }
  if (sharedWs && sharedWs.readyState === WebSocket.OPEN) {
    try {
      sharedWs.send(JSON.stringify(msg))
      return true
    } catch {
      return false
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
  store.setStreaming(false)
  store.setSendFailure(
    'Not connected to Agent8088 — this message is queued and will be sent '
    + 'as soon as the connection is back.',
  )
  ensureConnection()
  return false
}

/** Dispatch the head of the prompt queue. Called when a turn ends, and by
 *  Resume. One prompt per turn: the backend refuses concurrent turns. */
export function drainPromptQueue() {
  const store = useSessionStore.getState()
  if (store.queuePaused) return
  const next = store.promptQueue[0]
  if (!next) return
  store.removeQueuedPrompt(next.id)
  if (next.kind === 'command') {
    const [command, ...rest] = next.text.replace(/^\//, '').split(' ')
    sendFrame({ type: 'command', command, args: rest.join(' ') })
    return
  }
  // The transcript echo happens here rather than at enqueue time, so a queued
  // prompt does not appear to have been asked before it actually runs.
  store.addMessage({ role: 'user', content: next.text, attachments: next.attachments })
  sendFrame({ type: 'chat', text: next.text, attachments: next.attachmentIds })
}

/** A turn that stopped or failed holds the queue for the user to look at. */
function pauseQueueIfPending() {
  const store = useSessionStore.getState()
  if (store.promptQueue.length) store.setQueuePaused(true)
}

function stopLocally() {
  const store = useSessionStore.getState()
  const partial = scrubMarkup(store.streamingText).trim()
  if (partial) store.addMessage({ role: 'assistant', content: partial })
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
