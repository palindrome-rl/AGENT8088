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
    <div className="mx-4 my-2 rounded-xl border border-brand-border/40 bg-zinc-900/50 p-3.5">
      <div className="mb-2.5 flex items-center gap-2">
        <Clock className="h-4 w-4 text-brand-cyan" />
        <h3 className="text-[13px] font-semibold text-zinc-100">Approval Required</h3>
      </div>
      <div className="mb-2.5 text-[13px] text-zinc-400">
        <span className="font-mono text-brand-primary">{approvalPending.toolName}</span>
        {' — '}
        <span className="text-zinc-300">{approvalPending.changeType}</span>
      </div>
      <pre className="mb-3 max-h-28 overflow-auto rounded-lg bg-zinc-950 p-2 font-mono text-[11px] text-zinc-400">
        {approvalPending.description}
      </pre>
      <div className="flex gap-2">
        <button
          onClick={() => handleResponse(true, false)}
          className="flex items-center gap-1.5 rounded-lg bg-green-600/15 px-3 py-1.5 text-[13px] text-green-400 transition-colors hover:bg-green-600/25"
        >
          <ShieldCheck className="h-3.5 w-3.5" /> Approve once
        </button>
        <button
          onClick={() => handleResponse(true, true)}
          className="flex items-center gap-1.5 rounded-lg bg-brand-primary/15 px-3 py-1.5 text-[13px] text-brand-cyan transition-colors hover:bg-brand-primary/25"
        >
          <ShieldCheck className="h-3.5 w-3.5" /> Approve for session
        </button>
        <button
          onClick={() => handleResponse(false, false)}
          className="flex items-center gap-1.5 rounded-lg bg-red-600/15 px-3 py-1.5 text-[13px] text-red-400 transition-colors hover:bg-red-600/25"
        >
          <ShieldX className="h-3.5 w-3.5" /> Deny
        </button>
      </div>
    </div>
  )
}