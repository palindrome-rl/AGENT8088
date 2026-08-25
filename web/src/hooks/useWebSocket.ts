import { useCallback, useEffect, useRef } from 'react'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import type { WSClientMessage, WSEvent } from '@/types/api'

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const {
    setStreaming, appendStreamingText, appendStreamingReasoning,
    addToolEvent, updateToolEvent, updatePlanStep,
    resetStreaming, addMessage, setStatus,
  } = useSessionStore()
  const { setApprovalPending, setPlanApprovalPending } = useUIStore()

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

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
          addMessage({ role: 'assistant', content: data.text })
          setStreaming(false)
          resetStreaming()
          break
        case 'interrupted':
          setStreaming(false)
          resetStreaming()
          break
        case 'error':
          console.error('Agent error:', data.message)
          setStreaming(false)
          resetStreaming()
          break
        case 'session_saved':
          break
      }
    }

    ws.onclose = () => {
      setTimeout(() => connect(), 2000)
    }

    return ws
  }, [setStreaming, appendStreamingText, appendStreamingReasoning,
      addToolEvent, updateToolEvent, updatePlanStep, resetStreaming,
      addMessage, setStatus, setApprovalPending, setPlanApprovalPending])

  const send = useCallback((msg: WSClientMessage) => {
    wsRef.current?.send(JSON.stringify(msg))
  }, [])

  useEffect(() => {
    const ws = connect()
    return () => ws?.close()
  }, [connect])

  return { send, ws: wsRef }
}