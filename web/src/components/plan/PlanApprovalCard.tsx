import { Check, X, FileText } from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { useWebSocket } from '@/hooks/useWebSocket'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export function PlanApprovalCard() {
  const { planApprovalPending, setPlanApprovalPending } = useUIStore()
  const { send } = useWebSocket()

  if (!planApprovalPending) return null

  const handleResponse = (mode: string) => {
    send({ type: 'plan_approval', mode, id: planApprovalPending.id })
    setPlanApprovalPending(null)
  }

  return (
    <div className="mx-4 my-2 rounded-xl border border-brand-border bg-zinc-900 p-4">
      <div className="mb-3 flex items-center gap-2">
        <FileText className="h-5 w-5 text-brand-cyan" />
        <h3 className="font-semibold text-zinc-100">Plan Proposal</h3>
      </div>
      <div className="mb-4 max-h-96 overflow-auto rounded-lg bg-zinc-950 p-3 text-sm text-zinc-300">
        <div className="prose prose-invert prose-sm max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {planApprovalPending.plan}
          </ReactMarkdown>
        </div>
      </div>
      <div className="flex gap-2">
        <button
          onClick={() => handleResponse('readonly')}
          className="flex items-center gap-1.5 rounded-lg bg-yellow-600/20 px-3 py-1.5 text-sm text-yellow-400 hover:bg-yellow-600/30"
        >
          <Check className="h-4 w-4" /> Run in readonly
        </button>
        <button
          onClick={() => handleResponse('full-auto')}
          className="flex items-center gap-1.5 rounded-lg bg-green-600/20 px-3 py-1.5 text-sm text-green-400 hover:bg-green-600/30"
        >
          <Check className="h-4 w-4" /> Run in full-auto
        </button>
        <button
          onClick={() => handleResponse('')}
          className="flex items-center gap-1.5 rounded-lg bg-red-600/20 px-3 py-1.5 text-sm text-red-400 hover:bg-red-600/30"
        >
          <X className="h-4 w-4" /> Decline
        </button>
      </div>
    </div>
  )
}