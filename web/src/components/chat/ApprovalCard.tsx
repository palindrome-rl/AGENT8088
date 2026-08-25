import { ShieldCheck, ShieldX, Clock } from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { useWebSocket } from '@/hooks/useWebSocket'

export function ApprovalCard() {
  const { approvalPending, setApprovalPending } = useUIStore()
  const { send } = useWebSocket()

  if (!approvalPending) return null

  const handleResponse = (approved: boolean, sessionScope: boolean) => {
    send({ type: 'approval', approved, session_scope: sessionScope })
    setApprovalPending(null)
  }

  return (
    <div className="mx-4 my-2 rounded-xl border border-brand-border bg-zinc-900 p-4">
      <div className="mb-3 flex items-center gap-2">
        <Clock className="h-5 w-5 text-brand-cyan" />
        <h3 className="font-semibold text-zinc-100">Approval Required</h3>
      </div>
      <div className="mb-3 text-sm text-zinc-400">
        <span className="font-mono text-brand-primary">{approvalPending.toolName}</span>
        {' — '}
        <span className="text-zinc-300">{approvalPending.changeType}</span>
      </div>
      <pre className="mb-4 max-h-32 overflow-auto rounded-lg bg-zinc-950 p-2 font-mono text-xs text-zinc-400">
        {approvalPending.description}
      </pre>
      <div className="flex gap-2">
        <button
          onClick={() => handleResponse(true, false)}
          className="flex items-center gap-1.5 rounded-lg bg-green-600/20 px-3 py-1.5 text-sm text-green-400 hover:bg-green-600/30"
        >
          <ShieldCheck className="h-4 w-4" /> Approve once
        </button>
        <button
          onClick={() => handleResponse(true, true)}
          className="flex items-center gap-1.5 rounded-lg bg-brand-primary/20 px-3 py-1.5 text-sm text-brand-cyan hover:bg-brand-primary/30"
        >
          <ShieldCheck className="h-4 w-4" /> Approve for session
        </button>
        <button
          onClick={() => handleResponse(false, false)}
          className="flex items-center gap-1.5 rounded-lg bg-red-600/20 px-3 py-1.5 text-sm text-red-400 hover:bg-red-600/30"
        >
          <ShieldX className="h-4 w-4" /> Deny
        </button>
      </div>
    </div>
  )
}