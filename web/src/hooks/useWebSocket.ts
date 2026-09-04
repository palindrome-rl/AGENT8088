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
let sessionClosed = false
let consumers = 0
let reconnectTimer: ReturnType<typeof setTimeout> | null = null

function wireSocket(ws: WebSocket) {
  const {
    setStreaming, appendStreamingText, appendStreamingReasoning,
    addToolEvent, updateToolEvent, updatePlanStep,
    resetStreaming, addMessage, setStatus, setSessionName,
  } = useSessionStore.getState()
  const { setApprovalPending, setPlanApprovalPending, setRawPanelOpen } = useUIStore.getState()

  ws.onmessage = (event) => {
    const data: WSEvent = JSON.parse(event.data)
    switch (data.type) {
      case 'status':
        setStatus(data.data)
        break
      case 'spin':
        setStreaming(true)
        break
      case 'token':
        if (data.kind === 'reasoning') {
          appendStreamingReasoning(data.delta)
        } else {
          appendStreamingText(data.delta)
        }
        break
      case 'tool_calls':
        break
      case 'tool_start':
        addToolEvent(data.name)
        break
      case 'tool_result':
        updateToolEvent(data.name, data.result)
        break
      case 'plan_step':
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
        addMessage({ role: 'assistant', content: scrubMarkup(data.text) })
        setStreaming(false)
        resetStreaming()
        break
      case 'interrupted':
        setStreaming(false)
        resetStreaming()
        break
      case 'error':
        console.error('Agent error:', data.message)
        addMessage({ role: 'assistant', content: `Error: ${scrubMarkup(data.message)}` })
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
          addMessage({ role: 'assistant', content: scrubMarkup(data.result) })
        }
        // Display command output for commands that produce user-visible text
        // (parity with CLI — every /command prints to console; the web UI
        // should show that output in the chat). Skip 'raw' (handled above
        // with structured parsing) and session ops (handled with notifications).
        const cmd = data.command.toLowerCase()
        const sessionOps = ['new', 'resume', 'reset', 'compact']
        if (['exit', 'quit'].includes(cmd)) sessionClosed = true
        if (sessionOps.includes(cmd)) {
          void syncSession(true).then(() => {
            void useQueryClientHelper().invalidateQueries({ queryKey: ['sessions'] })
            if (data.result.trim().length > 0) {
              addMessage({ role: 'assistant', content: scrubMarkup(data.result) })
            }
          })
        } else if (!['exit', 'quit'].includes(cmd)) {
          void syncSession()
          void useQueryClientHelper().invalidateQueries({ queryKey: ['commands'] })
        }
        if (!sessionOps.includes(cmd) &&
            !data.result.toLowerCase().startsWith('unknown command') &&
            cmd !== 'raw' &&
            data.result.trim().length > 0) {
          addMessage({ role: 'assistant', content: scrubMarkup(data.result) })
        }
        break
    }
  }

  ws.onclose = () => {
    // Reconnect only while the app wants a socket — no zombie loops after unmount.
    if (!disposed && !sessionClosed) {
      reconnectTimer = setTimeout(() => ensureConnection(), 2000)
    }
  }
}

function ensureConnection() {
  if (disposed || sessionClosed || (sharedWs && sharedWs.readyState <= WebSocket.OPEN)) return
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${window.location.host}/ws`
  const ws = new WebSocket(wsUrl)
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

// Late-bound to avoid a circular import at module load.
let _queryClient: ReturnType<typeof useQueryClient> | null = null
function useQueryClientHelper() {
  return _queryClient ?? ({ invalidateQueries: async () => {} } as ReturnType<typeof useQueryClient>)
}

export function useWebSocket() {
  const queryClient = useQueryClient()
  _queryClient = queryClient

  const send = useCallback((msg: WSClientMessage) => {
    if (!sharedWs || sharedWs.readyState !== WebSocket.OPEN) {
      ensureConnection()
      // Retry once the socket opens (or drop if it never connects).
      const onOpen = () => sharedWs?.send(JSON.stringify(msg))
      if (sharedWs && sharedWs.readyState === WebSocket.CONNECTING) {
        sharedWs.addEventListener('open', onOpen, { once: true })
      }
      return
    }
    sharedWs.send(JSON.stringify(msg))
  }, [])

  useEffect(() => {
    consumers += 1
    disposed = false
    ensureConnection()
    if (consumers === 1) void syncSession()
    return () => {
      consumers -= 1
      if (consumers <= 0) {
        consumers = 0
        disposed = true
        if (reconnectTimer) clearTimeout(reconnectTimer)
        sharedWs?.close()
        sharedWs = null
      }
    }
  }, [])

  return { send, ws: sharedWs }
}
